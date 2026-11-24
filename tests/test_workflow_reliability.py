"""流程级回归，所有指标/模型输出均为临时目录中的软件测试夹具。"""
from __future__ import annotations

import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from core.artifacts import ArtifactContentStore, ArtifactManager, ProvenanceValidator
from core.config import GlobalConfig, ProjectPaths
from core.knowledge import Claim, ClaimStatus, Experiment, ExperimentStatus, KnowledgeStore, Paper
from core.llm import LLMRegistry
from core.orchestration.context import ContextKey, ExecutionContext
from core.orchestration.graph import Graph, GraphRunner
from core.orchestration.node import HumanResponse, NodeStatus
from core.state.lifecycle import LifecycleStage
from core.workflow_checkpoint import KEY, restore_context, save_context
from runtime.pipeline import Pipeline
from core.product_boundary import ExecutionPolicy
from stages.common import (
    ARTIFACT_MANAGER, DESIGN_CLAIM_IDS, DESIGN_METHOD_CONTENT, DRY_RUN, EXPERIMENT_IDS,
    EXPERIMENT_OUTCOME, KNOWLEDGE_STORE, LLM_REGISTRY, PROJECT_DIR, PROJECT_DOMAIN,
    PROVENANCE_VALIDATOR, RESEARCH_TOPIC, WRITING_DRAFT_CONTENT, WRITING_OUTLINE,
)
from stages.design.agents import MethodFormalizeAgent, MethodReviewHuman
from stages.experiment.agents import AnomalyCheckAgent, ClaimVerifyAgent, ExperimentOutcomeAssessAgent
from stages.experiment.io_schema import AnomalyCheckInput, ClaimVerifyInput, ExperimentOutcomeAssessInput
from stages.writing.agents import ReviseHuman, SectionDraftAgent


@pytest.fixture
def setup_project(tmp_path, monkeypatch):
    from web import api
    config = GlobalConfig(paths=ProjectPaths.from_root(tmp_path), dry_run=False)
    state = api.ProjectState(project_id="p1", topic="软件测试：联邦学习", created_at="2026-10-16")
    monkeypatch.setattr(api, "_CONFIG", config)
    monkeypatch.setattr(api, "_require_project", lambda pid: state)
    store = KnowledgeStore(config.paths.project_db("p1"))
    store.save_paper(Paper(paper_id="paper1", title="测试夹具，不是真实文献"))
    store.save_claim(Claim(claim_id="c1", statement="待检验设想（软件测试）", evidence_refs=[{"type":"paper", "id":"paper1"}], status=ClaimStatus.EVIDENCE_LINKED))
    store.save_experiment(Experiment(experiment_id="e1", name="baseline", verifies_claim_ids=["c1"]))
    ctx = ExecutionContext(project_id="p1")
    ctx.set(DRY_RUN, False)
    ctx.set(KNOWLEDGE_STORE, store)
    ctx.set(ARTIFACT_MANAGER, ArtifactManager(store, ArtifactContentStore(config.paths.project_artifacts("p1"))))
    ctx.set(PROVENANCE_VALIDATOR, ProvenanceValidator(store))
    ctx.set(PROJECT_DIR, config.paths.project_dir("p1"))
    ctx.set(DESIGN_CLAIM_IDS, ["c1"])
    ctx.set(EXPERIMENT_IDS, ["e1"])
    return api, config, state, store, ctx


def submit(api, items):
    # Explicit legacy-domain fixture; the public backfill route is closed.
    return api._backfill_experiment_results_internal("p1", api.ExperimentBackfillRequest(results_json=json.dumps({"experiments":items})))


@pytest.mark.parametrize("change", [
    {"status":"typo"}, {"status":"success", "metrics":{}},
    {"metrics":{"accuracy":True}}, {"metrics":{"accuracy":"0.7"}},
    {"metrics":{"accuracy":float("nan")}}, {"metrics":{"accuracy":float("inf")}},
    {"status":"failed", "claim_assessments":{"c1":"supports"}},
    {"claim_assessments":{"unknown":"supports"}},
])
def test_invalid_backfill_never_becomes_completed(setup_project, change):
    api, _, _, store, _ = setup_project
    item = {"name":"baseline", "status":"success", "metrics":{"accuracy":0.7}, **change}
    with pytest.raises(HTTPException) as error:
        submit(api, [item])
    assert error.value.status_code == 422
    assert store.get_experiment("e1").status == ExperimentStatus.PLANNED


def test_backfill_batch_is_all_or_nothing(setup_project):
    api, _, _, store, _ = setup_project
    with pytest.raises(HTTPException):
        submit(api, [{"name":"baseline", "status":"success", "metrics":{"accuracy":0.7}}, {"name":"missing", "status":"failed"}])
    assert store.get_experiment("e1").metrics is None
    with pytest.raises(HTTPException):
        submit(api, [{"name":"baseline", "status":"failed"}, {"name":"baseline", "status":"failed"}])


def test_completed_run_does_not_automatically_verify_claim_and_artifact_is_idempotent(setup_project):
    api, _, _, store, ctx = setup_project
    submit(api, [{"name":"baseline", "status":"success", "metrics":{"accuracy":0.7}}])
    node = ClaimVerifyAgent("claim_verify")
    first = node.run(ctx)
    second = node.run(ctx)
    assert first.status == NodeStatus.SUCCESS
    assert first.output.result_artifact_ids == second.output.result_artifact_ids
    assert store.get_claim("c1").status == ClaimStatus.EVIDENCE_LINKED
    assert len(store.list_artifacts()) == 1


def test_conflicting_user_judgments_keep_all_evidence_without_confirming(setup_project):
    api, _, _, store, ctx = setup_project
    store.save_experiment(Experiment(experiment_id="e2", name="ablation", verifies_claim_ids=["c1"]))
    ctx.set(EXPERIMENT_IDS, ["e1", "e2"])
    submit(api, [{"name":"baseline", "status":"success", "metrics":{"accuracy":0.7}, "claim_assessments":{"c1":"supports"}}, {"name":"ablation", "status":"success", "metrics":{"accuracy":0.4}, "claim_assessments":{"c1":"refutes"}}])
    assert ClaimVerifyAgent("verify").run(ctx).status == NodeStatus.SUCCESS
    claim = store.get_claim("c1")
    assert claim.status == ClaimStatus.EVIDENCE_LINKED
    assert {r["id"] for r in claim.evidence_refs if r["type"] == "experiment"} == {"e1", "e2"}


def test_pending_tasks_skip_anomaly_model_and_partial_backfill_still_waits(setup_project):
    api, _, _, store, ctx = setup_project
    class Forbidden:
        def structured_output(self, **kwargs):
            raise AssertionError("未执行任务不得调用分析模型")
    ctx.set(LLM_REGISTRY, Forbidden())
    assert AnomalyCheckAgent("anomaly").run(ctx).status == NodeStatus.SUCCESS
    store.save_experiment(Experiment(experiment_id="e2", name="ablation"))
    submit(api, [{"name":"baseline", "status":"success", "metrics":{"accuracy":0.7}}])
    output = ExperimentOutcomeAssessAgent("outcome")._execute(ExperimentOutcomeAssessInput(experiment_ids=["e1","e2"],claim_ids=["c1"]),ctx).output.outcome
    assert output["recommendation"] == "wait_for_user_results"
    assert not output["ready_for_writing"]


def test_context_restart_restores_domain_data_not_system_clients(setup_project):
    _, _, _, store, ctx = setup_project
    ctx.set(RESEARCH_TOPIC, "保留的研究主题")
    ctx.set(PROJECT_DOMAIN, "ai")
    ctx.set(DESIGN_METHOD_CONTENT, "实际保存的方法")
    save_context(ctx, store, False)
    assert not any(key.startswith("system.") for key in store.get_kv(KEY)["data"])
    restored = ExecutionContext(project_id="p1")
    sentinel = object()
    restored.set(LLM_REGISTRY, sentinel)
    assert restore_context(restored, store, False)
    assert restored.get(LLM_REGISTRY) is sentinel
    assert restored.get(DESIGN_METHOD_CONTENT) == "实际保存的方法"
    with pytest.raises(ValueError, match="演练/真实"):
        restore_context(restored, store, True)


class WorkflowModel:
    """测试模型，禁止网络；指标来自测试输入而不是模型生成。"""
    def __init__(self):
        self.calls = []
        self.claim_ids = ["c1"]
        self.store = None

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text="## 方法草稿\n已按用户修改要求整理（软件测试）。")

    def structured_output(self, **kwargs):
        self.calls.append(kwargs)
        schema = kwargs["output_schema"]
        name = schema.__name__
        if name == "ExperimentConfigSchema":
            import ast
            self.claim_ids = ast.literal_eval(kwargs["prompt"].split("Claim IDs: ",1)[1].splitlines()[0])
            data = {"configs":[{"name":"trial", "dataset":"fixture", "baseline":"fixture baseline", "purpose":"核查测试设想", "steps":["使用测试夹具运行并记录实际输出"], "requirements":"软件测试", "pass_criterion":"按真实结果判断", "verifies_claim_ids":self.claim_ids}]}
        elif name == "AnomalyReportSchema":
            data = {"has_anomaly":False, "analysis":"本测试夹具无执行异常", "severity":"low", "suggestion":""}
        elif name == "OutcomeAssessSchema":
            data = {"success":True,"verified_claim_ids":["invented"],"refuted_claim_ids":[],"inconclusive_claim_ids":[],"recommendation":"proceed_to_writing","summary":"模型无权覆盖证据判断", "advice":[]}
        elif name == "StyleLearnSchema":
            data = dict(voice="混合",terminology_density="中",section_structure="IMRaD",citation_style="数字",paragraph_length="适中",style_summary="中性事实陈述")
        elif name == "OutlineSchema":
            data = {"sections":[{"title":"结果与局限","claim_ids":self.claim_ids,"key_points":["保留负面结果"],"target_word_count":100}],"abstract":"仅为软件联调测试","total_target_word_count":100}
        elif name == "SectionDraftList":
            assert '"accuracy": 0.61' in kwargs["prompt"], "写作必须收到真实提交的指标，而不是只有结果文件编号"
            import re
            titles=re.findall(r"^\d+\. (.*?) \(target=",kwargs["prompt"],re.M)
            data = {"sections":[{"title":title,"content":"本段仅为软件测试。用户提交的测试指标为 0.61，不支持原设想，未经独立复现。","word_count":40} for title in titles]}
        elif name == "ReviewSchema":
            data = {key:{"score":3,"issues":[],"suggestions":[]} for key in ("structure","citation","expression")}
            data["overall_comment"] = "测试稿件保留负面结果与证据边界。"
        elif name == "TopicRefineSchema":
            data = {"keywords":["federated learning"],"query_strategy":"仅用离线软件测试夹具"}
        elif name == "SubquerySchema":
            data = {"subqueries":["federated learning comparison"],"intents":["benchmark"]}
        elif name in ("MaterialExtractSchema", "ResearchGapReportSchema", "ConflictReportSchema"):
            data = {}
        elif name == "BrainstormSchema":
            data = {"ideas":[{"text":"软件测试假设：同等预算下比较聚合方式的准确率变化。", "source_paper_ids":[p.paper_id for p in self.store.list_papers()][:1]}]}
        elif name == "IdeaValidationSchema":
            data = {"feasibility":0.8,"novelty":0.8,"contribution":0.8,"reason":"模拟评估分数，仅用于软件测试"}
        elif name == "ClaimDraftListSchema":
            data = {"claims":[{"statement":"测试假设：改变聚合方式可能影响准确率，尚未验证。"}]}
        elif name == "AtomDecomposeSchema":
            data = {"atom_concepts":[{"concept_name":"aggregation", "description":"软件测试方法组成", "formula_latex":"w=mean(w_i)","code_stub":"aggregate(weights)","dependencies":[]}], "formula_code_map":[]}
        elif name == "ClaimBatchSchema":
            data = {"claims":[{"statement":"软件测试设想：更换聚合方法后的准确率变化尚待实验。", "evidence_paper_ids":[p.paper_id for p in self.store.list_papers()][:1]}]}
        else:
            raise AssertionError(f"未预期的模型调用：{name}")
        return schema.model_validate(data)


def test_method_feedback_requires_new_confirmation_instead_of_silent_approval(setup_project):
    _, _, _, _, ctx = setup_project
    model = WorkflowModel()
    ctx.set(LLM_REGISTRY, model)
    ctx.set(DESIGN_METHOD_CONTENT, "原方法")
    graph = Graph(name="method-review")
    graph.add_node(MethodReviewHuman("method_review"))
    runner = GraphRunner(graph, ctx)
    runner.start()
    runner.resume_after_human(HumanResponse(action="continue", text="增加公平基线比较"))
    assert runner.is_pending_human()
    assert "增加公平基线比较" in model.calls[-1]["prompt"]
    runner.resume_after_human(HumanResponse(action="continue", text="approve"))
    assert runner.is_completed()


def test_final_save_failure_does_not_return_fake_artifact_id(setup_project):
    _, _, _, _, ctx = setup_project
    class Broken:
        def create_artifact(self, **kwargs):
            raise OSError("测试磁盘故障")
    ctx.set(ARTIFACT_MANAGER, Broken())
    ctx.set(WRITING_DRAFT_CONTENT, "需要保留的已有草稿")
    result = ReviseHuman("revise").continue_after_human(HumanResponse(action="continue", text="confirm"),ctx)
    assert result.status == NodeStatus.FAILED
    assert result.output is None
    assert ctx.get(WRITING_DRAFT_CONTENT) == "需要保留的已有草稿"


def test_resume_wait_backfill_negative_results_and_complete_writing_without_replanning(setup_project, monkeypatch):
    api, config, _, store, _ = setup_project
    model = WorkflowModel()
    monkeypatch.setattr(LLMRegistry, "from_config", classmethod(lambda cls, config: model))
    pipeline = Pipeline(config=config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY)
    session, ctx = pipeline.start_project("p1", "软件测试：联邦学习", "ai")
    for stage in (LifecycleStage.RESEARCH,LifecycleStage.IDEATION,LifecycleStage.DESIGN):
        session.start_stage(stage)
        session.complete_stage(reason="测试夹具已准备")
    ctx.set(DESIGN_CLAIM_IDS,["c1"])
    ctx.set(DESIGN_METHOD_CONTENT,"仅用于软件测试的方法，不作为科研成果")
    save_context(ctx,store,False)
    approve = lambda request: HumanResponse(action="continue",text="ok")
    first = pipeline.run_pipeline("p1", "软件测试：联邦学习", resume=True, human_callback=approve)
    assert first.status == "experiment_pending", first.summary
    count_before = len(store.list_experiments())
    # 模拟刷新/重启，再点继续。仍等待，不能绕过结果门控进入写作。
    still_waiting = Pipeline(config=config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1", "", resume=True, human_callback=approve)
    assert still_waiting.status == "experiment_pending"
    submit(api,[{"name":"trial","status":"success","metrics":{"accuracy":0.61},"claim_assessments":{"c1":"refutes"}}])
    completed = Pipeline(config=config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1", "", resume=True, human_callback=approve)
    assert completed.status == "completed", completed.summary
    assert len(store.list_experiments()) == count_before
    assert sum(c.get("output_schema",type(None)).__name__ == "ExperimentConfigSchema" for c in model.calls) == 1
    assert store.get_claim("c1").status == ClaimStatus.REFUTED
    assert completed.experiment_outcome["ready_for_writing"]
    assert not completed.experiment_outcome["success"]
    from core.knowledge.schema import ArtifactType
    drafts = store.list_artifacts(ArtifactType.PAPER_DRAFT)
    assert len(drafts) == 1 and "0.61" in drafts[0].content
    assert "未经独立复现" in drafts[0].content


def test_all_five_stages_with_offline_search_model_then_resume_to_saved_manuscript(setup_project, monkeypatch):
    """全部真实编排节点运行；仅外部搜索和模型响应替换成离线夹具。"""
    api, config, _, store, _ = setup_project
    model = WorkflowModel()
    model.store = store
    monkeypatch.setattr(LLMRegistry,"from_config",classmethod(lambda cls, config:model))
    from stages.research.agents import PaperFetchAgent, PaperIngestAgent
    meta = {"title":"离线软件测试夹具：Federated learning comparison", "abstract":"仅用于测试检索入库与恢复逻辑，不是真实论文证据。", "source":"sciverse_fixture", "evidence_score":0.9, "url":"https://example.invalid/fixture", "year":2026}
    monkeypatch.setattr(PaperFetchAgent,"_real_fetch",lambda self, data, *args, **kwargs:([dict(meta)],[]))
    monkeypatch.setattr(PaperIngestAgent,"_enrich_urls",staticmethod(lambda metas:None))
    approve = lambda request: HumanResponse(action="continue",text="ok")
    first = Pipeline(config=config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1","联邦学习（离线软件测试）",human_callback=approve,domain="ai")
    assert first.status == "experiment_pending", first.summary
    assert len(first.completed_stages) == 4
    assert store.list_ideas()
    assert any(p.title == meta["title"] for p in store.list_papers())
    plan_path = config.paths.project_dir("p1") / "experiments/experiment_plan.json"
    before = plan_path.read_text(encoding="utf-8")
    submit(api,[{"name":"trial","status":"success","metrics":{"accuracy":0.61},"claim_assessments":{cid:"refutes" for cid in model.claim_ids}}])
    final = Pipeline(config=config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1","",resume=True,human_callback=approve)
    assert final.status == "completed", final.summary
    assert len(final.completed_stages) == 5
    assert plan_path.read_text(encoding="utf-8") == before
    from core.knowledge.schema import ArtifactType
    assert store.list_artifacts(ArtifactType.PAPER_DRAFT)
    assert any(row["node_id"] == "paper_ingest" for row in final.node_history)
    assert any(row["node_id"] == "revise" for row in final.node_history)


def test_restart_at_method_confirmation_does_not_regenerate_paid_steps(setup_project, monkeypatch):
    _, config, _, store, _ = setup_project
    from core.knowledge.schema import Idea
    from stages.common import IDEATION_VALIDATED_IDEA_IDS
    store.save_idea(Idea(idea_id="i1", text="仅软件测试的待检验研究设想"))
    model = WorkflowModel()
    model.store = store
    monkeypatch.setattr(LLMRegistry,"from_config",classmethod(lambda cls, config:model))
    pipeline = Pipeline(config=config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY)
    session,ctx = pipeline.start_project("p1","联邦学习软件测试","ai")
    for stage in (LifecycleStage.RESEARCH,LifecycleStage.IDEATION):
        session.start_stage(stage)
        session.complete_stage(reason="测试上游材料已就绪")
    ctx.set(IDEATION_VALIDATED_IDEA_IDS,["i1"])
    first = pipeline.run_stage(session,ctx,LifecycleStage.DESIGN)
    assert first.status == "pending_human"
    count_before = len([c for c in model.calls if c.get("task_type") == "design_method_formalize"])
    assert count_before == 1
    resumed = Pipeline(config=config, execution_policy=ExecutionPolicy.INTERNAL_LEGACY).run_pipeline("p1","",resume=True,human_callback=lambda req:HumanResponse(action="continue",text="ok"))
    assert resumed.status == "experiment_pending", resumed.summary
    assert len([c for c in model.calls if c.get("task_type") == "design_method_formalize"]) == count_before


def test_duplicate_start_is_reserved_before_worker_runs(setup_project, monkeypatch):
    api, _, state, _, _ = setup_project
    class DelayedThread:
        def __init__(self, **kwargs):
            self.started = False
        def start(self):
            self.started = True
        def is_alive(self):
            return self.started
    monkeypatch.setattr(api.threading,"Thread",DelayedThread)
    assert api.run_project("p1")["project_id"] == "p1"
    assert state.status == "running"
    with pytest.raises(HTTPException) as error:
        api.run_project("p1")
    assert error.value.status_code == 409


def test_completed_project_and_domain_are_recovered_on_restart(setup_project, monkeypatch):
    api, config, _, store, _ = setup_project
    from core.state.session import ProjectSession
    session = ProjectSession.create("p1",config.paths)
    for stage in LifecycleStage.ordered():
        session.start_stage(stage)
        session.complete_stage(reason="软件测试完成")
    store.save_kv("research.topic","保留的主题")
    store.save_kv("project.domain","ai")
    monkeypatch.setattr(api,"_PROJECTS",{})
    api._scan_existing_projects()
    assert api._PROJECTS["p1"].status == "completed"
    assert api._PROJECTS["p1"].domain == "ai"


def test_task_export_retry_preserves_ids_and_completed_results(setup_project):
    api, _, _, store, ctx = setup_project
    from stages.experiment.agents import ExperimentRunTool
    from stages.experiment.io_schema import ExperimentRunInput
    inputs = ExperimentRunInput(configs=[{"name":"repeat_safe", "purpose":"软件测试", "steps":["记录实际输出"]}])
    first = ExperimentRunTool("experiment_run")._execute(inputs,ctx)
    submit(api,[{"experiment_id":first.output.experiment_ids[0],"status":"success","metrics":{"fixture":0.5}}])
    second = ExperimentRunTool("experiment_run")._execute(inputs,ctx)
    assert first.output.experiment_ids == second.output.experiment_ids
    result = store.get_experiment(first.output.experiment_ids[0])
    assert result.status == ExperimentStatus.COMPLETED and result.metrics == {"fixture":0.5}


def test_research_conflicts_persist_idempotently_preserving_adjudication(setup_project):
    _, _, _, store, _ = setup_project
    from core.knowledge import ResearchConflict
    from stages.research.agents import CrossValidateAgent
    node = CrossValidateAgent("cross_validate")
    item = {"claim":"仅测试：两篇论文存在分歧", "subquery":"测试子问题",
            "sources":[{"paper_id":"paper1", "chunk_id":"chunk1", "stance":"support"}],
            "resolution":"需人工复核", "confidence":0.5}
    # 兼容旧 UUID 标识，不能生成重复项或覆盖人工裁决。
    original = ResearchConflict(conflict_id="legacy-uuid", **item)
    original.metadata["adjudication"] = {"verdict":"uncertain", "reason":"人工记录"}
    store.save_research_conflict(original)
    node._persist_conflicts(store, [item])
    node._persist_conflicts(store, [item])
    records = store.list_research_conflicts()
    assert len(records) == 1
    assert records[0].conflict_id == "legacy-uuid"
    assert records[0].metadata == original.metadata
    assert records[0].sources[0]["chunk_id"] == "chunk1"
    node._persist_conflicts(store, [{**item,"claim":"另一个测试分歧"}])
    assert len(store.list_research_conflicts()) == 2


@pytest.mark.parametrize("function_name", ["agentic_search", "meta_search"])
@pytest.mark.parametrize("response_kind", ["oversized", "invalid_json", "zero_limit"])
def test_sciverse_enforces_limit_and_handles_invalid_json(monkeypatch, function_name, response_kind):
    from core.tools import sciverse_search as source
    monkeypatch.setattr(source, "_get_token", lambda:"software-test-token")
    monkeypatch.setattr(source.time, "sleep", lambda seconds:None)
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            if response_kind == "invalid_json":
                raise ValueError("invalid JSON fixture")
            return {"hits":[{"doc_id":str(i),"title":"软件测试夹具"} for i in range(10)]}
    def post(*args, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(source.requests,"post",post)
    result = getattr(source,function_name)("software test",max_results=0 if response_kind == "zero_limit" else 2)
    assert len(result) == (2 if response_kind == "oversized" else 0)
    assert len(calls) == (0 if response_kind == "zero_limit" else 1)


def test_failed_node_history_is_restored_to_web_state(setup_project, monkeypatch):
    api, _, _, store, ctx = setup_project
    ctx.current_stage="ideation";ctx.current_node_id="brainstorm"
    ctx.record_node_result("brainstorm","ideation_brainstorm","failed","软件测试：输出截断")
    save_context(ctx,store,False)
    monkeypatch.setattr(api,"_PROJECTS",{})
    api._scan_existing_projects()
    assert api._PROJECTS["p1"].status == "failed"
    assert api._PROJECTS["p1"].node_history[-1]["node_id"] == "brainstorm"
