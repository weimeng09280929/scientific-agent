"""BrainstormAgent 思路草稿格式回归测试。

线上 bug：真实 LLM 调用失败后走占位路径，_placeholder_drafts 元组格式新旧
拼接（conflicts/consensus 分支为 9 元组，位置 7 是 list），持久化循环
float(draft[7]) 抛 TypeError: float() argument must be ... not 'list'。
规范：所有草稿统一 8 元组 (text, constraints, src_pids, novelty,
feasibility, gap_relevance, innovation_pattern, testability)。
"""

from core.orchestration.context import ExecutionContext
from core.orchestration.node import NodeStatus
from stages.common import DRY_RUN, RESEARCH_TOPIC
from stages.ideation.agents import BrainstormAgent
from stages.ideation.io_schema import BrainstormInput


def _assert_valid_draft(draft: tuple) -> None:
    """统一 8 元组格式断言：数值位必须是数值，pattern 位必须是字符串。"""
    assert isinstance(draft, tuple), f"草稿应为 tuple，实际 {type(draft)}"
    assert len(draft) == 8, f"草稿应为 8 元组，实际 {len(draft)} 元: {draft!r}"
    assert isinstance(draft[0], str) and draft[0].strip()
    assert isinstance(draft[1], list)
    assert isinstance(draft[2], list)
    for pos in (3, 4, 5, 7):
        assert isinstance(draft[pos], (int, float)), (
            f"位置 {pos} 应为数值，实际 {type(draft[pos])}: {draft[pos]!r}"
        )
    assert isinstance(draft[6], str)


def test_placeholder_drafts_format_with_gaps_conflicts_consensus():
    """gaps/conflicts/consensus 各分支与填充器产出的草稿都必须是合法 8 元组。"""
    drafts = BrainstormAgent._placeholder_drafts(
        topic="联邦大语言模型成员推理",
        gaps=[{"statement": "跨设备迁移性缺乏检验", "priority": 2}],
        conflicts=[{"claim": "防御有效性与攻击强度关系存在矛盾结论"}],
        consensus=["成员推断在非独立同分布下更难"],
        paper_ids=["p1", "p2", "p3"],
    )
    assert len(drafts) >= 3
    for draft in drafts:
        _assert_valid_draft(draft)


def test_placeholder_drafts_format_with_empty_inputs():
    """无 gaps/conflicts/consensus 时填充器也要产出合法 8 元组。"""
    drafts = BrainstormAgent._placeholder_drafts(
        topic="联邦学习成员推理", gaps=[], conflicts=[], consensus=[], paper_ids=[]
    )
    assert len(drafts) >= 3
    for draft in drafts:
        _assert_valid_draft(draft)


def _run_dry_run_brainstorm(input_obj: BrainstormInput) -> object:
    agent = BrainstormAgent(node_id="brainstorm")
    ctx = ExecutionContext(project_id="test_brainstorm")
    ctx.set(DRY_RUN, True)
    ctx.set(RESEARCH_TOPIC, "联邦学习成员推理")
    return agent._execute(input_obj, ctx)


def test_execute_dry_run_with_structured_report_survives():
    """dry_run 全链路：占位草稿 -> 持久化循环不得因类型错位崩溃。"""
    input_obj = BrainstormInput(
        paper_ids=["p1", "p2"],
        cross_validation_report={
            "conflicts": [{"claim": "A 方法在 X 场景优于 B", "confidence": 0.4}],
            "consensus": ["成员信号与过拟合程度相关"],
            "gaps": ["缺乏跨设备评测"],
        },
        gap_report=[{"statement": "跨设备迁移衰减未量化", "gap_type": "empirical",
                     "priority": 2}],
    )
    result = _run_dry_run_brainstorm(input_obj)
    assert result.status == NodeStatus.SUCCESS
    assert len(result.output.idea_ids) >= 1


def test_execute_fallback_tuple_when_placeholders_empty():
    """占位草稿为空时走内置兜底块：兜底元组同样必须能通过持久化循环。"""
    agent = BrainstormAgent(node_id="brainstorm")
    ctx = ExecutionContext(project_id="test_brainstorm_fallback")
    ctx.set(DRY_RUN, True)
    ctx.set(RESEARCH_TOPIC, "联邦学习成员推理")
    input_obj = BrainstormInput(paper_ids=["p1", "p2", "p3"])

    import stages.ideation.agents as ideation_agents

    original = BrainstormAgent._placeholder_drafts
    try:
        BrainstormAgent._placeholder_drafts = staticmethod(
            lambda topic, gaps, conflicts, consensus, paper_ids: []
        )
        result = agent._execute(input_obj, ctx)
    finally:
        BrainstormAgent._placeholder_drafts = original

    assert result.status == NodeStatus.SUCCESS
    assert len(result.output.idea_ids) == 1
    meta = result.output.ideas_meta[0]
    assert meta["validation_notes"]["novelty_score"] == 0.5
    assert meta["validation_notes"]["testability_score"] == 0.5
