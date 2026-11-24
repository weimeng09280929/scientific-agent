"""Single read model for the research mainline; executor state is a subtask.

No LLM, training, lifecycle advance, result invention, or implicit adoption.
Frontend pages, guide and resume use this same server-derived contract.
"""
from __future__ import annotations

from core.experiment_results import has_real_metrics


def analyze(workspace, papers) -> dict:
    items = [i.model_dump(mode="json") for i in workspace.items]
    by_id = {i["item_id"]: i for i in items}
    active = [i for i in items if i["status"] == "active"]
    known = {p.paper_id for p in papers}
    def usable(item, seen=frozenset()):
        if not item or item["needs_review"] or item["status"] != "active" or item["item_id"] in seen or not set(item["paper_ids"]) <= known:
            return False
        if item["kind"] == "plan" and len({ref for ref in item["related_ids"] if by_id.get(ref, {}).get("kind") == "question"}) != 1:
            return False
        return all(usable(by_id.get(ref), seen | {item["item_id"]}) for ref in item["related_ids"])
    def select(options, selected):
        return next((i for i in options if i["item_id"] == selected), None) if selected else options[0] if len(options) == 1 else None
    questions = [i for i in active if i["kind"] == "question"]
    question = select(questions, workspace.focus.question)
    plans = [i for i in active if i["kind"] == "plan" and question and question["item_id"] in i["related_ids"]]
    plan = select(plans, workspace.focus.plan)
    results = [i for i in active if i["kind"] == "result" and plan and plan["item_id"] in i["related_ids"]]
    valid = [i for i in results if usable(i) and (i["result_status"] in ("failed", "inconclusive")
             or i["result_status"] == "completed" and has_real_metrics(i["metrics"]))]
    ids = {i["item_id"] for i in valid}
    measured = {i["item_id"] for i in valid if i["result_status"] == "completed"}
    findings = [i for i in active if i["kind"] == "finding" and usable(i) and set(i["related_ids"]) & ids
                and (i["assessment"] == "inconclusive" or i["assessment"] in ("supports", "refutes") and set(i["related_ids"]) & measured)]
    issues = []
    if question and not question["paper_ids"]:
        issues.append("当前问题尚未关联论文，研究依据仍需补充；这不阻止先保存待查证的想法。")
    paper_ids = list(dict.fromkeys(p for i in [question, plan, *valid, *findings] if i for p in i["paper_ids"]))
    if set(paper_ids) - known:
        issues.append("主线引用的部分论文不在当前文献库中，需要核对来源。")
    if len(results) != len(valid):
        issues.append("部分结果需要复核或缺少有效实测指标，未作为可用结果计入。")
    steps = [
        {"kind":"question", "label":"研究问题", "count":int(bool(question)), "state":"已保存问题" if usable(question) else "问题待复核" if question else "尚未选择问题"},
        {"kind":"plan", "label":"方法方案", "count":int(bool(plan)), "state":"已关联方案" if usable(plan) else "方案待复核" if plan else "请选择当前方案" if len(plans)>1 else "尚无关联方案"},
        {"kind":"result", "label":"实际结果", "count":len(valid), "state":"已有实测指标" if measured else "记录了运行失败" if any(i["result_status"]=="failed" for i in valid) else "结果尚无法定论" if valid else "等待结果记录"},
        {"kind":"finding", "label":"结果判断", "count":len(findings), "state":"有多条判断，请核对" if len(findings)>1 else "用户判断："+{"supports":"支持","refutes":"反驳","inconclusive":"无法定论"}[findings[0]["assessment"]] if findings else "尚未记录判断"},
    ]
    if not question:
        next_step = {"action":"select", "text":"请选择本次推进的研究问题，不混用不同问题的方案与结果。"} if questions else {"action":"create","kind":"question","text":"确认一个具体研究问题；可以直接关联已有思路或研究缺口，不必重复录入。"}
    elif not usable(question):
        next_step = {"action":"revise","item":question,"text":"问题或其来源已变化，先复核并修订问题，再核对下游方案和结果。"}
    elif not plan:
        next_step = {"action":"select","text":"当前问题有多个方案，请选择这次推进的方案。"} if plans else {"action":"create","kind":"plan","relatedId":question["item_id"],"text":"确认当前问题的方案；可以直接关联已有方法或实验任务，不必重新生成。"}
    elif not usable(plan):
        next_step = {"action":"revise","item":plan,"text":"方案或其依据已变化，先复核并修订，旧版本保留。"}
    else:
        next_step = {"action":"plan_ready","item":plan,"text":"方案已整理，可审阅、修改或导出并与导师讨论；本轮止于研究方案，不进入实验或后续写作。"}
    return {"questions":questions,"question":question,"plans":plans,"plan":plan,"results":results,"validResults":valid,
            "findings":findings,"steps":steps[:2],"issues":issues,"next":next_step,"paperIds":paper_ids,
            "planReady":bool(usable(question) and usable(plan)),"historicalSteps":steps[2:],
            "recordsReady":bool(question and plan and usable(question) and usable(plan) and valid and findings)}


def build_workflow(workspace, papers, catalog, execution=None) -> dict:
    flow = analyze(workspace, papers)
    usable_records = [i for i in workspace.items if i.status=="active" and not i.needs_review]
    counts = {kind:sum(i.kind==kind for i in usable_records) for kind in ("note","question","plan","result","finding","outline")}
    next_step = flow["next"]
    flow["steps"] = flow["steps"][:2]
    execution = execution or {}
    info = {"action":"record","page":"gaps","label":"明确研究问题","hint":next_step["text"],
            "record_kind":next_step.get("kind"),"related_id":next_step.get("relatedId"),"source":"research"}
    if next_step["action"]=="select":
        info.update(action="select",page="experiments" if flow["question"] else "gaps",label="选择当前方案" if flow["question"] else "选择研究问题")
    elif next_step["action"]=="revise":
        info.update(action="review_record",page="gaps" if next_step["item"]["kind"]=="question" else "experiments",label="复核当前记录",record_id=next_step["item"]["item_id"])
    elif next_step.get("kind")=="plan":
        info.update(page="experiments",label="完善方法方案")
    elif next_step["action"]=="plan_ready":
        info.update(action="plan_ready",page="experiments",label="审阅、修改与导出方案",record_kind="plan",record_id=flow["plan"]["item_id"])
    has_questions = bool(flow["questions"])
    # Active workers/real human gates remain authoritative. A dormant executor
    # cannot restart earlier stages over a confirmed research mainline.
    if execution.get("action") in ("running","confirm","view_only") or execution.get("restore_gate") or not has_questions:
        info = {**execution,"source":"execution"} if execution else info
    candidates = []
    for source in catalog:
        linked = next((i for i in workspace.items if i.status=="active" and i.source_ref and i.source_ref.kind==source["kind"] and i.source_ref.source_id==source["source_id"]),None)
        candidates.append({**source,"linked_item_id":linked.item_id if linked else None,
                           "linked_needs_review":bool(linked and linked.needs_review),
                           "needs_update":bool(linked and linked.source_ref.fingerprint!=source["fingerprint"])})
    # Stop calling a populated legacy project 'empty'. Continue reads existing
    # materials first instead of rerunning completed research without a cursor.
    if not has_questions and execution.get("action") in (None,"resume","complete") and not execution.get("restore_gate") and any(s["kind"] in ("idea","gap") and s["available"] for s in catalog):
        info.update(action="materials",page="gaps",label="从已有材料确认问题",hint="已有思路或研究缺口可直接关联为当前问题；不会重新检索或丢弃原材料。",source="research")
    methods = [s for s in catalog if s["kind"] in ("method","experiment_plan")]
    has_methods = any(s["available"] for s in methods)
    report = len(papers)>0
    stage = "question" if not flow["question"] or (next_step.get("item",{}).get("kind")=="question" and next_step["action"]=="revise") else "design"
    if not has_questions and not catalog and not report:
        stage="orientation"
    stages = [
        {"id":"literature","label":"文献与现状","page":"research-report","state":"available" if report else "pending","detail":f"{len(papers)} 篇入库论文，需核对原文"},
        {"id":"question","label":"研究问题","page":"gaps","state":"confirmed" if flow["steps"][0]["state"]=="已保存问题" else "review" if flow["question"] else "available" if any(s["kind"] in ("idea","gap") for s in catalog) else "pending","detail":flow["question"]["title"] if flow["question"] else "已有候选材料待确认" if catalog else "待明确"},
        {"id":"design","label":"方法方案","page":"experiments","state":"confirmed" if flow["planReady"] else "review" if flow["plan"] else "available" if has_methods else "pending","detail":flow["plan"]["title"] if flow["plan"] else "已有方法或任务待关联" if has_methods else "待制定"},
        {"id":"experiment","label":"外部验证","page":"experiments","state":"recorded" if flow["validResults"] else "waiting" if flow["plan"] else "pending","detail":f"{len(flow['validResults'])} 条可用结果记录；不自动执行实验"},
        {"id":"writing","label":"成果整理","page":"writing","state":"review" if flow["recordsReady"] else "available" if counts["outline"] else "pending","detail":"有判断，待核对局限与写作" if flow["recordsReady"] else "可先整理骨架，不代表验证完成"},
    ]
    stages = [s for s in stages if s["id"] not in ("experiment", "writing")]
    boundary = execution.get("product_boundary") or {"max_stage":"design","restricted":False,"reason":"plan_ready" if flow["planReady"] else "review_required" if next_step["action"]=="revise" else "within_product"}
    return {"version":1,"revision":workspace.revision,"focus":workspace.focus.model_dump(),"flow":flow,"counts":counts,"continuation":info,
            "execution":execution,"current_stage":stage,"product_stage":stage,"product_boundary":boundary,"stages":stages,"materials":candidates,
            "evidence_notice":"保存记录、后台任务结束、生成稿件，均不等于科学结论已验证。"}
