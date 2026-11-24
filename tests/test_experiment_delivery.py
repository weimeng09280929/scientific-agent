"""实验代码交付链路回归测试。

线上 bug：LLM 生成的实验代码语法错误（残留 ``` 围栏导致 L1 invalid syntax），
CodeReview 只记录不修复，交付门直接把整条流水线判死
（"实验交付跳过：代码语法错误"）。
修复：① CodeGenerateAgent 加围栏清理 + 一轮 LLM 自修复；
② 交付门降级为「草稿交付」——照样导出任务说明书，但显眼标记代码为草稿。
"""

from pathlib import Path

from core.knowledge import KnowledgeStore
from core.orchestration.context import ExecutionContext
from core.orchestration.node import NodeStatus
from stages.common import DRY_RUN, KNOWLEDGE_STORE, PROJECT_DIR, RESEARCH_TOPIC
from stages.experiment.agents import CodeGenerateAgent, ExperimentRunTool
from stages.experiment.io_schema import ExperimentRunInput


BROKEN_FENCED_CODE = "```python\nprint('hello')\n```"


def test_strip_stray_fences_removes_nested_or_unclosed_fences():
    """双层围栏 / 未闭合围栏残留行必须剥掉，否则 compile() 在 L1 报 invalid syntax。"""
    double_fenced = "```markdown\n```python\nx = 1\n```\n```"
    cleaned = CodeGenerateAgent._strip_stray_fences(double_fenced)
    assert cleaned == "x = 1"
    assert CodeGenerateAgent._strip_stray_fences("```\nprint('a')\n```") == "print('a')"
    # 正常代码不受影响
    assert CodeGenerateAgent._strip_stray_fences("x = 1\ny = 2") == "x = 1\ny = 2"


def test_extract_code_block_output_needs_no_fence_stripping():
    """正则提取出的代码块不应再带围栏残留（双重保险链路）。"""
    text = "说明文字\n```python\nimport os\nprint(os.name)\n```\n结尾"
    code = CodeGenerateAgent._extract_code_block(text)
    cleaned = CodeGenerateAgent._strip_stray_fences(code)
    import ast

    ast.parse(cleaned)  # 不抛 SyntaxError 即通过


def test_experiment_run_exports_tasks_without_code(tmp_path):
    """交付门定位：实验只交付任务说明书，不交付代码（用户明确要求）。

    即使 ctx 中残留旧代码，也不写 run_exp.py；README 含回填格式说明。
    """
    store = KnowledgeStore(tmp_path / "kv.db")
    ctx = ExecutionContext(project_id="test_tasks_export")
    ctx.set(DRY_RUN, False)
    ctx.set(KNOWLEDGE_STORE, store)
    ctx.set(PROJECT_DIR, tmp_path)
    ctx.set(RESEARCH_TOPIC, "联邦学习成员推理")

    input_obj = ExperimentRunInput(
        configs=[{
            "name": "exp_mia_baseline",
            "verifies_claim_ids": ["c1"],
            "purpose": "复现成员推理基线",
            "steps": ["搭建基线脚本", "运行并记录 metrics"],
            "requirements": "python>=3.10",
            "pass_criterion": "攻击精度显著高于随机猜测",
        }],
        code={},  # 不再依赖代码
    )
    result = ExperimentRunTool(node_id="experiment_run")._execute(input_obj, ctx)

    assert result.status == NodeStatus.SUCCESS
    assert "任务" in result.summary

    # 导出文件：只有规划清单与任务说明书，没有代码
    assert (tmp_path / "experiments" / "experiment_plan.json").exists()
    readme = (tmp_path / "experiments" / "README.md").read_text(encoding="utf-8")
    assert "实验任务说明书" in readme
    assert "结果回填格式" in readme
    assert "复现成员推理基线" in readme
    assert not (tmp_path / "experiments" / "run_exp.py").exists()

    # 实验实体为 PLANNED
    exp_ids = result.output.experiment_ids
    assert len(exp_ids) == 1
    exp = store.get_experiment(exp_ids[0])
    assert exp.status.value == "planned"


def test_experiment_run_never_writes_run_exp(tmp_path):
    """静态断言：ExperimentRunTool 不再包含写代码/语法门控逻辑。"""
    import inspect
    src = inspect.getsource(ExperimentRunTool._execute)
    assert "run_exp.py" not in src
    assert "draft_syntax_error" not in src


def test_outcome_assess_waiting_branch_when_experiments_planned(tmp_path):
    """实验仍为 PLANNED（用户未执行回填）时：结论必须是 wait_for_user_results，
    而不是 experiment_failed/retry_experiment（规划型定位回归）。"""
    from stages.experiment.agents import ExperimentOutcomeAssessAgent
    from stages.experiment.io_schema import ExperimentOutcomeAssessInput
    from core.knowledge import Experiment, ExperimentStatus

    store = KnowledgeStore(tmp_path / "kv.db")
    exp_ids = []
    for name in ("exp_a", "exp_b"):
        eid = KnowledgeStore.new_id()
        store.save_experiment(Experiment(
            experiment_id=eid, name=name, verifies_claim_ids=[],
            config={}, status=ExperimentStatus.PLANNED,
        ))
        exp_ids.append(eid)

    ctx = ExecutionContext(project_id="test_outcome_waiting")
    ctx.set(DRY_RUN, False)  # 真实模式；不注入 LLM_REGISTRY → 走纯规则判断
    ctx.set(KNOWLEDGE_STORE, store)
    input_obj = ExperimentOutcomeAssessInput(
        experiment_ids=exp_ids, claim_ids=[], anomaly_report="",
    )
    result = ExperimentOutcomeAssessAgent(node_id="outcome")._execute(input_obj, ctx)

    assert result.status == NodeStatus.SUCCESS
    outcome = result.output.outcome
    assert outcome["success"] is False
    assert outcome["recommendation"] == "wait_for_user_results"
    assert "回填" in outcome["summary"]


def test_pipeline_maps_wait_for_user_results_to_experiment_pending():
    """pipeline 必须把 wait_for_user_results 映射为 experiment_pending（等待回填），
    与真正的 experiment_failed 区分开。静态断言防止回归。"""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "runtime" / "pipeline.py"
    text = src.read_text(encoding="utf-8")
    assert 'outcome.get("recommendation") == "wait_for_user_results"' in text
    assert 'result.status = "experiment_pending"' in text


def test_code_generate_empty_llm_text_falls_back_to_placeholder(tmp_path):
    """线上 bug 回归：LLM 返回空文本时不得产出 0 字符代码
    （空字符串能骗过 compile() 语法检查，最终在交付门以「代码为空」判死流水线）。
    期望：重试仍为空 → 回退占位骨架，代码非空且语法可编译。"""
    from types import SimpleNamespace
    from stages.experiment.agents import CodeGenerateAgent
    from stages.experiment.io_schema import CodeGenerateInput
    from stages.common import LLM_REGISTRY

    class EmptyLLM:
        def complete(self, **kwargs):
            return SimpleNamespace(text="")

    ctx = ExecutionContext(project_id="test_empty_code")
    ctx.set(DRY_RUN, False)
    ctx.set(LLM_REGISTRY, EmptyLLM())

    input_obj = CodeGenerateInput(
        configs=[{"name": "exp_a", "params": {"lr": 0.001}}],
        formula_code_map=[],
    )
    result = CodeGenerateAgent(node_id="code_generate")._execute(input_obj, ctx)

    assert result.status == NodeStatus.SUCCESS
    content = result.output.code["content"]
    assert content.strip(), "占位回退后代码不得为空"
    compile(content, "<experiment>", "exec")  # 语法可编译


def test_check_syntax_empty_code_is_invalid_for_delivery():
    """防御断言：compile("") 本身会通过——因此交付层不得依赖语法检查兜空值。"""
    from core.tools.code_runner import check_syntax

    ok, _ = check_syntax("")
    assert ok is True  # 空字符串能骗过 compile()，代码链路必须显式判空
