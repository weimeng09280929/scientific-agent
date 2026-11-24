"""创新点五大套路锚定（innovation playbook）测试。

验证：
1. INNOVATION_PATTERNS_PROMPT 常量包含五大套路与问题×方法矩阵；
2. TopicRecommendationItem / IdeaDraftItem 支持 innovation_pattern 字段（默认空串，不破坏旧数据）；
3. 方向推荐 step2 的 system prompt 中注入了套路锚定（通过源码静态检查，避免真实联网）。
"""

from __future__ import annotations

import inspect
import pathlib

from core.knowledge.innovation_playbook import (
    INNOVATION_PATTERN_LABELS,
    INNOVATION_PATTERNS_PROMPT,
)
from stages.ideation.agents import IdeaDraftItem
from stages.topic_discovery.agents import TopicRecommendationItem

FIVE_TAGS = ["application", "modification", "fusion", "comparison", "theory"]


def test_prompt_contains_five_patterns_and_matrix():
    for tag in FIVE_TAGS:
        assert tag in INNOVATION_PATTERNS_PROMPT, f"缺少套路标签: {tag}"
    for label in INNOVATION_PATTERN_LABELS.values():
        assert label in INNOVATION_PATTERNS_PROMPT, f"缺少套路中文名: {label}"
    # 问题×方法矩阵关键行
    assert "新问题 + 新方法" in INNOVATION_PATTERNS_PROMPT
    assert "老问题 + 老方法" in INNOVATION_PATTERNS_PROMPT
    # 防天马行空的约束语句
    assert "天马行空" in INNOVATION_PATTERNS_PROMPT


def test_topic_recommendation_item_accepts_pattern():
    item = TopicRecommendationItem(
        topic="联邦学习场景下的成员推理攻击评测基准",
        rationale="相关关键词年增长率 45%",
        innovation_point="统一数据划分下对比五类 MIA 防御",
        innovation_pattern="comparison",
        trend_summary="持续增长",
        difficulty="medium",
        novelty="medium",
        relevance="high",
        popularity_score=70,
    )
    assert item.innovation_pattern == "comparison"
    # 旧数据（不传该字段）兼容
    legacy = TopicRecommendationItem(
        topic="t", rationale="r", innovation_point="i",
        trend_summary="s", difficulty="easy", novelty="low",
        relevance="low", popularity_score=10,
    )
    assert legacy.innovation_pattern == ""


def test_idea_draft_item_accepts_pattern():
    idea = IdeaDraftItem(
        text="（融合创新）把差分隐私机制嫁接到联邦成员推理防御",
        constraints=["需通过隐私预算约束验证"],
        source_paper_ids=["p1"],
        innovation_pattern="fusion",
    )
    assert idea.innovation_pattern == "fusion"
    assert IdeaDraftItem(text="x").innovation_pattern == ""


def test_topic_recommend_topic_uniqueness_guard():
    """方向推荐必须有主题唯一性硬约束 + 服务端去重守卫（防 LLM 复制主题名退化）。"""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "stages" / "topic_discovery" / "agents.py").read_text(encoding="utf-8")
    assert "唯一性硬约束" in src          # prompt 约束
    assert "主题唯一性守卫" in src        # 服务端去重注释
    assert "补生成约束" in src            # 重复槽位补生成
    assert "热门方向去重" in src          # step1 关键词去重


def test_prompts_reference_playbook():
    """方向推荐 / 思路生成 / 研究缺口识别三处 agent 源码中必须引用 INNOVATION_PATTERNS_PROMPT。"""
    base = pathlib.Path(__file__).resolve().parents[1]
    td_src = (base / "stages" / "topic_discovery" / "agents.py").read_text(encoding="utf-8")
    idea_src = (base / "stages" / "ideation" / "agents.py").read_text(encoding="utf-8")
    gap_src = (base / "stages" / "research" / "agents.py").read_text(encoding="utf-8")
    assert "INNOVATION_PATTERNS_PROMPT" in td_src
    assert "INNOVATION_PATTERNS_PROMPT" in idea_src
    assert "INNOVATION_PATTERNS_PROMPT" in gap_src
    # brainstorm system prompt 的构建处实际引用（静态检查 f-string 拼接）
    brainstorm_fn = inspect.getsource(
        __import__("stages.ideation.agents", fromlist=["BrainstormAgent"]).BrainstormAgent
    )
    assert "INNOVATION_PATTERNS_PROMPT" in brainstorm_fn


def test_frontend_methodology_page_hides_playbook():
    """套路属于系统内部策略：方法论页面不得展示，前端不得出现套路标签映射。"""
    base = pathlib.Path(__file__).resolve().parents[1]
    app_src = (base / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "method-innovate" not in app_src
    assert "五大套路（可直接照做）" not in app_src
    assert "INNOVATION_PATTERN_LABELS" not in app_src  # 套路标签徽章已从前端移除
    # 系统内部三处 prompt 引用保持不变
    gap_src = (base / "stages" / "research" / "agents.py").read_text(encoding="utf-8")
    idea_src = (base / "stages" / "ideation" / "agents.py").read_text(encoding="utf-8")
    assert "不要在输出文本中向用户解释或标注套路名称" in gap_src
    assert "不要出现套路名称或分类标签" in idea_src
