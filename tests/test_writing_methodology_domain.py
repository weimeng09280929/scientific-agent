"""领域自适应底层逻辑 + 写作方法论落地测试。

覆盖：
1. ResearchGapIdentifyAgent / CrossValidateAgent 不再硬编码材料学科（prompt 按领域画像生成）；
2. ConflictReportSchema 新增 landscape（领域脉络，写作方法论 M2）；
3. 通道 B 断链检测在非材料领域用通用措辞；
4. IdeaDraftItem 新增 testability_score（贡献可检验性）；
5. writing_methodology 模块（P0 前缀 + 按阶段要点）；
6. writing 阶段 system prompt 注入 P0。
"""

from __future__ import annotations

import pathlib

from core.knowledge.writing_methodology import (
    WRITING_P0_PREFIX,
    build_stage_writing_hint,
)
from stages.ideation.agents import IdeaDraftItem
from stages.research.agents import ConflictReportSchema, ResearchGapIdentifyAgent

BASE = pathlib.Path(__file__).resolve().parents[1]


def test_no_material_hardcode_in_gap_prompts():
    src = (BASE / "stages" / "research" / "agents.py").read_text(encoding="utf-8")
    # 旧的硬编码角色词必须消失
    assert "材料科学研究缺口分析专家" not in src
    assert "材料科学文献调研」的科研助理" not in src
    assert "聚焦材料领域的具体空白点" not in src
    # 必须引用领域画像
    assert "get_domain_profile" in src


def test_conflict_report_schema_has_landscape():
    s = ConflictReportSchema()
    assert s.landscape == ""
    s2 = ConflictReportSchema(landscape="a; b; c")
    assert "领域脉络" in (
        ConflictReportSchema.model_fields["landscape"].description or ""
    )


def test_data_driven_detect_domain_wording(tmp_path):
    """非材料领域的断链检测用通用措辞，材料领域沿用原口径。"""
    from core.knowledge import KnowledgeStore

    store = KnowledgeStore(tmp_path / "kv.db")
    agent = ResearchGapIdentifyAgent("research_gap_identify")

    # 空库不报错
    assert agent._data_driven_detect(store, domain="ai") == []
    # 措辞函数级验证：非材料领域不含「材料」「合成工艺」字样的模板词
    # （通过构造最小对象太重，这里检查方法源码中的通用化变量）
    import inspect

    src = inspect.getsource(ResearchGapIdentifyAgent._data_driven_detect)
    assert "_entity_word" in src and "_method_word" in src


def test_idea_draft_testability_score():
    idea = IdeaDraftItem(text="x", testability_score=0.8)
    assert idea.testability_score == 0.8
    assert IdeaDraftItem(text="x").testability_score == 0.5


def test_writing_methodology_module():
    # P0 常驻约束的底线条款
    assert "evidence_log" in WRITING_P0_PREFIX
    assert "待核实" in WRITING_P0_PREFIX
    assert "思路库" in WRITING_P0_PREFIX
    # 五个阶段都有要点
    for stage in ("topic_discovery", "research", "ideation", "experiment", "writing"):
        hint = build_stage_writing_hint(stage)
        assert hint.startswith("【写作视角"), stage
    assert build_stage_writing_hint("unknown_stage") == ""
    # 领域标签注入
    hint = build_stage_writing_hint("writing", domain="ai")
    from core.research_scope import PUBLIC_DOMAINS

    assert PUBLIC_DOMAINS["ai"] in hint


def test_writing_agents_inject_p0():
    src = (BASE / "stages" / "writing" / "agents.py").read_text(encoding="utf-8")
    assert src.count("WRITING_P0_PREFIX") >= 5  # import + 4 处 system prompt


def test_experiment_stage_is_planning_only():
    """实验阶段只做分模块规划，把任务写清楚让用户照着跑完，不代跑。"""
    src = (BASE / "stages" / "experiment" / "agents.py").read_text(encoding="utf-8")
    # 不再引用代码执行函数
    assert "run_python_code" not in src
    # 任务说明书语义存在
    assert "experiment_plan.json" in src
    assert "任务说明书" in src
    assert "不代跑" in src
    # 任务描述字段：目标 / 步骤 / 前置条件 / 判定标准
    for field in ("purpose", "steps", "requirements", "pass_criterion"):
        assert field in src, field
    assert "等待用户执行后回填结果" in src


def test_dual_path_experiment_writing():
    """写作与实验先后：骨架驱动与实验驱动两条路径并存，不强制先后。"""
    hint = build_stage_writing_hint("experiment")
    assert "骨架驱动" in hint
    assert "实验驱动" in hint
    assert "不代跑" in hint
    whint = build_stage_writing_hint("writing")
    assert "实验成功" in whint  # 实验驱动型论文须等实验成功再写
