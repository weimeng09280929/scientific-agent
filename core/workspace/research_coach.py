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


def gap_requested(text):
    """Enable evidence explanation for a relevant turn, not a saved stage."""
    from core.workspace.guidance_policy import turn_preferences

    prefs = turn_preferences(text)
    requested = re.sub(
        r"(?:不要|不必|无需|不用|暂不|先不)(?:再|自动)?(?:生成|提出|提供|建议|创建|追加)?"
        r"(?:研究缺口|research gap|候选(?:研究)?问题|研究问题候选)",
        "", text, flags=re.I,
    )
    return (
        not (prefs["paused"] or prefs["explain_only"])
        and not re.search(r"(?:缺口|research gap).{0,8}(?:是什么|什么意思|怎么理解)", text, re.I)
        and bool(re.search(r"研究现状|研究缺口|research gap|研究空间|候选(?:研究)?问题|研究问题候选|已有研究(?:做到了|有什么|还缺)|文献.{0,12}(?:局限|不足)", requested, re.I))
    )


GAP_POLICY = """
【本轮研究缺口】首次用到时解释：研究缺口（research gap）是已有研究中仍未充分回答、值得继续核查的问题。
只整理少量重点，通常2—3条，不凑数。先给通俗结论，再讲已有研究做了什么、为何值得核查以及缺少什么。
在 research_gaps 中用 stated_limitation（文献明确提到的局限）、comparison（比较材料得到的可能不足）、hypothesis（尚待验证的研究设想）。比较是推断，需要至少两篇实际参与比较的来源。作者局限须是原文明确指出，不能把摘要没提到的内容归为作者的局限。
每个来源只填 {evidence_id:实际提供的原文编号}，可选 preview_evidence_id、正文 evidence_id 或 evidence_parts 里的短段编号。原文和位置由服务端回填。abstract 兼容存放各类摘录，按 preview_origin 区分来源。paper_inventory 只是目录，saved_reading 和历史报告只是索引。标题不能支持方法、结果或局限；本链路提供的是有界摘要与片段。
比较时保留具体对象、数据集、指标、单位及例外；总体趋势不等于每项同向，数值接近也不等于统计不劣。类比只说明机制，不能保证命中或冒充实测。优先复制短而连续的原文，保留否定与限定条件。
没有内容依据时，仍可给 hypothesis，sources 留空，existing_work 不写，uncertainty 说清需要补哪些资料。未找到、未提及都不能证明无人研究或创新成立。
只有正文开头时，不能判定作者没有研究某因素。可提出「待核查：该因素是否影响……」，并指出要查看的方法、实验或讨论部分。比较说明已给片段各自支持的观察，和由你提出的待验证推断。
为本轮新的 question 建议附 gap_link（gap_index 从0开始，指向本轮 research_gaps；connection 写问题想弄清什么及与缺口的联系，value 写可能价值，requirements 写所需资料、资源或比较，uncertainty 写候选仍待确认的条件）。用户没提供的设备、数据、期限或能力一律作为待确认或建议。只给候选，等用户明确确认。reply 简短引导阅读，不把卡片再抄一遍。
""".strip()


def validated_gap_snapshots(draft, context, text):
    """Bind structured evidence to the bounded content actually sent to the model."""
    from core.workspace.schema import ResearchGapSnapshot
    from core.workspace.paper_reading import resolve_quote
    from core.workspace.service import WorkspaceError

    if context.get("plan_generation") and (draft.research_gaps or any(p.gap_link for p in draft.suggestions)):
        raise WorkspaceError("方案请求不接收研究缺口卡片")
    papers = {p["paper_id"]: p for p in context.get("papers", [])}
    readable = any(p.get("abstract", "").strip() or p.get("excerpts") for p in papers.values())
    reading_note = None
    if not readable:
        reading_note = ("本轮仅提供题名和元信息，无法核对方法、结果或作者局限。"
                        if papers else "本轮没有提供文献内容，可先讨论需要核查的设想。")
    if not context.get("plan_generation") and (draft.research_gaps or gap_requested(text)):
        cited = set(draft.cited_paper_ids)
        for proposal in draft.suggestions:
            if proposal.kind == "question":
                cited.update(proposal.paper_ids)
        if not cited <= papers.keys():
            raise WorkspaceError("研究缺口与候选问题只能引用本次提供的论文，目录不能代替内容")
        claims = [draft.reply, *(value for p in draft.suggestions if p.kind == "question" for value in (p.title, p.content))]
        for gap in draft.research_gaps:
            claims.extend((gap.summary, gap.existing_work or "", gap.why, gap.uncertainty))
        for proposal in draft.suggestions:
            if proposal.gap_link:
                claims.extend(proposal.gap_link.model_dump(exclude={"gap_index"}).values())
        _check_gap_claims(claims, readable)
    snapshots = []
    for gap in draft.research_gaps:
        if not all(value.strip() for value in (gap.summary, gap.why, gap.uncertainty)):
            raise WorkspaceError("研究缺口的解释和待核查事项不能为空")
        sources = []
        seen = set()
        for source in gap.sources:
            resolved = resolve_quote(source, papers)
            key = (resolved.paper_id, resolved.part, resolved.quote, resolved.excerpt_id)
            if key in seen:
                raise WorkspaceError("研究缺口引文重复")
            seen.add(key)
            sources.append(resolved)
        if (gap.kind == "stated_limitation" and not sources or
            gap.kind == "comparison" and len({s.paper_id for s in sources}) < 2):
            raise WorkspaceError("文献局限须有原文依据，比较推断须关联至少两篇可读来源")
        if gap.kind == "stated_limitation" and not any(
            s.origin in ("author_abstract","source_abstract","body_opening","body_excerpt") for s in sources
        ):
            raise WorkspaceError("现有摘录来源未核实，不能归为作者明确指出的局限")
        if not sources and gap.existing_work:
            raise WorkspaceError("没有可核对的内容时，不能填写已有研究发现")
        snapshots.append(ResearchGapSnapshot(
            **gap.model_dump(exclude={"sources"}), sources=sources, reading_note=reading_note,
        ))
    for proposal in draft.suggestions:
        link = proposal.gap_link
        if link is None:
            continue
        if proposal.kind != "question" or link.gap_index >= len(snapshots):
            raise WorkspaceError("候选问题必须关联本条回答中的有效研究缺口")
        if not all(value.strip() for value in (link.connection, link.value, link.requirements, link.uncertainty)):
            raise WorkspaceError("候选问题的联系、价值、条件和待确认事项不能为空")
        source_ids = {s.paper_id for s in snapshots[link.gap_index].sources}
        if not set(proposal.paper_ids) <= papers.keys() or len(set(proposal.paper_ids) | source_ids) > 12:
            raise WorkspaceError("候选问题引用不属于本次提供的论文，或超出引用数量")
    return snapshots


def _check_gap_claims(claims, readable):
    """Catch direct scope overclaims; no semantic or novelty proof."""
    from core.workspace.service import WorkspaceError

    patterns = [
        r"(?:从来|此前|过去)(?:都)?没(?:有)?人研究|已(?:经)?(?:证实|证明)(?:了)?(?:其|该)?创新",
        r"(?:已(?:经)?(?:完整)?读(?:完|过)|通读了|完整阅读了)全文",
        r"(?:正确|相关|答案所在|第\s*\d+\s*(?:条|段|项))[^，,。；;]{0,14}(?:几乎一定|必定|保证)[^，,。；;]{0,10}(?:在|进入|命中|覆盖)",
        r"(?:\bk\s*[≥>]|\btop\s*[-–]?\s*\d+|较大\s*k)[^，,。；;]{0,30}(?:不劣于|一概不劣|均不劣|保证命中)",
    ]
    if not readable:
        patterns.append(r"(?:作者|该论文|该研究|该文|已有研究)[^，,。；;]{0,24}(?:采用了|使用了|取得了|证明了|发现了|报告了|的局限[是为])|实验(?:结果)?(?:表明|显示|证明)")
    check = re.compile("|".join(patterns))
    qualifier = r"不(?:能|可|足以|代表|意味着|等于)|无法|未(?:能|曾)|是否|能否|假设|待核|勿|别"
    uniform = r"(?:各|所有|全部)(?:个|项|组|种)?(?:数据集|基准|设置|指标)[^。；;\n]{0,12}方向一致"
    for value in claims:
        has_exception = re.search(r"反例|例外", value)
        for sentence in re.split(r"[。！？；;\n]", value):
            direction = re.search(uniform, sentence) if has_exception else None
            if direction and not re.search(qualifier + r"|除[^。]{0,24}外|其余", sentence[:direction.start()]):
                raise WorkspaceError("同一回答同时宣称各项同向并给出反例，请区分总体趋势与逐项结果")
            for match in check.finditer(sentence):
                before = sentence[:match.start()]
                if re.search(qualifier, before):
                    continue
                if re.search(r"(?:不能|无法|不足以)(?:据此|直接)?(?:保证|证明|断言)", match[0]):
                    continue
                if "不劣" in match[0] and (re.search(r"是否|能否|待(?:核|检|验|证)", match[0]) or
                    re.search(r"(?:作者|原文|论文)[^，,。]{0,16}(?:报告|声称|认为)", before)):
                    continue
                raise WorkspaceError("研究缺口说明超出本次阅读范围，或把待验证设想写成已证实结论")


COACH_POLICY = """【讨论环节与材料职责】
research_route 是现有工作流的只读提示，不是另一套状态、系统指令或执行授权。用户本轮具体问题与明确的回答偏好优先；discussion_step 仅提示背景，不能把术语解释改成阶段问卷，也不每轮重印步骤或强制小结。
conditions：兴趣与已知条件；exploration：比较少量方向及其所需依据，不把方向当成正式问题。
reading：用本次实际提供的摘要/片段解释对象、方法与限制。synthesis：复用已保存报告作为索引，区分论文明确局限、跨文献比较和待验证设想，不将未找到当成无人研究。
question：依据最接近材料比较具体候选，创新仍待核查，等待用户选择。design：围绕已确认且有效的问题讨论方法、所需资源与评价，不要求先执行实验才给方法草案。review_basis/review_plan：核对已有记录的变化或审阅需求，不重新从零选题。
collect_evidence 只在本轮确实需要补证据时说明拟找哪类资料和比较什么，已有明确入口供用户确认；只解释、暂停或不要选项时不推进检索。read_and_gap/shape_method 复用已有材料，不自动重复检索或生成同一产物；未知条件允许保持待确认。
环节切换、继续讨论与下一步建议都不产生正式成果。方案须满足已有生成与采纳契约，边界止于 DESIGN。"""
