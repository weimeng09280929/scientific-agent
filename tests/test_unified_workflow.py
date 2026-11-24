"""Unified research mainline. Offline fixtures, no training or model calls."""
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.artifacts import ArtifactContentStore, ArtifactManager
from core.config import GlobalConfig, ProjectPaths
from core.knowledge import ArtifactType, ExperimentStatus
from core.knowledge.schema import Experiment, Idea, Paper
from core.workspace.guide import build_guide_context
from core.workspace.schema import ItemInput, ResearchFocus
from core.workspace.service import WorkspaceService, WorkspaceError, RevisionConflict


@pytest.fixture
def unified(tmp_path,monkeypatch):
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD","1")
    from web import api
    from web.workspace_api import build_workspace_router
    config=GlobalConfig(paths=ProjectPaths.from_root(tmp_path),dry_run=True)
    monkeypatch.setattr(api,"_CONFIG",config)
    monkeypatch.setattr(api,"_PROJECTS",{})
    monkeypatch.setattr(api,"_METHOD_REGEN_JOBS",{})
    app=FastAPI()
    for path,endpoint,methods in [
        ("/api/projects",api.create_project,["POST"]),
        ("/api/projects/{project_id}/status",api.get_status,["GET"]),
        ("/api/projects/{project_id}/dashboard",api.get_dashboard,["GET"]),
        ("/api/projects/{project_id}/resume",api.resume_project_task,["POST"]),
        ("/api/projects/{project_id}/run",api.run_project,["POST"]),
        ("/api/projects/{project_id}/experiments/backfill",api.backfill_experiment_results,["POST"]),
        ("/api/projects/{project_id}/download/{artifact_type}",api.download_artifact,["GET"]),
    ]:
        app.add_api_route(path,endpoint,methods=methods)
    app.include_router(build_workspace_router(config,api._require_project,workflow_provider=lambda state:api._workspace_workflow(state,include_materials=True)))
    client=TestClient(app)
    pid=client.post("/api/projects",json={"topic":"离线流程夹具：联邦学习对照","domain":"ai"}).json()["project_id"]
    service=WorkspaceService(pid,config.paths.project_db(pid))
    service.store.save_paper(Paper(paper_id="paper-a",title="Offline fixture paper",abstract="软件测试，非科研证据"))
    service.store.save_idea(Idea(idea_id="idea-a",text="测试问题A：在相同条件下比较方法",source_paper_ids=["paper-a"],status="validated"))
    service.store.save_experiment(Experiment(experiment_id="exp-a",name="测试任务A",config={"purpose":"测试对照","pass_criterion":"如实记录差异"}))
    service.store.save_experiment(Experiment(experiment_id="exp-b",name="测试任务B",config={"purpose":"另一个方案"}))
    manager=ArtifactManager(service.store,ArtifactContentStore(config.paths.project_artifacts(pid)))
    manager.create_artifact(ArtifactType.METHOD_DOC,title="测试方法文档",content="软件测试方法说明，尚未执行。")
    return api,client,service,manager


def item(service,kind,title,**extra):
    return service.add_item(ItemInput(kind=kind,title=title,content="软件测试夹具，不是真实科研结果。",**extra),service.load().revision).items[-1]


def link(service,kind,source_id,related=None):
    source=next(s for s in service.catalog() if s["kind"]==kind and s["source_id"]==source_id)
    doc=service.link_material(kind,source_id,source["fingerprint"],related,service.load().revision)
    return next(i for i in reversed(doc.items) if i.source_ref and i.source_ref.kind==kind and i.source_ref.source_id==source_id)


def branch(service):
    q=link(service,"idea","idea-a")
    p=link(service,"experiment_plan","exp-a",q.item_id)
    return q,p


def test_workspace_with_real_workflow_provider_does_not_migrate_on_read(unified):
    import sqlite3
    from contextlib import closing
    api,client,s,_=unified
    branch(s)
    with closing(sqlite3.connect(s.db_path)) as conn, conn:
        conn.execute("DROP TABLE evidence_log")
    before=s.db_path.read_bytes()
    for path in ("workspace", "workspace?include_chat=false", "workspace/export?format=json"):
        response=client.get(f"/api/projects/{s.project_id}/{path}")
        assert response.status_code==200,response.text
        assert s.db_path.read_bytes()==before
    workspace=client.get(f"/api/projects/{s.project_id}/workspace").json()
    assert workspace["workflow"]["flow"]["planReady"]
    assert workspace["workflow"]["product_boundary"]["max_stage"]=="design"


def test_reads_offer_existing_sources_without_adoption_or_experiment_writes(unified):
    api,client,s,_=unified
    before=[e.model_dump() for e in s.store.list_experiments()]
    data=client.get(f"/api/projects/{s.project_id}/workspace").json()
    assert data["workspace"]["items"]==[]
    assert len(data["workflow"]["materials"])==4
    assert data["workflow"]["continuation"]["action"]=="materials"
    assert [e.model_dump() for e in s.store.list_experiments()]==before
    assert s.load().revision==0


def test_explicit_link_is_idempotent_and_selection_survives_service_restart(unified):
    _,_,s,_=unified
    q,p=branch(s)
    again=link(s,"experiment_plan","exp-a",q.item_id)
    assert again.item_id==p.item_id
    restored=WorkspaceService(s.project_id,s.db_path)
    assert restored.load().focus==ResearchFocus(question=q.item_id,plan=p.item_id)
    assert len(restored.load().items)==2
    assert s.store.get_experiment("exp-a").status==ExperimentStatus.PLANNED
    assert s.workflow()["flow"]["next"]["action"]=="plan_ready"
    assert s.workflow()["flow"]["planReady"] is True


def test_link_requires_confirmation_and_current_source(unified):
    _,client,s,_=unified
    source=s.catalog()[0]
    body={"expected_revision":0,"kind":source["kind"],"source_id":source["source_id"],"fingerprint":source["fingerprint"]}
    assert client.post(f"/api/projects/{s.project_id}/workspace/link-material",json=body).status_code==422
    response=client.post(f"/api/projects/{s.project_id}/workspace/link-material",json={**body,"confirmed":True,"fingerprint":"0"*64})
    assert response.status_code==409
    assert s.load().items==[]


def test_primary_resume_opens_plan_without_starting_worker(unified):
    api,client,s,_=unified
    q,p=branch(s)
    response=client.post(f"/api/projects/{s.project_id}/resume").json()
    assert response["action"]=="plan_ready" and response["record_id"]==p.item_id
    assert response["record_kind"]=="plan"
    assert api._PROJECTS[s.project_id].thread is None


def test_workspace_dashboard_status_share_one_focus_and_action(unified):
    _,client,s,_=unified
    branch(s)
    responses=[client.get(f"/api/projects/{s.project_id}/{suffix}").json() for suffix in ("workspace","status","dashboard")]
    for key in ("focus","flow","counts","continuation","stages"):
        assert responses[0]["workflow"][key]==responses[1]["workflow"][key]==responses[2]["workflow"][key]


def test_result_for_linked_task_updates_compatibility_row_in_same_transaction(unified):
    _,_,s,_=unified
    q,p=branch(s)
    r=item(s,"result","测试结果",related_ids=[p.item_id],result_status="completed",metrics={"accuracy":0.71})
    assert s.store.get_experiment("exp-a").metrics=={"accuracy":0.71}
    assert s.store.get_experiment("exp-a").status==ExperimentStatus.COMPLETED
    assert r.source_ref.kind=="experiment_result"
    assert not s.load().items[-1].needs_review
    assert s.store.list_claims()==[]
    with pytest.raises(WorkspaceError,match="修订原结果"):
        item(s,"result","重复覆盖",related_ids=[p.item_id],result_status="completed",metrics={"accuracy":0.99})
    assert s.store.get_experiment("exp-a").metrics=={"accuracy":0.71}
    assert len(s.load().items)==3


def test_result_revision_retains_history_and_synchronizes_new_value(unified):
    _,_,s,_=unified
    _,p=branch(s)
    r=item(s,"result","初次记录",related_ids=[p.item_id],result_status="completed",metrics={"accuracy":0.71})
    f=item(s,"finding","暂无法定论",related_ids=[r.item_id],assessment="inconclusive")
    replacement=item(s,"result","修正记录",related_ids=[p.item_id],result_status="completed",metrics={"accuracy":0.61},supersedes_id=r.item_id)
    assert s.store.get_experiment("exp-a").metrics=={"accuracy":0.61}
    by_id={i.item_id:i for i in s.load().items}
    assert by_id[r.item_id].status=="superseded" and by_id[f.item_id].needs_review
    assert not by_id[replacement.item_id].needs_review


def test_external_backfill_invalidates_historical_dependents_but_public_write_is_closed(unified):
    api,client,s,_=unified
    _,p=branch(s)
    r=item(s,"result","用户记录",related_ids=[p.item_id],result_status="completed",metrics={"accuracy":0.71})
    f=item(s,"finding","判断",related_ids=[r.item_id],assessment="inconclusive")
    o=item(s,"outline","提纲",related_ids=[f.item_id])
    response=client.post(f"/api/projects/{s.project_id}/experiments/backfill",json={"results_json":json.dumps({"experiments":[{"experiment_id":"exp-a","status":"completed","metrics":{"accuracy":0.65}}]})})
    assert response.status_code==422
    assert s.store.get_experiment("exp-a").metrics=={"accuracy":0.71}
    # Simulate a historical external update through the internal validation path.
    api._backfill_experiment_results_internal(s.project_id,api.ExperimentBackfillRequest(results_json=json.dumps({"experiments":[{"experiment_id":"exp-a","status":"completed","metrics":{"accuracy":0.65}}]})))
    by_id={i.item_id:i for i in s.load().items}
    assert all(by_id[i.item_id].needs_review for i in (r,f,o))
    assert s.workflow()["continuation"]["action"]=="plan_ready"
    updated=link(s,"experiment_result","exp-a",p.item_id)
    assert updated.metrics=={"accuracy":0.65} and updated.supersedes_id==r.item_id
    assert s.workflow()["flow"]["next"]["action"]=="plan_ready"


def test_cannot_borrow_results_from_another_task_or_select_other_question_plan(unified):
    _,_,s,_=unified
    q,p=branch(s)
    exp=s.store.get_experiment("exp-b");exp.status=ExperimentStatus.FAILED;s.store.save_experiment(exp)
    with pytest.raises(WorkspaceError,match="同一实验"):
        link(s,"experiment_result","exp-b",p.item_id)
    q2=item(s,"question","另一个问题")
    with pytest.raises(WorkspaceError,match="不属于"):
        s.save_focus(ResearchFocus(question=q2.item_id,plan=p.item_id),s.load().revision)


def test_new_method_version_invalidates_source_link_and_dependents(unified):
    _,_,s,manager=unified
    q=link(s,"idea","idea-a")
    source=next(a for a in s.catalog() if a["kind"]=="method")
    p=link(s,"method",source["source_id"],q.item_id)
    r=item(s,"result","试验记录",related_ids=[p.item_id],result_status="failed")
    manager.new_version(source["source_id"],content="修改后的测试方法说明")
    by_id={i.item_id:i for i in s.load().items}
    assert by_id[p.item_id].needs_review and by_id[r.item_id].needs_review
    assert s.workflow()["continuation"]["action"]=="review_record"


def test_guide_and_export_use_same_persisted_mainline(unified):
    _,client,s,_=unified
    q,p=branch(s)
    context=build_guide_context(s,"测试主题","overview",None,[])
    assert context["research_mainline"]["focus"]==s.load().focus.model_dump()
    assert context["research_mainline"]["next_step"]["action"]=="plan_ready"
    body=client.get(f"/api/projects/{s.project_id}/download/full-report").text
    assert q.item_id in body and p.item_id in body and "来源材料" in body


def test_actual_worker_and_human_gate_override_manual_record_action(unified):
    api,_,s,_=unified
    branch(s)
    state=api._PROJECTS[s.project_id]
    state.thread=SimpleNamespace(is_alive=lambda:True)
    assert api._continuation(state)["action"]=="running"
    api._BRIDGE._pending[s.project_id]={"context":{"node_id":"method_review"}}
    try:
        assert api._continuation(state)["action"]=="confirm"
    finally:
        api._BRIDGE._pending.pop(s.project_id,None)


def test_expected_outline_does_not_advance_experiment_or_scientific_completion(unified):
    _,_,s,_=unified
    _,p=branch(s)
    item(s,"outline","预期示例（非实测）",related_ids=[p.item_id])
    assert s.workflow()["current_stage"]=="design"
    assert s.workflow()["counts"]["result"]==0
    assert s.workflow()["continuation"]["action"]=="plan_ready"


def test_real_mode_forced_writing_is_rejected_before_start(unified):
    api,client,s,_=unified
    api._CONFIG.dry_run=False
    response=client.post(f"/api/projects/{s.project_id}/run",json={"force_writing":True})
    assert response.status_code==422 and api._PROJECTS[s.project_id].thread is None


def test_pipeline_cannot_bypass_real_result_gate_even_without_web(unified):
    from runtime.pipeline import Pipeline
    api,_,s,_=unified
    api._CONFIG.dry_run=False
    with pytest.raises(ValueError,match="force_writing"):
        Pipeline(api._CONFIG).run_pipeline(s.project_id,"测试",force_writing=True)
    assert s.store.list_experiments()[0].status==ExperimentStatus.PLANNED


def test_current_branch_rejects_multi_problem_plan_and_cross_plan_finding(unified):
    _,_,s,_=unified
    q,p=branch(s)
    q2=item(s,"question","另一个问题")
    with pytest.raises(WorkspaceError,match="一份方案只能"):
        item(s,"plan","混用两个问题",related_ids=[q.item_id,q2.item_id])
    p2=item(s,"plan","独立方案",related_ids=[q2.item_id])
    r=item(s,"result","结果A",related_ids=[p.item_id],result_status="failed")
    r2=item(s,"result","结果B",related_ids=[p2.item_id],result_status="failed")
    with pytest.raises(WorkspaceError,match="不同方案"):
        item(s,"finding","不能混用",related_ids=[r.item_id,r2.item_id],assessment="inconclusive")


def test_source_adoption_cannot_duplicate_same_source_on_another_branch(unified):
    _,_,s,_=unified
    _,p=branch(s)
    q2=item(s,"question","问题B")
    with pytest.raises(WorkspaceError,match="另一主线"):
        link(s,"experiment_plan","exp-a",q2.item_id)
    assert s.workflow()["counts"]["plan"]==1
