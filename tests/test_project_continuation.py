"""中断恢复与结果报告回归；外部模型、文献均为软件测试夹具。"""
from collections import Counter
from types import SimpleNamespace

import pytest
from core.knowledge import ArtifactType, ExperimentStatus, KnowledgeStore
from core.knowledge.schema import Artifact
from core.llm import LLMRegistry
from core.orchestration.context import ContextKey
from core.orchestration.graph import Graph
from core.orchestration.node import HumanResponse, NodeResult, NodeStatus
from core.state.lifecycle import LifecycleStage
from core.workflow_checkpoint import KEY
from runtime.pipeline import Pipeline
from core.product_boundary import ExecutionPolicy
from stages.common import StageCheckpoint
from tests.test_orchestration_graph import _NoopAgent
from tests.test_workflow_reliability import setup_project, WorkflowModel, submit


@pytest.mark.parametrize("target", ["topic_confirm", "idea_discuss", "method_review", "experiment_review", "revise", "revise_changed"])
def test_restart_at_each_stage_then_finish_without_repeating_completed_work(setup_project, monkeypatch, target):
    api, config, _, store, _ = setup_project
    model = WorkflowModel()
    model.store = store
    monkeypatch.setattr(LLMRegistry, "from_config", classmethod(lambda cls, cfg: model))
    from stages.research.agents import PaperFetchAgent, PaperIngestAgent
    meta = {"title": "Federated learning comparison (offline test fixture)", "abstract": "软件测试夹具，不是真实科研证据。", "source": "sciverse_fixture", "year": 2026}
    monkeypatch.setattr(PaperFetchAgent, "_real_fetch", lambda *args: ([dict(meta)], []))
    monkeypatch.setattr(PaperIngestAgent, "_enrich_urls", staticmethod(lambda metas: None))
    approve = lambda request: HumanResponse(action="continue", text="ok")
    changed = target == "revise_changed"
    target = "revise" if changed else target

    def interrupt(request):
        if request.context.get("node_id") == target:
            raise RuntimeError("测试：进程在人工确认时中断")
        return approve(request)

    if target == "revise":
        first = Pipeline(config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1", "联邦学习离线恢复测试", human_callback=approve, domain="ai")
        assert first.status == "experiment_pending"
        submit(api, [{"name": "trial", "status": "completed", "metrics": {"accuracy": .61}, "claim_assessments": {c: "inconclusive" for c in model.claim_ids}}])
        with pytest.raises(RuntimeError, match="进程"):
            Pipeline(config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1", "", resume=True, human_callback=interrupt)
    else:
        with pytest.raises(RuntimeError, match="进程"):
            Pipeline(config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1", "联邦学习离线恢复测试", human_callback=interrupt, domain="ai")
    saved = store.get_kv(KEY)
    assert saved["history"][-1]["node_id"] == target
    key = lambda c: (c.get("task_type"), getattr(c.get("output_schema"), "__name__", "text"))
    before = Counter(key(c) for c in model.calls)
    node_count_before = len(saved["history"])
    if changed:
        submit(api, [{"name":"trial", "status":"completed", "metrics":{"accuracy":.61,"additional_measure":1}, "claim_assessments":{c:"inconclusive" for c in model.claim_ids}}])
    restored = Pipeline(config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1", "", resume=True, human_callback=approve)
    # Only new work may call the model; earlier generation tasks are not repeated.
    after = Counter(key(c) for c in model.calls)
    excluded = {"experiment_anomaly_analyze", "experiment_outcome_assess"}
    if changed:
        versions = store.get_kv("writing.recovered_draft_history")
        assert versions and versions[-1]["content"] == saved["data"]["writing.draft_content"]
        assert any(after[task] > before[task] for task in before if task[0] == "writing_section_draft")
    else:
        for task in [task for task in before if task[0] not in excluded]:
            assert after[task] == before[task], task
    if restored.status == "experiment_pending":
        submit(api, [{"name": "trial", "status": "completed", "metrics": {"accuracy": .61}, "claim_assessments": {c: "refutes" for c in model.claim_ids}}])
        restored = Pipeline(config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1", "", resume=True, human_callback=approve)
    assert restored.status == "completed", restored.summary
    assert len(store.list_experiments()) == 2  # seeded baseline + one generated task
    assert store.list_artifacts(ArtifactType.PAPER_DRAFT)
    assert len(store.get_kv(KEY)["history"]) > node_count_before


def test_failed_node_preserves_history_and_retries_only_failed_node(setup_project, monkeypatch):
    _, config, _, _, _ = setup_project
    calls = Counter()
    class Counted(_NoopAgent):
        def _execute(self, input_obj, ctx):
            calls[self.node_id] += 1
            if self.node_id == "later" and calls[self.node_id] == 1:
                return NodeResult(status=NodeStatus.FAILED, error="测试中的临时故障", summary="临时故障")
            ctx.set(ContextKey[str]("research.saved_material"), "已保存材料")
            return NodeResult(status=NodeStatus.SUCCESS, summary="测试成功")
    graph = Graph(name="failure_recovery", stage="research")
    for node in (Counted("earlier"), StageCheckpoint("checkpoint"), Counted("later")):
        graph.add_node(node)
    graph.add_edge("earlier", "checkpoint")
    graph.add_edge("checkpoint", "later")
    pipeline = Pipeline(config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY)
    monkeypatch.setattr(pipeline, "_build_stage_graph", lambda stage: graph)
    session, ctx = pipeline.start_project("p1", "故障恢复软件测试", "ai")
    first = pipeline.run_stage(session, ctx, LifecycleStage.RESEARCH)
    assert first.status == "failed" and first.node_history[-1]["node_id"] == "later"
    session, ctx = pipeline.resume_project("p1")
    assert ctx.get(ContextKey[str]("research.saved_material")) == "已保存材料"
    assert pipeline.run_stage(session, ctx, LifecycleStage.RESEARCH).status == "success"
    assert calls == {"earlier": 1, "later": 2}


@pytest.mark.parametrize("mode", ["pipeline", "topic_discovery"])
def test_resume_routes_by_saved_mode_and_repairs_orphaned_status(setup_project, monkeypatch, mode):
    api, _, state, store, _ = setup_project
    state.status = "pending_human"
    state.run_mode = ""  # emulate service restart / legacy state
    store.save_kv("web.run_mode", mode)
    calls = []
    monkeypatch.setattr(api, "run_project", lambda pid: calls.append(("pipeline", pid)) or {})
    monkeypatch.setattr(api, "run_topic_discovery", lambda pid, req: calls.append(("topic_discovery", pid)) or {})
    assert api.resume_project_task("p1")["action"] == "started"
    assert calls == [(mode, "p1")]
    assert state.status == "stopped"


def test_resume_running_project_does_not_start_duplicate_worker(setup_project, monkeypatch):
    api, _, state, _, _ = setup_project
    state.status = "running"
    state.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(api, "run_project", lambda *args: pytest.fail("不应重复启动"))
    assert api.resume_project_task("p1")["action"] == "running"


def test_completed_project_opens_outputs_and_flags_results_changed_after_final_save(setup_project, monkeypatch):
    api, _, state, _, _ = setup_project
    state.status = "completed"
    monkeypatch.setattr(api, "run_project", lambda *args: pytest.fail("已完成项目不能重跑"))
    assert api.resume_project_task("p1")["action"] == "materials"
    submit(api, [{"name":"baseline", "status":"completed", "metrics":{"accuracy":.61}}])
    assert "原稿件保留" in state.summary
    assert api.resume_project_task("p1")["action"] == "materials"
    assert state.thread is None


def test_waiting_results_then_review_action_changes_after_backfill(setup_project):
    api, _, state, store, ctx = setup_project
    state.status = "experiment_pending"
    assert api.resume_project_task("p1")["action"] == "view_only"
    submit(api, [{"name": "baseline", "status": "completed", "metrics": {"accuracy": .61}}])
    assert api.get_status("p1")["continuation"]["action"] == "view_only"
    assert store.get_experiment("e1").metrics=={"accuracy":.61}


def test_written_artifacts_are_counted_without_duplicate_versions(setup_project):
    api, _, _, store, _ = setup_project
    for version in (1, 2):
        store.save_artifact(Artifact(artifact_id=f"a{version}", artifact_group="draft", version=version, artifact_type=ArtifactType.PAPER_DRAFT, title="软件测试稿件", content="软件测试正文"))
    store.save_artifact(Artifact(artifact_id="empty", artifact_group="empty", version=1, artifact_type=ArtifactType.REVIEW_NOTE, title="空修改意见"))
    assert api.get_status("p1")["counts"]["writing_artifacts"] == 1


def test_resume_ignores_unrelated_old_planned_experiments(setup_project):
    api, _, state, store, _ = setup_project
    from core.knowledge.schema import Experiment
    store.save_experiment(Experiment(experiment_id="active", name="当前任务", status=ExperimentStatus.COMPLETED, metrics={"accuracy": .61}))
    store.save_kv(KEY, {"data": {"experiment.experiment_ids": ["active"]}})
    state.status = "experiment_pending"
    assert api.get_status("p1")["continuation"]["action"] == "view_only"
    assert store.get_experiment("active").metrics=={"accuracy":.61}


def test_topic_discovery_handoff_tracks_the_new_worker(setup_project, monkeypatch):
    api, _, state, _, _ = setup_project
    monkeypatch.setattr(api, "_PROJECTS", {"p1": state})
    class Worker:
        def __init__(self, **kwargs):
            self.started = False
        def start(self):
            self.started = True
        def is_alive(self):
            return self.started
    class Discovery:
        def __init__(self, **kwargs):
            pass
        def run_topic_discovery(self, **kwargs):
            return SimpleNamespace(status="completed", summary="测试选题完成", recommendation="", node_history=[], extra={"selected_topic": "联邦学习测试主题"})
    monkeypatch.setattr(api, "Pipeline", Discovery)
    monkeypatch.setattr(api.threading, "Thread", Worker)
    api._run_topic_discovery_thread("p1", "测试兴趣", False)
    assert state.thread.is_alive() and state.run_mode == "pipeline" and state.status == "running"
    assert api.resume_project_task("p1")["action"] == "running"


def test_discovery_resume_advances_past_saved_success_instead_of_regenerating(setup_project, monkeypatch):
    _, config, _, store, _ = setup_project
    from core.workflow_checkpoint import save_context
    from tests.test_orchestration_graph import _NoopHuman
    import stages.topic_discovery.graph as discovery
    graph = Graph(name="discovery_fixture", stage="topic_discovery")
    graph.add_node(_NoopAgent("generate"))
    graph.add_node(_NoopHuman("select", prompt="测试确认"))
    graph.add_edge("generate", "select")
    monkeypatch.setattr(discovery, "build_topic_discovery_graph", lambda: graph)
    pipeline = Pipeline(config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY)
    _, ctx = pipeline.start_project("p1", "测试兴趣", "ai")
    ctx.current_stage = "topic_discovery"
    ctx.current_node_id = "generate"
    ctx.record_node_result("generate", "test", "success", "测试已完成")
    save_context(ctx, store, False)
    result = pipeline.run_topic_discovery("p1", "测试兴趣", resume=True)
    assert result.status == "pending_human"
    assert [r["node_id"] for r in result.node_history] == ["generate", "select"]
