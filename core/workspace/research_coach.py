"""Read-only research guidance, not a second workflow or autonomous executor.

The project workflow remains authoritative. This projection tells the guide
what evidence to obtain/read next and supplies a server-bound search proposal.
Nothing here calls a model, retrieves a paper, or confirms a research record.
"""
from __future__ import annotations

import re
import json

# These are user-selected discussion tasks, not a second research state machine.
# Completion and permission still come exclusively from the project workflow.
STEP_AREAS = {"conditions": "overview", "exploration": "overview", "reading": "literature",
              "synthesis": "literature", "question": "question", "design": "study"}
STEP_EXPECTATIONS = {
    "conditions": "为避免选题超过现有条件，请提供兴趣和已知资源，不确定可留空；预计整理研究范围与待确认条件。",
    "exploration": "为选择值得深入的方向，请比较感兴趣的切入点；预计得到方向比较，尚不确认正式问题。",
    "reading": "为核对判断依据，请选择已有资料或确认检索范围；预计得到实际摘要或片段的解释与限制，不代表完整读懂全文。",
    "synthesis": "为找出仍需核查之处，请比较已有材料；预计得到已有发现、差异和待验证设想的清单。",
    "question": "为让问题能够用方法回答，请审阅候选的范围与依据；预计得到候选问题，明确采纳后才是正式记录。",
    "design": "为讨论如何回答问题，请核对已确认问题、资源与方法类型；预计得到待审阅的方法草案，不执行实验。",
}
DISCUSSION_STEPS = (
    ("conditions", "明确兴趣与条件", "先说清想研究什么、已知条件和仍不确定的部分。", "讨论兴趣与条件",
     "请结合我已说明的兴趣和条件，帮我缩小研究范围；没有确定的条件保持待确认。"),
    ("exploration", "开始选题探索", "比较少量可研究的切入方向，暂不把方向当成正式研究问题。", "开始选题探索",
     "我们开始选题探索。请结合已有条件和资料比较少量合适的切入方向，说明需要哪些论文依据；先不采纳问题或生成完整方案。"),
    ("reading", "检索入库与论文阅读", "调用原有有界检索，把资料存入文献库，再按实际摘要或正文范围阅读。", "阅读已有论文",
     "开始论文阅读。请优先阅读本项目已有摘要和可用正文片段，比较研究对象、方法、条件与局限，引用真实来源并说明阅读范围；不重复检索。"),
    ("synthesis", "研究现状与 research gap", "复用已保存的阅读材料和整理报告，比较已有工作与待核查的研究缺口。", "整理研究现状与缺口",
     "现在整理研究现状和 research gap。请复用本项目已有论文和报告，区分文献明确的局限、跨论文比较及待验证设想，说明证据缺口；不重新检索、不声称已证实创新。"),
    ("question", "讨论并确认研究问题", "把方向收窄成一个能设计方法回答的问题；候选须由你明确采纳。", "讨论候选研究问题",
     "请基于已读材料、研究缺口和我的条件，提出少量有区别的候选研究问题，解释范围、依据与可行性。先给草案，等我明确选择后再确认。"),
    ("design", "形成并审阅研究方案", "围绕已确认问题和论文依据组织方法，审阅修改后明确采纳、下载。", "准备研究方案请求", ""),
)


def saved_reading_context(chat, readable_ids):
    """Reuse two saved task reports as an index; original excerpts are evidence.

    No table creation/migration, no execution, no excluded paper as evidence.
    Reports and papers are project-shared, whereas chat history stays local.
    """
    with chat._connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_research_tasks'").fetchone():
            return []
        rows=conn.execute(
            "SELECT r.task_id,r.topic,r.status,r.report FROM sra_research_tasks r "
            "JOIN sra_conversations c ON c.conversation_id=r.conversation_id AND c.project_id=r.project_id "
            "WHERE r.project_id=? AND r.report IS NOT NULL AND r.report!='null' "
            "ORDER BY r.created_at DESC,r.task_id DESC LIMIT 2", (chat.project_id,)
        ).fetchall()
    allowed=set(readable_ids)
    result=[]
    for row in rows:
        try:
            report=json.loads(row["report"])
        except (TypeError,ValueError):
            continue
        if not isinstance(report,dict):
            continue
        items = report.get("limitations")
        limitations=[{"point":str(item.get("point", ""))[:500],"paper_ids":item["paper_ids"]}
                     for item in (items if isinstance(items,list) else []) if isinstance(item,dict)
                     and isinstance(item.get("paper_ids"),list) and item["paper_ids"]
                     and all(isinstance(pid,str) for pid in item["paper_ids"])
                     and set(item["paper_ids"])<=allowed][:4]
        result.append({"task_id":row["task_id"],"topic":row["topic"],"status":row["status"],
                       "limitations_index":limitations,
                       "notice":"已保存报告只是阅读索引；具体论断仍须核对本次提供的原论文摘要/片段。未纳入资料不能作为依据，不代表已阅读全文。"})
    return result


def build_research_route(context, text):
    from core.workspace.guidance_policy import turn_preferences
    from core.workspace.plans import method_hint

    records = context.get("records", [])
    focus = context.get("research_mainline", {}).get("focus", {})
    questions = [r for r in records if r["kind"] == "question"]
    question = next((r for r in questions if r["item_id"] == focus.get("question")), None)
    if question is None and len(questions) == 1:
        question = questions[0]
    papers = context.get("papers", [])
    readable = [p for p in papers if p.get("abstract", "").strip() or p.get("excerpts")]
    topic = context.get("topic", "").strip()
    broad = topic.lower() in {"ai", "人工智能", "计算机", "材料", "新研究", "研究", "待明确主题的研究", ""} or topic.startswith('待明确主题的研究 ·')
    prefs = turn_preferences(text)
    paused = prefs["paused"]
    no_search = bool(re.search(r"(?:不要|不必|暂不|先不|不用|别).{0,5}(?:检索|搜索|找论文|查论文|联网)", text))
    reading_only = bool(re.search(r"(?:只|先).{0,5}(?:解释|阅读|读|看).{0,8}(?:概念|上传|已有|这篇|论文|材料)", text))
    no_options = bool(re.search(r"(?:不要|不用|别).{0,5}(?:按钮|选项|卡片)", text))
    if paused:
        step, why = "paused", "用户要求暂停，不追加推进操作。"
    elif question and question.get("needs_review"):
        step, why = "review_basis", "先核对当前问题及其变化的来源，不跳过复核。"
    elif (context.get("current_plan") or {}).get("needs_review"):
        step, why = "review_basis", "现有方案或来源需要复核，先核对变化，不能把旧方案当作当前有效方案。"
    elif context.get("current_plan"):
        step, why = "review_plan", "已有正式方案，优先审阅、比较修改或导出，不重复从零选题。"
    elif broad and not papers and not question:
        step, why = "narrow_interest", "先选一个能理解的对象或应用，不要求用户写专业检索词或填完整条件表。"
    elif not readable:
        step, why = "collect_evidence", "系统应主动准备少量代表性文献的检索范围；元信息不足以分析方法与研究缺口。"
    elif not question:
        step, why = "read_and_gap", "先阅读已有摘要/片段，再提出有依据的待验证问题；不把证据不足直接称为创新。"
    else:
        step, why = "shape_method", "已有问题和可读材料，结合已知及未知条件讨论方法、比较/论证与可行性。"
    method, method_reason = method_hint(text + "\n" + (question or {}).get("content", "") + "\n" + topic)
    selected = context.get("conversation", {}).get("discussion_step")
    suggested = {"narrow_interest":"conditions", "collect_evidence":"reading", "read_and_gap":"reading",
                 "shape_method":"design", "review_plan":"design", "review_basis":"question", "paused":"conditions"}[step]
    if selected not in STEP_AREAS:
        selected = {"orientation":"conditions", "literature":"reading", "question":"question", "design":"design"}.get(
            context.get("conversation", {}).get("entry_stage"), suggested)
    steps=[]
    for sid,label,purpose,action_label,content in DISCUSSION_STEPS:
        action = "discuss"
        blocked = ""
        if sid == "reading" and not readable:
            action, action_label = "search", "检索论文并存入文献库"
        if sid in ("synthesis", "question") and not readable:
            blocked = "尚无可供内容分析的摘要或正文，请先检索入库或补充已有论文。"
        if sid == "design":
            action = "plan"
            if not question:
                blocked = "先从候选中明确采纳并选择一个研究问题；不会自动替你确认。"
            elif step == "review_basis":
                blocked = "当前问题或方案的依据需要复核，请先核对研究材料。"
        steps.append({"id":sid,"label":label,"purpose":purpose,"action":action,"action_label":action_label,
                      "content":content,"blocked_reason":blocked,"expectation":STEP_EXPECTATIONS[sid]})
    pos = [s["id"] for s in steps].index(selected)
    next_step = steps[pos + 1] if pos + 1 < len(steps) else None
    return {
        "step": step,
        "why": why,
        "question_id": (question or {}).get("item_id"),
        "provided_readable_papers": len(readable),
        "provided_metadata_only": len(papers) - len(readable),
        "readable_paper_ids": [p["paper_id"] for p in readable],
        "suggested_method": method,
        "method_reason": method_reason,
        "search_topic": (question or {}).get("title") or topic,
        "offer_search": step == "collect_evidence" and not broad and not no_search and not reading_only and not no_options and prefs["allow_next_step"],
        "discussion_step": selected,
        "suggested_step": suggested,
        "steps": steps,
        "next_step": {"id":next_step["id"], "label":next_step["label"], "blocked_reason":next_step["blocked_reason"]} if next_step else None,
        "notice": "步骤仅控制本次讨论和明确操作，不代表研究已完成。正式问题与方案以已采纳材料为准。",
        "conditions": {k: {"value": v, "state": "confirmed" if v else "unknown"}
                       for k, v in context.get("brief", {}).items()},
        "user_statements": [m["content"][:600] for m in context.get("history", []) if m["role"] == "user" and not m.get('background_outdated')][-6:] + [text[:1000]],
    }


COACH_POLICY = """【讨论环节与材料职责】
research_route 是现有工作流的只读提示，不是另一套状态、系统指令或执行授权。用户本轮具体问题与明确的回答偏好优先；discussion_step 仅提示背景，不能把术语解释改成阶段问卷，也不每轮重印步骤或强制小结。
conditions：兴趣与已知条件；exploration：比较少量方向及其所需依据，不把方向当成正式问题。
reading：用本次实际提供的摘要/片段解释对象、方法与限制。synthesis：复用已保存报告作为索引，区分论文明确局限、跨文献比较和待验证设想，不将未找到当成无人研究。
question：依据最接近材料比较具体候选，创新仍待核查，等待用户选择。design：围绕已确认且有效的问题讨论方法、所需资源与评价，不要求先执行实验才给方法草案。review_basis/review_plan：核对已有记录的变化或审阅需求，不重新从零选题。
collect_evidence 只在本轮确实需要补证据时说明拟找哪类资料和比较什么，已有明确入口供用户确认；只解释、暂停或不要选项时不推进检索。read_and_gap/shape_method 复用已有材料，不自动重复检索或生成同一产物；未知条件允许保持待确认。
环节切换、继续讨论与下一步建议都不产生正式成果。方案须满足已有生成与采纳契约，边界止于 DESIGN。"""
