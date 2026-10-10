from types import SimpleNamespace

from core.tools import direction_evidence as de
from core.orchestration.context import ExecutionContext
from core.orchestration.node import NodeStatus
from stages.common import DRY_RUN, LLM_REGISTRY, PROJECT_DOMAIN
from stages.topic_discovery.agents import TopicRecommendAgent, TrendFetchAgent
from stages.topic_discovery.io_schema import TopicRecommendInput, TrendFetchInput


def test_federated_interest_changes_search_plan():
    privacy = de.federated_directions("联邦学习隐私保护")
    foundation = de.federated_directions("联邦学习大模型微调")
    assert privacy[0]["id"] == "fl_privacy_leak"
    assert foundation[0]["id"] == "fl_foundation"
    assert [item["id"] for item in privacy] != [item["id"] for item in foundation]
    assert not de.is_federated_learning("材料热电性能", "materials")


def test_cybersecurity_domain_uses_security_research_questions():
    directions = de.federated_directions("联邦学习", "网络空间安全")
    assert directions[0]["id"] == "fl_poisoning"
    assert all(item["id"] not in {"fl_cross_silo", "fl_foundation"} for item in directions)
    assert all("federated learning" in item["query"].lower() for item in directions)


def test_large_model_security_uses_stable_preset_directions():
    assert de.is_large_model_security("大模型安全", "人工智能")
    directions = de.large_model_security_directions("大模型提示注入安全", "人工智能")
    assert directions[0]["id"] == "llm_jailbreak"
    assert len(directions) == 5
    assert all("language model" in item["query"].lower() for item in directions)


def test_generic_fallback_directions_keep_user_topic_and_english_query():
    directions = de.fallback_directions("机器人路径规划", "人工智能", "robot path planning")
    assert len(directions) == 3
    assert all("机器人路径规划" in item["name"] for item in directions)
    assert all("robot path planning" in item["query"] for item in directions)


def test_unrelated_search_hits_are_not_evidence(monkeypatch):
    unrelated = SimpleNamespace(to_meta_dict=lambda: {"title": "Healthcare data analysis", "abstract": "Hospital diagnostic data", "url": "https://example.org/paper", "year": 2025})
    monkeypatch.setattr(de, "search_arxiv", lambda **kwargs: [unrelated])
    monkeypatch.setattr(de, "search_crossref", lambda **kwargs: [])
    monkeypatch.setattr(de, "search_openalex", lambda **kwargs: [])
    monkeypatch.setattr(de, "search_semantic_scholar", lambda **kwargs: [])
    assert de.fetch_direction_evidence(de.federated_directions("联邦学习", "网络空间安全")[:1]) == []


def test_specific_topic_requires_specific_paper_support():
    papers = [{"title": "Federated Learning Poisoning Survey", "abstract": "Poisoning attacks"}]
    assert de.supporting_papers_for_topic("面向非IID数据的联邦学习对抗基准构建", papers) == []
    assert de.supporting_papers_for_topic("联邦元学习的后门防御", papers) == []
    supported = [{"title": "Backdoor Attacks on Federated Meta-Learning", "abstract": "Meta-learning backdoor attacks"}]
    assert de.supporting_papers_for_topic("联邦元学习的后门防御", supported) == supported


def test_direction_evidence_requires_real_paper_url(monkeypatch):
    paper = SimpleNamespace(to_meta_dict=lambda: {"title": "Personalized Federated Learning", "url": "https://arxiv.org/abs/1", "year": 2025, "arxiv_id": "1"})
    monkeypatch.setattr(de, "search_arxiv", lambda **kwargs: [paper])
    monkeypatch.setattr(de, "search_crossref", lambda **kwargs: [])
    monkeypatch.setattr(de, "search_openalex", lambda **kwargs: [])
    monkeypatch.setattr(de, "search_semantic_scholar", lambda **kwargs: [paper])
    result = de.fetch_direction_evidence([{"id": "x", "name": "X", "query": "federated learning"}])
    assert result[0]["sample_count"] == 1
    assert result[0]["papers"][0]["url"] == "https://arxiv.org/abs/1"


def test_direction_evidence_no_fake_results(monkeypatch):
    monkeypatch.setattr(de, "search_arxiv", lambda **kwargs: [])
    monkeypatch.setattr(de, "search_crossref", lambda **kwargs: [])
    monkeypatch.setattr(de, "search_openalex", lambda **kwargs: [])
    monkeypatch.setattr(de, "search_semantic_scholar", lambda **kwargs: [])
    assert de.fetch_direction_evidence([{"id": "x", "name": "X", "query": "federated learning"}]) == []


def test_federated_security_branch_is_not_overwritten_by_generic_plan(monkeypatch):
    from stages.topic_discovery import agents

    fake = [{"id": "fl_poisoning", "name": "投毒防御", "query": "federated learning poisoning",
             "sample_count": 1, "recent_sample_count": 1,
             "papers": [{"title": "Federated Poisoning Defense", "url": "https://arxiv.org/abs/1", "year": 2025}]}]
    monkeypatch.setattr(agents, "fetch_direction_evidence", lambda directions: fake)
    monkeypatch.setattr(TrendFetchAgent, "_translate_to_english", lambda self, registry, interest: "federated learning")
    monkeypatch.setattr(TrendFetchAgent, "_plan_directions", lambda *args: (_ for _ in ()).throw(AssertionError("generic path used")))
    ctx = ExecutionContext(project_id="test")
    ctx.set(DRY_RUN, False)
    ctx.set(LLM_REGISTRY, object())
    ctx.set(PROJECT_DOMAIN, "网络空间安全")
    result = TrendFetchAgent(node_id="fetch")._execute(TrendFetchInput(interest="联邦学习"), ctx)
    assert result.status == NodeStatus.SUCCESS
    assert result.output.trend_data["direction_evidence"][0]["id"] == "fl_poisoning"


def test_no_evidence_fails_in_real_mode(monkeypatch):
    from stages.topic_discovery import agents

    monkeypatch.setattr(agents, "fetch_direction_evidence", lambda directions: [])
    ctx = ExecutionContext(project_id="test")
    ctx.set(DRY_RUN, False)
    ctx.set(PROJECT_DOMAIN, "网络空间安全")
    result = TrendFetchAgent(node_id="fetch")._execute(TrendFetchInput(interest="联邦学习"), ctx)
    assert result.status == NodeStatus.FAILED


def test_generic_evidence_fallback_uses_direction_specific_security_topics():
    evidence = [
        {"id": "direction_1", "name": "大模型提示注入与越狱防御",
         "query": "large language model prompt injection jailbreak defense",
         "sample_count": 3, "recent_sample_count": 2,
         "papers": [{"title": "Prompt Injection Attacks and Defenses", "url": "https://doi.org/10.1/a", "year": 2025}]},
        {"id": "direction_2", "name": "大模型训练数据隐私泄露",
         "query": "large language model privacy membership inference data extraction",
         "sample_count": 4, "recent_sample_count": 3,
         "papers": [{"title": "Privacy Risks of Language Models", "url": "https://doi.org/10.1/b", "year": 2025}]},
    ]
    input_obj = TopicRecommendInput(
        interest="大模型安全",
        trend_data={"direction_evidence": evidence},
        analysis={},
    )
    result = TopicRecommendAgent(node_id="recommend")._evidence_fallback(input_obj, "人工智能")
    assert len(result) == 2
    by_id = {item["evidence_id"]: item for item in result}
    assert "自适应越狱" in by_id["direction_1"]["topic"]
    assert "隐私保护权衡" in by_id["direction_2"]["topic"]
    assert all(item["popularity_basis"] and item["relevance_basis"] for item in result)
    assert len({item["rationale"] for item in result}) == len(result)
    assert all("可复现实证与对比研究" not in item["topic"] for item in result)
