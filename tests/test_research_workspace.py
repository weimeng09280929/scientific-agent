"""新框架的行为测试：持久化、人工采纳、依赖修订与真实结果边界。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.config import ProjectPaths
from core.knowledge.schema import Paper
from core.workspace.guide import build_guide_context, build_guide_system
from core.workspace.schema import Brief, GuideDraft, ItemInput, SuggestedItem
from core.workspace.service import RevisionConflict, WorkspaceError, WorkspaceService
from web.workspace_api import build_workspace_router


@pytest.fixture
def workspace(tmp_path):
    service = WorkspaceService("test-project", tmp_path / "knowledge.db")
    service.store.save_paper(Paper(paper_id="paper-1", title="Federated learning — test fixture", abstract="用于软件测试的摘要，不作为科研证据。"))
    return service


def add(service, kind, title, **kwargs):
    doc = service.add_item(ItemInput(kind=kind, title=title, content=f"{title}：用户提供的测试内容。", **kwargs), service.load().revision)
    return doc.items[-1]


def chain(service, verdict="supports"):
    question = add(service, "question", "非 IID 下的裁剪与隐私问题", paper_ids=["paper-1"])
    plan = add(service, "plan", "同等训练预算的试跑方案", related_ids=[question.item_id])
    result = add(service, "result", "实际运行记录", related_ids=[plan.item_id], result_status="completed", metrics={"accuracy": 0.71, "attack_auc": 0.56})
    finding = add(service, "finding", "用户对结果的判断", related_ids=[result.item_id], assessment=verdict)
    outline = add(service, "outline", "含局限与负面结果的提纲", related_ids=[finding.item_id])
    return question, plan, result, finding, outline


def test_end_to_end_records_survive_restart_and_do_not_verify_legacy_claims(workspace):
    workspace.save_brief(Brief(goal="完成联邦学习学期项目", resources="个人电脑", time_budget="每周八小时"), 0)
    add(workspace, "note", "阅读六问笔记", paper_ids=["paper-1"])
    items = chain(workspace)
    restored = WorkspaceService(workspace.project_id, workspace.db_path)
    view = restored.view()
    assert all(view["counts"][kind] == 1 for kind in ("note", "question", "plan", "result", "finding", "outline"))
    assert restored.load().brief.time_budget == "每周八小时"
    assert restored.store.list_claims() == []
    assert items[2].result_status == "completed" and items[3].origin == "user"
    exported = restored.export_markdown()
    assert "未经系统独立验证" in exported and items[2].item_id in exported


def test_negative_findings_can_be_used_for_writing(workspace):
    *_, finding, outline = chain(workspace, verdict="refutes")
    assert finding.assessment == "refutes"
    assert outline.status == "active"


def test_missing_sources_and_dependencies_rejected_without_partial_save(workspace):
    with pytest.raises(WorkspaceError, match="论文不在"):
        add(workspace, "question", "不存在的证据", paper_ids=["invented-paper"])
    with pytest.raises(WorkspaceError, match="关联一个"):
        add(workspace, "plan", "孤立的方案")
    assert workspace.load().revision == 0
    assert workspace.load().items == []


def test_result_requires_known_run_status_and_real_metrics(workspace):
    question = add(workspace, "question", "问题")
    plan = add(workspace, "plan", "方案", related_ids=[question.item_id])
    with pytest.raises(WorkspaceError, match="实际指标"):
        add(workspace, "result", "空指标", related_ids=[plan.item_id], result_status="completed")
    with pytest.raises(ValidationError):
        ItemInput(kind="result", title="状态", content="内容", result_status="success")
    for invalid in (float("nan"), float("inf"), True, "0.8"):
        with pytest.raises(ValidationError):
            ItemInput(kind="result", title="非有限值", content="内容", metrics={"loss": invalid})


def test_failed_execution_is_not_evidence_of_refutation(workspace):
    q = add(workspace, "question", "问题")
    plan = add(workspace, "plan", "方案", related_ids=[q.item_id])
    result = add(workspace, "result", "显存不足", related_ids=[plan.item_id], result_status="failed")
    for assessment in ("supports", "refutes"):
        with pytest.raises(WorkspaceError, match="运行失败"):
            add(workspace, "finding", "不能做科研判断", related_ids=[result.item_id], assessment=assessment)
    finding = add(workspace, "finding", "暂时无法判断", related_ids=[result.item_id], assessment="inconclusive")
    assert finding.assessment == "inconclusive"


def test_revising_question_preserves_versions_and_marks_all_dependents(workspace):
    question, *dependents = chain(workspace)
    replacement = add(workspace, "question", "收窄后的问题", supersedes_id=question.item_id)
    doc = workspace.load()
    assert next(i for i in doc.items if i.item_id == question.item_id).status == "superseded"
    assert all(next(i for i in doc.items if i.item_id == old.item_id).needs_review for old in dependents)
    assert not replacement.needs_review
    assert workspace.view()["counts"]["plan"] == 0
    with pytest.raises(WorkspaceError, match="重新核对"):
        add(workspace, "result", "旧方案不能续用", related_ids=[dependents[0].item_id], result_status="completed", metrics={"x": 1})


def test_revision_cannot_depend_on_its_old_descendants(workspace):
    question, plan, *_ = chain(workspace)
    with pytest.raises(WorkspaceError, match="下游"):
        add(workspace, "question", "形成循环的问题", related_ids=[plan.item_id], supersedes_id=question.item_id)


def test_optimistic_revision_prevents_lost_updates(workspace):
    other = WorkspaceService(workspace.project_id, workspace.db_path)
    workspace.save_brief(Brief(goal="第一个窗口"), 0)
    with pytest.raises(RevisionConflict):
        other.save_brief(Brief(goal="旧窗口"), 0)
    assert other.load().brief.goal == "第一个窗口"


def test_changed_project_constraints_require_rechecking_existing_work(workspace):
    chain(workspace)
    doc = workspace.save_brief(Brief(goal="改为两周内完成", resources="仅 CPU"), workspace.load().revision)
    assert all(i.needs_review for i in doc.items)
    assert workspace.view()["needs_review_count"] == 5


def test_chat_is_persisted_but_suggestion_needs_user_adoption(workspace):
    draft = GuideDraft(reply="建议先记录研究问题。", cited_paper_ids=["paper-1"], suggestions=[
        SuggestedItem(kind="question", title="问题草案", content="尚未核实创新性", paper_ids=["paper-1"]),
    ])
    doc = workspace.record_guide("question", "帮我整理问题", draft, "model", 0)
    assert len(doc.messages) == 2 and not doc.items
    other = WorkspaceService(workspace.project_id, workspace.db_path)
    doc = other.apply_suggestion(doc.suggestions[0].suggestion_id, doc.revision)
    assert doc.items[0].origin == "accepted_suggestion"
    again=other.apply_suggestion(doc.suggestions[0].suggestion_id,doc.revision)
    assert len(again.items)==1 and again.items[0].item_id==doc.items[0].item_id
    assert again.revision==doc.revision


def test_stale_suggestion_cannot_overwrite_new_research(workspace):
    doc = workspace.record_guide("overview", "想法", GuideDraft(reply="记录", suggestions=[SuggestedItem(title="笔记", content="内容")]), "rules", 0)
    suggestion_id = doc.suggestions[0].suggestion_id
    add(workspace, "note", "后续新想法")
    with pytest.raises(RevisionConflict, match="已有变化"):
        workspace.apply_suggestion(suggestion_id, workspace.load().revision)


def test_unknown_citation_rolls_back_whole_turn(workspace):
    with pytest.raises(WorkspaceError, match="不存在"):
        workspace.record_guide("literature", "问题", GuideDraft(reply="答复", cited_paper_ids=["fake"]), "model", 0)
    assert workspace.load().messages == [] and workspace.load().revision == 0


def test_guidance_rules_and_context_are_task_specific(workspace):
    reading = build_guide_system("literature", "ai")
    innovation = build_guide_system("question", "ai")
    writing = build_guide_system("writing", "ai")
    assert "六问" in reading and "论文事实" in reading
    assert "最接近的已有工作" in innovation and "不能决定 novelty" in innovation
    assert "负面结果" in writing and "最终结论" in writing
    federated = build_guide_system("overview", "ai", topic="联邦学习隐私")
    assert "差分隐私是保护性质" in federated
    assert "完整篇" not in reading or "不代表" in reading
    context = build_guide_context(workspace, "联邦学习", "literature", None, ["paper-1"])
    assert context["papers"][0]["scope"] == "摘要或元信息"
    with pytest.raises(WorkspaceError):
        build_guide_context(workspace, "主题", "literature", None, ["other-project-paper"])


def test_guidance_distinguishes_library_from_context_and_receives_actual_metrics(workspace):
    from core.knowledge import Experiment, ExperimentStatus
    for index in range(8):
        workspace.store.save_paper(Paper(paper_id=f"extra-{index}",title=f"FedAvg median software fixture {index}"))
    workspace.store.save_experiment(Experiment(experiment_id="e1",name="measured",status=ExperimentStatus.COMPLETED,metrics={"accuracy":0.7}))
    context=build_guide_context(workspace,"FedAvg median","study",None,[])
    assert context["paper_scope"]["total_in_project"] == 9
    assert context["paper_scope"]["abstracts_provided"] == 6
    assert len(context["paper_inventory"]) == 9
    assert context["existing_system"]["experiments"][0]["metrics"] == {"accuracy":0.7}


def client_for(tmp_path, dry_run=True, registry_factory=None):
    project = SimpleNamespace(topic="联邦学习", domain="ai")
    config = SimpleNamespace(paths=ProjectPaths.from_root(tmp_path), dry_run=dry_run)
    def require_project(project_id):
        if project_id != "p1":
            raise HTTPException(status_code=404, detail="项目不存在")
        return project
    app = FastAPI()
    app.include_router(build_workspace_router(config, require_project, registry_factory))
    return TestClient(app), WorkspaceService("p1", config.paths.project_db("p1"))


def test_api_rules_mode_does_not_call_model_and_requires_explicit_apply(tmp_path):
    def forbidden_registry():
        raise AssertionError("规则模式不能调用模型")
    client, service = client_for(tmp_path, registry_factory=forbidden_registry)
    assert client.get("/api/projects/p1/workspace").json()["guide_mode"] == "rules"
    response = client.post("/api/projects/p1/workspace/guide", json={"expected_revision": 0, "area": "question", "message": "联邦学习隐私方向如何开始"})
    assert response.status_code == 200
    data = response.json()["workspace"]
    assert data["messages"][-1]["mode"] == "rules" and data["items"] == []
    # Step 4B: ordinary discussion no longer manufactures a note every turn.
    # Explicit saving still produces a draft and requires atomic user adoption.
    assert data["suggestions"] == []
    response = client.post("/api/projects/p1/workspace/guide", json={"expected_revision": data["revision"], "area": "question", "message": "请保存为笔记：先梳理联邦学习的隐私威胁"})
    assert response.status_code == 200
    data = response.json()["workspace"]
    assert data["items"] == [] and len(data["suggestions"]) == 1
    assert client.post(f'/api/projects/p1/workspace/suggestions/{data["suggestions"][0]["suggestion_id"]}/apply', json={"expected_revision": data["revision"]}).status_code == 200
    assert client.get("/api/projects/p1/workspace/export?format=md").status_code == 200
    assert service.load().items[0].origin == "accepted_suggestion"
    # Read/guide routes no longer repair missing historical identity records.
    assert service.store.get_kv("research.topic") is None
    assert client.get("/api/projects/other/workspace").status_code == 404


def test_api_model_failure_is_not_replaced_by_fake_guidance(tmp_path):
    class BrokenRegistry:
        def structured_output(self, **kwargs):
            raise RuntimeError("测试中的模型故障")
    client, service = client_for(tmp_path, dry_run=False, registry_factory=BrokenRegistry)
    response = client.post("/api/projects/p1/workspace/guide", json={"expected_revision": 0, "area": "overview", "message": "帮我判断"})
    assert response.status_code == 502
    assert len(service.load().messages)==1 and service.load().messages[0].role=='user'
    assert service.load().messages[0].request_status=='failed'
    assert service.load().items==[] and service.load().revision==0


def test_api_model_turn_receives_actual_project_context(tmp_path):
    calls = []
    class Registry:
        def structured_output(self, **kwargs):
            calls.append(kwargs)
            return GuideDraft(reply="先确认设备与可用时间，再缩小问题。")
    client, service = client_for(tmp_path, dry_run=False, registry_factory=Registry)
    response = client.post("/api/projects/p1/workspace/guide", json={"expected_revision": 0, "area": "overview", "message": "先做什么"})
    assert response.status_code == 200
    assert "联邦学习" in calls[0]["prompt"] and "已有材料" in calls[0]["system"]
    assert service.load().messages[-1].mode == "model"
    conflict = client.put("/api/projects/p1/workspace/brief", json={"expected_revision": 0, "brief": {"goal": "旧版本提交"}})
    # Chat no longer changes the research revision. The first formal write is valid.
    assert conflict.status_code == 200
    stale=client.put('/api/projects/p1/workspace/brief',json={'expected_revision':0,'brief':{'goal':'stale formal write'}})
    assert stale.status_code==409
