"""真实验收发现的截断与上下文膨胀问题。"""
from types import SimpleNamespace
import pytest
from pydantic import BaseModel

from core.llm.base import LLMTruncatedError, StructuredOutputRequest
from core.llm.registry import LLMRegistry
from core.llm.task_router import TaskConfig, EmbeddingConfig, TaskRouter
from core.llm.providers.openai_provider import OpenAIProvider


class Payload(BaseModel):
    value: int


@pytest.mark.parametrize("messages_mode", [False, True])
def test_structured_truncation_increases_budget_once_preserving_request(messages_mode):
    calls = []
    class Provider:
        def structured_output(self, request, model):
            calls.append((request.max_tokens,request.prompt,request.messages,request.system))
            if len(calls) == 1:
                raise LLMTruncatedError("length")
            return Payload(value=1)
    router = TaskRouter({"x":TaskConfig(task_type="x",provider="p",model="m",max_tokens=4096)},EmbeddingConfig(provider="p",model="m"))
    registry = LLMRegistry(router,{"p":Provider()})
    args = {"messages":[{"role":"user","content":"test"}]} if messages_mode else {"prompt":"test"}
    assert registry.structured_output("x",Payload,system="system",**args).value == 1
    assert [call[0] for call in calls] == [4096,8192]
    assert calls[0][1:] == calls[1][1:]


def test_provider_reports_truncation_instead_of_generic_json_error():
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:SimpleNamespace(choices=[SimpleNamespace(finish_reason="length",message=SimpleNamespace(content='{"value":'))]))))
    with pytest.raises(LLMTruncatedError):
        provider.structured_output(StructuredOutputRequest(output_schema=Payload,prompt="test"),"m")


def test_brainstorm_context_is_bounded_with_ids():
    from stages.ideation.agents import BrainstormAgent
    from stages.ideation.io_schema import BrainstormInput
    from core.orchestration.context import ExecutionContext
    from stages.common import DRY_RUN, LLM_REGISTRY, RESEARCH_TOPIC
    calls=[]
    class Capture:
        def structured_output(self, **kwargs):
            calls.append(kwargs)
            raise LLMTruncatedError("fixture")
    ctx=ExecutionContext(project_id="bounded")
    ctx.set(DRY_RUN,False);ctx.set(LLM_REGISTRY,Capture());ctx.set(RESEARCH_TOPIC,"联邦学习")
    data=BrainstormInput(paper_ids=["p1"],cross_validation_report={"conflicts":[{"claim":"x"*10000}]*100,"consensus":["y"*10000]*100,"gaps":["z"*10000]*100})
    result=BrainstormAgent("brainstorm")._execute(data,ctx)
    assert len(calls[0]["prompt"]) < 15000
    assert "JSON 被截断" in result.summary and "文献已保留" in result.summary


def test_filtered_model_recommendations_keep_real_evidence_candidates(monkeypatch):
    from stages.topic_discovery.agents import TopicRecommendAgent
    from stages.topic_discovery.io_schema import TopicRecommendInput
    from core.orchestration.context import ExecutionContext
    from stages.common import DRY_RUN, LLM_REGISTRY
    from core.orchestration.node import NodeStatus
    ctx=ExecutionContext(project_id="fixture");ctx.set(DRY_RUN,False);ctx.set(LLM_REGISTRY,object())
    node=TopicRecommendAgent("recommend")
    monkeypatch.setattr(node,"_evidence_recommend",lambda *args:[])
    monkeypatch.setattr(node,"_evidence_fallback",lambda *args:[{"topic":"证据限定候选","evidence_papers":[{"title":"软件测试"}]}])
    result=node._execute(TopicRecommendInput(interest="联邦学习",trend_data={"direction_evidence":[{"id":"test"}]},analysis={}),ctx)
    assert result.status == NodeStatus.SUCCESS


def test_federated_fallback_cannot_become_large_model_template():
    from stages.topic_discovery.agents import TopicRecommendAgent
    from stages.topic_discovery.io_schema import TopicRecommendInput
    directions=[{"id":"direction_1","name":"标签翻转投毒的威胁模型","query":"federated learning label flipping poisoning",
        "sample_count":1,"recent_sample_count":1,"papers":[{"title":"软件测试 Federated learning under label flipping","url":"https://example.invalid/test","year":2026}]}]
    result=TopicRecommendAgent("recommend")._evidence_fallback(TopicRecommendInput(interest="联邦学习",trend_data={"direction_evidence":directions},analysis={}),"ai")
    assert "联邦" in result[0]["topic"] and "大模型" not in result[0]["topic"]
    assert "待检验" in result[0]["innovation_point"]


def test_discovery_confirmation_survives_restart_without_new_paid_search(tmp_path,monkeypatch):
    from core.config import GlobalConfig, ProjectPaths
    from core.orchestration.node import HumanResponse
    from core.knowledge import KnowledgeStore
    from core.workflow_checkpoint import KEY
    from runtime.pipeline import Pipeline
    from stages.topic_discovery.agents import TrendFetchAgent
    monkeypatch.setattr(LLMRegistry,"from_config",classmethod(lambda cls,config:object()))
    config=GlobalConfig(paths=ProjectPaths.from_root(tmp_path),dry_run=True)
    first=Pipeline(config=config).run_topic_discovery("p1","联邦学习",auto_research=False)
    assert first.status == "pending_human"
    store=KnowledgeStore(config.paths.project_db("p1"))
    assert store.get_kv(KEY)["current_node_id"] == "topic_select"
    monkeypatch.setattr(TrendFetchAgent,"_execute",lambda *args:pytest.fail("恢复人工审核不能重新检索"))
    final=Pipeline(config=config).run_topic_discovery("p1","联邦学习",resume=True,auto_research=False,human_callback=lambda req:HumanResponse(action="continue",text="ok"))
    assert final.status == "completed"


@pytest.mark.parametrize("reproduction",[False,True])
def test_reproduction_does_not_inflate_novelty_or_require_new_algorithm(tmp_path,reproduction):
    from core.knowledge import KnowledgeStore, Idea
    from core.orchestration.context import ExecutionContext
    from stages.common import DRY_RUN,KNOWLEDGE_STORE,LLM_REGISTRY,RESEARCH_TOPIC
    from stages.ideation.agents import IdeaValidateAgent
    from stages.ideation.io_schema import IdeaValidateInput
    store=KnowledgeStore(tmp_path/"knowledge.db")
    store.save_idea(Idea(idea_id="i1",text="公开可复算的既有算法对照"))
    class Registry:
        def structured_output(self,**kwargs):
            return SimpleNamespace(feasibility=0.8,novelty=0.1,contribution=0.1,reason="可执行对照，但不是新算法")
    ctx=ExecutionContext(project_id="p1")
    ctx.set(DRY_RUN,False);ctx.set(KNOWLEDGE_STORE,store);ctx.set(LLM_REGISTRY,Registry())
    ctx.set(RESEARCH_TOPIC,"联邦学习真实验收" if reproduction else "联邦学习新算法研究")
    result=IdeaValidateAgent("validate")._execute(IdeaValidateInput(idea_ids=["i1"],discussion_notes=""),ctx)
    assert bool(result.output.validated_idea_ids) is reproduction
    assert result.output.validation_reports[0]["novelty"] == 0.1
    assert result.output.validation_reports[0]["novelty_threshold_applied"] is not reproduction
    assert store.get_kv("ideation.validation_reports")[0]["reason"] == "可执行对照，但不是新算法"
    if not reproduction:
        from core.orchestration.node import NodeStatus
        assert result.status == NodeStatus.FAILED


@pytest.mark.parametrize("still_truncated",[False,True])
def test_text_truncation_never_returns_partial_and_has_bounded_retry(still_truncated):
    from core.llm.base import LLMResponse
    calls=[]
    class Provider:
        def complete(self,request,model):
            calls.append(request.max_tokens)
            finish="length" if len(calls)==1 or still_truncated else "stop"
            return LLMResponse(text="partial" if finish=="length" else "complete",model=model,provider="p",raw={"choices":[{"finish_reason":finish}]})
    router=TaskRouter({"x":TaskConfig(task_type="x",provider="p",model="m",max_tokens=4096)},EmbeddingConfig(provider="p",model="m"))
    registry=LLMRegistry(router,{"p":Provider()})
    if still_truncated:
        with pytest.raises(LLMTruncatedError):registry.complete("x",prompt="test")
    else:
        assert registry.complete("x",prompt="test").text == "complete"
    assert calls == [4096,8192]


def test_resume_repairs_legacy_zero_idea_done_state_without_retrieval(tmp_path,monkeypatch):
    from core.config import GlobalConfig, ProjectPaths
    from core.knowledge import Idea
    from core.state.lifecycle import LifecycleStage,StageStatus
    from core.workflow_checkpoint import save_context
    from runtime.pipeline import Pipeline
    from stages.common import IDEATION_IDEA_IDS,IDEATION_VALIDATED_IDEA_IDS,KNOWLEDGE_STORE
    monkeypatch.setattr(LLMRegistry,"from_config",classmethod(lambda cls,config:object()))
    pipeline=Pipeline(GlobalConfig(paths=ProjectPaths.from_root(tmp_path),dry_run=False))
    session,ctx=pipeline.start_project("p1","联邦学习真实验收")
    for stage in (LifecycleStage.RESEARCH,LifecycleStage.IDEATION):
        session.start_stage(stage);session.complete_stage(reason="旧版软件夹具")
    session.start_stage(LifecycleStage.DESIGN);session.mark_blocked(reason="旧版空思路状态")
    store=ctx.get(KNOWLEDGE_STORE);store.save_idea(Idea(idea_id="i1",text="待重新评审的软件夹具"))
    ctx.set(IDEATION_IDEA_IDS,["i1"]);ctx.set(IDEATION_VALIDATED_IDEA_IDS,[])
    ctx.current_stage="design";ctx.record_node_result("atom_decompose","design_atom_decompose","failed","缺少思路")
    save_context(ctx,store,False)
    restored,context=pipeline.resume_project("p1")
    assert restored.status_of(LifecycleStage.RESEARCH)==StageStatus.DONE
    assert restored.status_of(LifecycleStage.IDEATION)==StageStatus.IN_PROGRESS
    assert context.current_stage=="ideation" and context.history()[-1]["node_id"]=="idea_validate"
    assert store.get_idea("i1") is not None


@pytest.mark.parametrize("kind",["method","draft"])
def test_human_review_contains_full_content_not_short_preview(kind):
    from core.orchestration.context import ExecutionContext
    from stages.common import DESIGN_METHOD_CONTENT,WRITING_DRAFT_CONTENT
    from stages.design.agents import MethodReviewHuman
    from stages.writing.agents import ReviseHuman
    ctx=ExecutionContext(project_id="p1")
    content="待审核测试正文"*200+"审核末尾内容"
    ctx.set(DESIGN_METHOD_CONTENT if kind=="method" else WRITING_DRAFT_CONTENT,content)
    node=MethodReviewHuman("method_review") if kind=="method" else ReviseHuman("revise")
    assert "审核末尾内容" in node._render_prompt(ctx)


def test_experiment_planning_receives_method_protocol_beyond_first_2000_chars():
    from core.orchestration.context import ExecutionContext
    from stages.common import DRY_RUN,LLM_REGISTRY,DESIGN_METHOD_CONTENT
    from stages.experiment.agents import ExperimentConfigAgent,ExperimentConfigItem
    from stages.experiment.io_schema import ExperimentConfigInput
    calls=[]
    class Registry:
        def structured_output(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(configs=[ExperimentConfigItem(name="fixture",dataset="synthetic",baseline="baseline",purpose="仅软件测试",steps=["记录实际输出"])])
    ctx=ExecutionContext(project_id="p1");ctx.set(DRY_RUN,False);ctx.set(LLM_REGISTRY,Registry())
    ctx.set(DESIGN_METHOD_CONTENT,"前文"*3000+"METHOD_TAIL_EXPERIMENT_PROTOCOL")
    ExperimentConfigAgent("config")._execute(ExperimentConfigInput(claim_ids=[],method_artifact_id="test"),ctx)
    assert "METHOD_TAIL_EXPERIMENT_PROTOCOL" in calls[0]["prompt"]


def test_results_report_outline_uses_actual_data_not_innovation_claims(tmp_path):
    from core.knowledge import KnowledgeStore,Experiment,ExperimentStatus
    from core.writing_report import build_results_outline
    store=KnowledgeStore(tmp_path/"knowledge.db")
    with pytest.raises(ValueError):build_results_outline(store,[],[],"软件测试")
    store.save_experiment(Experiment(experiment_id="e1",name="软件测试实际记录",status=ExperimentStatus.COMPLETED,metrics={"accuracy":0.7}))
    outline=build_results_outline(store,["unverified"],["e1"],"软件测试")
    assert outline["mode"]=="results_report" and outline["total_target_word_count"]<=3000
    assert len(outline["sections"])==5
    assert outline["sections"][3]["claim_ids"]==["unverified"]
    assert all(not section["claim_ids"] for section in outline["sections"][:3])
    assert "1组" in outline["abstract"] and "不构成新算法" in outline["abstract"]
