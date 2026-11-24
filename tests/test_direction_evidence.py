from types import SimpleNamespace

from core.tools import direction_evidence as de
from stages.topic_discovery import agents as de_trend
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


def test_membership_inference_interest_gets_specific_direction():
    """细粒度兴趣（联邦学习成员推理）必须命中成员推理方向，而非通用联邦方向。"""
    directions = de.federated_directions("联邦学习成员推理")
    assert directions[0]["id"] == "fl_membership_inference"
    assert "membership inference" in directions[0]["query"].lower()
    assert all("federated" in item["query"].lower() for item in directions)
    assert de.supporting_papers_for_topic(
        "联邦学习成员推理攻击与隐私审计",
        [{"title": "Membership Inference Attacks on Federated Learning",
          "abstract": "membership inference privacy audit"}],
    ) != []


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


def test_hot_scan_ranks_classics_by_citations_and_caps_lists(monkeypatch):
    """热门扫描：S2 按被引降序取经典，arXiv 取近期；空结果返回空 dict。"""
    from core.tools.semantic_scholar import S2Paper

    def fake_s2(**kwargs):
        return [
            S2Paper(s2_id="a", title="Low-cited recent", year=2025, citation_count=3),
            S2Paper(s2_id="b", title="Shokri classic", year=2017, citation_count=9269),
            S2Paper(s2_id="c", title="MIA survey", year=2022, citation_count=1202),
        ]

    def fake_arxiv(**kwargs):
        return [SimpleNamespace(title="Label-only MIA 2026", year=2026)]

    monkeypatch.setattr(de_trend, "search_semantic_scholar", fake_s2)
    monkeypatch.setattr(de_trend, "search_arxiv", fake_arxiv)
    agent = TrendFetchAgent(node_id="fetch")
    hot = agent._hot_scan("membership inference attack")
    assert hot["query"] == "membership inference attack"
    assert hot["classics"][0]["title"] == "Shokri classic"
    assert hot["classics"][0]["citation_count"] == 9269
    assert len(hot["classics"]) <= 8 and len(hot["recent"]) == 1

    monkeypatch.setattr(de_trend, "search_semantic_scholar", lambda **k: [])
    monkeypatch.setattr(de_trend, "search_arxiv", lambda **k: [])
    assert agent._hot_scan("membership inference attack") == {}


def test_zh_evidence_merges_chinese_papers(monkeypatch):
    """中文原词检索：OpenAlex/S2 中文论文合并去重，标记 language=zh；无结果返回 None。"""
    def fake_openalex(**kwargs):
        return [SimpleNamespace(to_meta_dict=lambda: {
            "title": "联邦学习中的成员推理攻击综述", "url": "https://example.org/zh1",
            "year": 2024, "doi": "10.9999/zh1",
        })]

    def fake_s2(**kwargs):
        return [
            SimpleNamespace(to_meta_dict=lambda: {
                "title": "联邦学习中的成员推理攻击综述", "url": "https://s2.org/zh1",
                "year": 2024, "doi": "10.9999/zh1",
            }),
            SimpleNamespace(to_meta_dict=lambda: {
                "title": "面向图像分类的成员推断防御", "url": "https://s2.org/zh2",
                "year": 2025, "doi": None,
            }),
        ]

    monkeypatch.setattr(de, "search_openalex", fake_openalex)
    monkeypatch.setattr(de, "search_semantic_scholar", fake_s2)
    ev = de.fetch_zh_evidence("联邦学习成员推理")
    assert ev is not None
    assert ev["id"] == "direction_zh"
    assert ev["sample_count"] == 2  # 同 DOI 去重
    assert all(p.get("language") == "zh" for p in ev["papers"])
    # 支撑校验：中文标题也能命中成员推理标记
    assert de.supporting_papers_for_topic("联邦学习成员推理攻击综述", ev["papers"]) != []

    monkeypatch.setattr(de, "search_openalex", lambda **k: [])
    monkeypatch.setattr(de, "search_semantic_scholar", lambda **k: [])
    assert de.fetch_zh_evidence("联邦学习成员推理") is None
    assert de.fetch_zh_evidence("membership inference") is None  # 无中文不触发


def test_federated_security_branch_keeps_base_and_prepends_fine_plan(monkeypatch):
    """联邦兴趣：LLM 细粒度方向前置补充，但内置安全方向不得被覆盖丢失。"""
    from stages.topic_discovery import agents

    fine = [{"id": "direction_1", "name": "成员推理在异构数据下的可行性",
             "query": "membership inference attack federated learning non-iid"}]
    captured = {}
    fake = [{"id": "fl_poisoning", "name": "投毒防御", "query": "federated learning poisoning",
             "sample_count": 1, "recent_sample_count": 1,
             "papers": [{"title": "Federated Poisoning Defense", "url": "https://arxiv.org/abs/1", "year": 2025}]}]

    def fake_fetch(directions):
        captured["directions"] = directions
        return fake

    monkeypatch.setattr(agents, "fetch_direction_evidence", fake_fetch)
    monkeypatch.setattr(agents, "fetch_zh_evidence", lambda zh_query: None)
    monkeypatch.setattr(TrendFetchAgent, "_translate_to_english", lambda self, registry, interest: "federated learning")
    monkeypatch.setattr(TrendFetchAgent, "_hot_scan", lambda self, q, zh_query="": {})
    monkeypatch.setattr(TrendFetchAgent, "_plan_directions", lambda *args, **kwargs: fine)
    ctx = ExecutionContext(project_id="test")
    ctx.set(DRY_RUN, False)
    ctx.set(LLM_REGISTRY, object())
    ctx.set(PROJECT_DOMAIN, "网络空间安全")
    result = TrendFetchAgent(node_id="fetch")._execute(TrendFetchInput(interest="联邦学习"), ctx)
    assert result.status == NodeStatus.SUCCESS
    ids = [d["id"] for d in captured["directions"]]
    assert ids[0] == "direction_1"          # 细粒度方向前置
    assert "fl_poisoning" in ids            # 内置安全方向保留
    assert len(ids) <= 6
    assert result.output.trend_data["direction_evidence"][0]["id"] == "fl_poisoning"


def test_no_evidence_fails_in_real_mode(monkeypatch):
    from stages.topic_discovery import agents

    monkeypatch.setattr(agents, "fetch_direction_evidence", lambda directions: [])
    monkeypatch.setattr(agents, "fetch_zh_evidence", lambda zh_query: None)
    monkeypatch.setattr(TrendFetchAgent, "_hot_scan", lambda self, q, zh_query="": {})
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


def test_evidence_fallback_unique_topics_when_directions_share_profile():
    """回归：多个细分方向命中同一语义档案时，兜底主题必须互不相同。

    线上 bug：兴趣「联邦学习成员推理」的 5 个方向全部命中 privacy 档案，
    5 条推荐主题一模一样（大模型训练数据泄露风险与隐私保护权衡 ×5）。
    """
    papers = [{"title": "Membership Inference Attacks on Federated Learning",
               "url": "https://arxiv.org/abs/2401.00001", "year": 2025}]
    evidence = [
        {"id": f"fl_mia_{i}", "name": f"联邦学习成员推理细分方向{i}",
         "query": "federated learning membership inference privacy leakage defense",
         "sample_count": 5 + i, "recent_sample_count": 3, "papers": papers}
        for i in range(5)
    ]
    input_obj = TopicRecommendInput(
        interest="联邦学习成员推理",
        trend_data={"direction_evidence": evidence},
        analysis={},
    )
    result = TopicRecommendAgent(node_id="recommend")._evidence_fallback(input_obj, "网络空间安全")
    assert len(result) == 5
    topics = [item["topic"] for item in result]
    assert len(set(topics)) == len(topics), f"兜底主题出现重复: {topics}"
    innovation_points = [item["innovation_point"] for item in result]
    assert len(set(innovation_points)) == len(innovation_points)
    assert all(item["evidence_papers"] for item in result)
