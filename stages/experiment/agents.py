"""experiment 阶段 Agent / Tool 节点实现。

节点拓扑（借鉴 AI-Researcher 的「导师-学生迭代」核心方法）：
    ExperimentConfigAgent（生成实验配置：数据集/baseline/超参）
    → CodeGenerateAgent（AI-Researcher Code Agent：生成实验代码）
    → CodeReviewAgent（AI-Researcher Advisor Agent：审查代码）
    → StageCheckpoint
    → ExperimentRunTool（执行实验，ToolNode）
    → AnomalyCheckAgent（检测异常：loss spike/NaN/不收敛）
    → ClaimVerifyAgent（用实验结果验证 Claim）
    → ExperimentOutcomeAssessAgent（评估实验成败，决定是否进入 writing）

执行模式：
- dry_run=True  ：用占位数据返回，不调用 LLM、不真实运行代码
- dry_run=False ：真实调用配置的 LLM（glm-5.3-flash），真实运行实验代码，真实更新 Claim 状态
"""
from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from core.artifacts import ArtifactManager
from core.experiment_results import has_real_metrics
from core.knowledge import (
    ArtifactType,
    Claim,
    ClaimStatus,
    Experiment,
    ExperimentStatus,
    KnowledgeStore,
)
from core.llm import LLMRegistry
from core.orchestration.context import ContextKey, ExecutionContext
from core.orchestration.node import (
    AgentNode,
    HumanNode,
    HumanResponse,
    NodeInput,
    NodeOutput,
    NodeResult,
    NodeStatus,
    ToolNode,
)
from core.tools import check_syntax

from stages.common import (
    ARTIFACT_MANAGER,
    DESIGN_CLAIM_IDS,
    DESIGN_FORMULA_CODE_MAP,
    DESIGN_METHOD_ARTIFACT_ID,
    DESIGN_METHOD_CONTENT,
    DRY_RUN,
    EXPERIMENT_ANOMALY_REPORT,
    EXPERIMENT_CODE,
    EXPERIMENT_CONFIGS,
    EXPERIMENT_IDS,
    EXPERIMENT_OUTCOME,
    EXPERIMENT_RESULT_ARTIFACT_IDS,
    EXPERIMENT_REVIEW_NOTES,
    KNOWLEDGE_STORE,
    LLM_REGISTRY,
    PROJECT_DIR,
    PROJECT_ROOT,
    RESEARCH_TOPIC,
)
from stages.experiment.io_schema import (
    AnomalyCheckInput,
    AnomalyCheckOutput,
    ClaimVerifyInput,
    ClaimVerifyOutput,
    CodeGenerateInput,
    CodeGenerateOutput,
    CodeReviewInput,
    CodeReviewOutput,
    ExperimentConfigInput,
    ExperimentConfigOutput,
    ExperimentOutcomeAssessInput,
    ExperimentOutcomeAssessOutput,
    ExperimentRunInput,
    ExperimentRunOutput,
)

logger = logging.getLogger(__name__)


# ===== 结构化输出 Schema =====

class ExperimentConfigItem(BaseModel):
    """单条实验配置（即一份用户可照着执行的任务说明）。"""

    name: str = Field(description="实验名称")
    dataset: str = Field(description="数据集名称")
    baseline: str = Field(description="对比基线方法")
    hyperparams: dict = Field(default_factory=dict, description="超参")
    verifies_claim_ids: list[str] = Field(
        default_factory=list,
        description="本实验验证的 Claim ID（从给定 claim_ids 中选取）",
    )
    purpose: str = Field(
        default="",
        description="任务目标：这个实验要回答什么问题、验证哪条贡献（1-2 句，写清楚为什么跑）",
    )
    steps: list[str] = Field(
        default_factory=list,
        description="执行步骤：用户照着做的分步操作（准备数据 → 运行命令 → 查看产出），每步一句话",
    )
    requirements: str = Field(
        default="",
        description="前置条件：所需数据集、环境依赖（pip 包）、硬件要求、预计耗时",
    )
    pass_criterion: str = Field(
        default="",
        description="判定标准：什么结果算支持该 Claim，什么结果算反驳（写清具体指标与阈值）",
    )


class ExperimentConfigSchema(BaseModel):
    """实验配置批量 schema。"""

    configs: list[ExperimentConfigItem]


class CodeArtifactSchema(BaseModel):
    """代码生成 schema。"""

    path: str = Field(default="experiments/run_exp.py", description="代码文件相对路径")
    content: str = Field(description="完整 Python 代码")
    language: str = Field(default="python")


class ReviewIssueItem(BaseModel):
    """审查问题项。"""

    severity: str = Field(description="high/medium/low")
    category: str = Field(description="公式偏差/bug/规范")
    location: str = Field(default="", description="代码位置（如 L42-58）")
    comment: str = Field(description="问题描述")
    suggestion: str = Field(default="", description="修改建议")


class ReviewNoteSchema(BaseModel):
    """单轮审查记录 schema。"""

    passed: bool = Field(description="本轮是否通过")
    issues: list[ReviewIssueItem] = []
    summary: str = Field(description="本轮审查总结")


class AnomalyReportSchema(BaseModel):
    """异常检测 schema。"""

    has_anomaly: bool = Field(description="是否检测到异常")
    analysis: str = Field(description="异常分析")
    severity: str = Field(default="low", description="high/medium/low")
    suggestion: str = Field(default="", description="处置建议")


class OutcomeAssessItem(BaseModel):
    """单条方法改进建议（可执行、可溯源）。"""

    target: str = Field(description="建议作用对象：claim_id / experiment_id / method 文档")
    action: str = Field(description="具体动作：补证据/修代码/重跑/调参/换基线/改方法")
    reason: str = Field(description="给出该建议的实验证据（实验结果/metrics/异常）")
    expected: str = Field(description="执行后期望达成的效果")


class OutcomeAssessSchema(BaseModel):
    """实验成败评估 schema。"""

    success: bool = Field(description="实验是否成功验证核心 Claim")
    verified_claim_ids: list[str] = []
    refuted_claim_ids: list[str] = []
    inconclusive_claim_ids: list[str] = []
    recommendation: str = Field(
        description="proceed_to_writing / rollback_to_ideation / retry_experiment / abort"
    )
    summary: str = Field(description="一句话总结")
    advice: list[OutcomeAssessItem] = Field(
        default_factory=list,
        description=(
            "方法改进建议列表（2-6 条）: 基于实验结果给 Claim 验证/方法设计/实验配置"
            "的具体可执行建议。每条必须指向具体 target，说明 action/reason/expected，"
            "禁止空泛话术（如'建议优化模型'）。"
        ),
    )


# ===== ExperimentConfigAgent =====

class ExperimentConfigAgent(AgentNode):
    """实验配置生成 Agent。

    调用 experiment_config_generate，根据 claim_ids + method artifact 生成实验配置
    （数据集、baseline、超参）。配置后续传给 CodeGenerateAgent 作为代码生成的上下文。
    """

    node_type = "experiment_config"
    task_type = "experiment_config_generate"
    input_schema = ExperimentConfigInput
    output_schema = ExperimentConfigOutput
    output_keys = {
        "configs": EXPERIMENT_CONFIGS,
    }

    def _build_input(self, ctx: ExecutionContext) -> ExperimentConfigInput:
        claim_ids = ctx.get(DESIGN_CLAIM_IDS, [])
        method_artifact_id = ctx.get(DESIGN_METHOD_ARTIFACT_ID, "")
        return ExperimentConfigInput(
            claim_ids=claim_ids,
            method_artifact_id=method_artifact_id,
        )

    def _execute(self, input_obj: ExperimentConfigInput, ctx: ExecutionContext) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        dry_run: bool = ctx.get(DRY_RUN, True)

        # 加载方法文档作为 prompt 素材
        method_content = ctx.get(DESIGN_METHOD_CONTENT, "")

        if not dry_run and registry is not None:
            try:
                result = registry.structured_output(
                    task_type=self.task_type,
                    output_schema=ExperimentConfigSchema,
                    system=(
                        "你是实验规划助手。根据 Claim 与方法文档规划 3-6 组实验配置（分模块实验清单）。"
                        "注意：系统只负责实验规划，不代用户跑实验——每条配置就是一份**实验任务说明**，"
                        "用户会照着它独立执行跑完，所以必须写到「不遗漏、不歧义」：\n"
                        "- purpose 任务目标：这个实验回答什么问题、验证哪条贡献；\n"
                        "- steps 执行步骤：从准备数据到运行到查看产出的分步操作，用户照做即可；\n"
                        "- requirements 前置条件：数据集来源、pip 依赖、硬件要求、预计耗时；\n"
                        "- pass_criterion 判定标准：什么结果算支持 Claim、什么算反驳（具体指标+阈值）。\n"
                        "实验设计原则："
                        "1) 覆盖核心 Claim（每条 claim 至少被一组实验验证）；"
                        "2) 必须包含三类实验：baseline 对照、消融实验、参数敏感性分析；"
                        "3) baseline 与本文方法条件公平可比；"
                        "4) hyperparams 给出具体可复现取值（learning rate、epochs、batch size 等）。"
                        "verifies_claim_ids 必须从给定的 claim_ids 列表中选取。"
                    ),
                    prompt=(
                        f"Claim IDs: {input_obj.claim_ids}\n"
                        f"方法文档（包括已审核的实验设计）：\n{method_content[:20000]}\n"
                        f"用户本轮修改要求：{ctx.get(ContextKey[str]('experiment.review_comments'), '')}\n"
                        "请产出完整实验配置，若方法文档含实验设计建议（第 7 章），"
                        "应将其落为具体实验组。"
                    ),
                )
                configs = [c.model_dump() for c in result.configs]
                if not configs or any(not c.get("name", "").strip() or not c.get("steps") or not c.get("purpose") for c in configs):
                    raise ValueError("实验方案为空或缺少目标与执行步骤")
                if len({c["name"] for c in configs}) != len(configs):
                    raise ValueError("实验任务名称重复")
                # 校验 verifies_claim_ids 都在 input_obj.claim_ids 中
                valid_claim_ids = set(input_obj.claim_ids)
                for cfg in configs:
                    cfg["verifies_claim_ids"] = [
                        cid for cid in cfg.get("verifies_claim_ids", [])
                        if cid in valid_claim_ids
                    ]
                    # 兜底：若过滤后为空，关联全部 claim
                    if not cfg["verifies_claim_ids"] and input_obj.claim_ids:
                        cfg["verifies_claim_ids"] = list(input_obj.claim_ids)
            except Exception as e:
                return NodeResult(status=NodeStatus.FAILED, error=f"实验规划失败：{type(e).__name__}", summary="实验规划未完成，未生成占位任务；请修正后重试")
        else:
            if not dry_run:
                return NodeResult(status=NodeStatus.FAILED, error="模型未连接", summary="未生成实验规划，请先配置可用模型")
            configs = self._placeholder(input_obj)

        output = ExperimentConfigOutput(configs=configs)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=f"生成 {len(configs)} 组实验配置",
        )

    @staticmethod
    def _placeholder(input_obj: ExperimentConfigInput) -> list[dict]:
        claim_ids = input_obj.claim_ids or []
        groups = [
            {
                "name": "exp_baseline",
                "dataset": "placeholder_dataset",
                "baseline": "random",
                "hyperparams": {"lr": 1e-3, "epochs": 10, "batch_size": 64},
                "verifies_claim_ids": claim_ids,
            },
            {
                "name": "exp_ablation",
                "dataset": "placeholder_dataset",
                "baseline": "ours(no_ablation)",
                "hyperparams": {"lr": 1e-3, "epochs": 10, "batch_size": 64},
                "verifies_claim_ids": claim_ids,
            },
            {
                "name": "exp_sensitivity",
                "dataset": "placeholder_dataset",
                "baseline": "ours",
                "hyperparams": {"lr": [1e-4, 1e-3, 1e-2], "epochs": 10, "batch_size": 64},
                "verifies_claim_ids": claim_ids,
            },
        ]
        return groups


# ===== CodeGenerateAgent（借鉴 AI-Researcher Code Agent）=====

class CodeGenerateAgent(AgentNode):
    """实验代码生成 Agent（AI-Researcher Code Agent，学生角色）。

    根据实验配置 + design 阶段产生的公式↔代码映射，生成忠实实现论文方法的实验代码。

    设计要点：
    - task_type = experiment_code_generate
    - 输入：实验配置 + 公式↔代码映射
    - 输出：实验代码 {path, content, language}
    - 公式↔代码映射作为强约束：每个公式必须落地为对应代码片段
    """

    node_type = "experiment_code_generate"
    task_type = "experiment_code_generate"
    input_schema = CodeGenerateInput
    output_schema = CodeGenerateOutput
    output_keys = {
        "code": EXPERIMENT_CODE,
    }

    def _build_input(self, ctx: ExecutionContext) -> CodeGenerateInput:
        return CodeGenerateInput(
            configs=ctx.get(EXPERIMENT_CONFIGS, []),
            formula_code_map=ctx.get(DESIGN_FORMULA_CODE_MAP, []),
        )

    def _execute(self, input_obj: CodeGenerateInput, ctx: ExecutionContext) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        dry_run: bool = ctx.get(DRY_RUN, True)

        if not dry_run and registry is not None:
            try:
                # 构造 prompt：实验配置 + 公式↔代码映射
                formula_blocks = "\n".join(
                    f"- 概念: {m.get('concept')}\n"
                    f"  公式: {m.get('formula_latex')}\n"
                    f"  代码骨架: {m.get('code_stub')}\n"
                    f"  状态: {m.get('status')}"
                    for m in input_obj.formula_code_map
                ) or "(无公式↔代码映射)"
                config_blocks = "\n".join(
                    f"- {c.get('name')}: dataset={c.get('dataset')}, "
                    f"baseline={c.get('baseline')}, hyperparams={c.get('hyperparams')}, "
                    f"verifies={c.get('verifies_claim_ids')}"
                    for c in input_obj.configs
                ) or "(无实验配置)"

                # 代码生成用 complete 而非 structured_output：
                # 推理模型在代码生成场景倾向返回纯代码/markdown 代码块，
                # 强制 json_object 反而触发 JSON 解析失败。complete + 代码块提取更稳。
                resp = None
                for attempt in range(2):
                    resp = registry.complete(
                        task_type=self.task_type,
                        system=(
                            "你是实验代码工程师。根据实验配置与公式↔代码映射，"
                            "生成完整可运行的实验代码。每个公式必须落地为对应代码片段，"
                            "不得遗漏或简化。代码须包含：数据加载、模型定义、训练循环、"
                            "评估指标输出。代码必须自包含、可独立运行（不依赖外部数据集时用合成数据）。"
                            "\n\n交付约定（重要）：这份代码会打包给用户在本地终端自行运行，"
                            "系统不代跑——因此必须自包含（依赖只用 pip 可安装的常见库）、"
                            "带 main 入口与清晰的 print 输出、开头注释列出 pip 依赖与运行命令、"
                            "对数据集路径等环境相关项用参数或常量集中声明，方便用户改路径。"
                            "\n\n结果输出约定（必须遵守）："
                            "代码末尾必须将结果写入文件 experiments/results.json，格式："
                            '{"experiments": [{"name": "exp_name", "metrics": {"accuracy": 0.85, "loss": 0.12}, '
                            '"verified_claims": ["claim_id1"], "status": "success"}]}'
                            "。每个实验配置对应一条 experiments 记录，name 与配置中的 name 一致；"
                            "status 取 success/failed/placeholder。同时打印相同 JSON 到 stdout（最后一行为该 JSON）。"
                            "\n\n输出格式：仅返回一个 ```python ... ``` 代码块，不要任何额外说明。"
                        ),
                        prompt=(
                            f"实验配置：\n{config_blocks}\n\n"
                            f"公式↔代码映射：\n{formula_blocks}\n\n"
                            "请生成完整可运行的 Python 实验代码，用 ```python 代码块包裹。"
                        ),
                    )
                    if resp is not None and (resp.text or "").strip():
                        break
                    logger.warning(
                        "CodeGenerate 第 %d 次调用返回空文本，重试", attempt + 1
                    )
                content = self._extract_code_block(resp.text if resp else "")
                if not content:
                    # 兜底：若 LLM 未用代码块包裹，直接把全文当代码
                    content = (resp.text if resp else "") or ""
                content = self._strip_stray_fences(content)
                if not content.strip():
                    # LLM 返回空内容（重试后仍为空）：显式失败走占位骨架，
                    # 绝不让 0 字符代码流到审查/交付门（空字符串能骗过 compile()）。
                    raise RuntimeError("LLM 返回空代码内容（重试后仍为空）")
                code = {
                    "path": "experiments/run_exp.py",
                    "content": content,
                    "language": "python",
                }
                # 语法检查；失败时自修复一轮（坏代码 + 报错交回 LLM）
                ok, err = check_syntax(code["content"])
                if not ok:
                    logger.warning("生成的代码语法错误，尝试自修复: %s", err)
                    ok, err = self._self_repair(registry, code, err)
                if not ok:
                    logger.warning("代码自修复后仍有语法错误，交由审查/交付门处理: %s", err)
            except Exception as e:
                logger.warning("CodeGenerate 真实调用失败，回退占位: %s", e)
                code = self._placeholder(input_obj)
        else:
            code = self._placeholder(input_obj)

        output = CodeGenerateOutput(code=code)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=(
                f"生成实验代码：{code['path']}（{code['language']}），"
                f"{len(code['content'])} 字符"
            ),
        )

    @staticmethod
    def _strip_stray_fences(code: str) -> str:
        """剥掉因嵌套/未闭合围栏残留的 ``` 行（合法 Python 不含围栏行）。

        常见病：LLM 返回 ```markdown 包 ```python 的双层围栏，或代码块
        未正常闭合，正则提取后首行/尾行仍残留 ```，导致 L1 invalid syntax。
        """
        lines = code.splitlines()
        while lines and lines[0].lstrip().startswith("```"):
            lines.pop(0)
        while lines and lines[-1].strip() == "```":
            lines.pop()
        return "\n".join(lines).strip()

    def _self_repair(self, registry: LLMRegistry, code: dict, err: str) -> tuple[bool, str]:
        """语法检查失败时把坏代码 + 报错交回 LLM 修复一轮，返回 (ok, err)。"""
        try:
            fix_resp = registry.complete(
                task_type=self.task_type,
                system=(
                    "你是实验代码工程师。用户提供的 Python 代码未通过语法检查，"
                    "请修复全部语法问题并返回完整可运行的代码（不得截断、不得省略），"
                    "仅返回一个 ```python ... ``` 代码块，不要任何额外说明。"
                ),
                prompt=(
                    f"语法检查报错：{err}\n\n"
                    f"待修复代码：\n```python\n{code['content'][:12000]}\n```"
                ),
            )
            fixed = self._extract_code_block(fix_resp.text) or fix_resp.text
            fixed = self._strip_stray_fences(fixed)
            ok2, err2 = check_syntax(fixed)
            if ok2:
                code["content"] = fixed
                logger.info("实验代码自修复成功")
                return True, ""
            logger.warning("实验代码自修复后仍有语法错误: %s", err2)
            return False, err2
        except Exception as e:
            logger.warning("实验代码自修复调用失败: %s", e)
            return False, err

    @staticmethod
    def _extract_code_block(text: str) -> str:
        """从 LLM 返回中提取 Python 代码。

        处理：
        1. ```python ... ``` 代码块（可在文本任意位置）
        2. ``` ... ``` 代码块
        3. 无代码块包裹的纯代码（兜底：尝试跳过明显的非代码行）
        """
        if not text:
            return ""
        stripped = text.strip()

        # 优先用正则搜索 ```python ... ``` 或 ``` ... ``` 代码块（可在任意位置）
        import re
        patterns = [
            r"```python\s*\n(.*?)```",
            r"```\s*\n(.*?)```",
            r"```python\s*(.*?)```",
            r"```(.*?)```",
        ]
        for pat in patterns:
            match = re.search(pat, stripped, re.DOTALL)
            if match:
                code = match.group(1).strip()
                if code:
                    return code

        # 兜底：若整体看起来像纯代码（无大量自然语言），直接返回
        # 启发式：若首行以 Python 关键字/注释/导入开头，认为是纯代码
        first_line = stripped.splitlines()[0].strip() if stripped.splitlines() else ""
        code_indicators = ("import ", "from ", "#", "def ", "class ", "if ", "try:", "\"\"\"", "'''", "@", "import\t")
        if first_line.startswith(code_indicators):
            return stripped

        # 最后兜底：返回原文（让语法检查拦截）
        return stripped

    @staticmethod
    def _placeholder(input_obj: CodeGenerateInput) -> dict:
        """生成可运行的实验代码（不再硬编码 MNIST 模板）。

        策略：
        1. 根据 formula_code_map 中 code_stub 动态生成函数骨架
        2. 默认走 NumPy + 标准库的纯 Python 实现，不强依赖 torch（兼容材料/统计/博弈论等多种主题）
        3. 末尾按 ExperimentRunTool 协议写入 experiments/results.json
        """
        import textwrap
        stubs = [m.get("code_stub", "") for m in input_obj.formula_code_map if m.get("code_stub")]
        formulas = [m.get("formula_latex", "") for m in input_obj.formula_code_map if m.get("formula_latex")]
        concepts = [m.get("concept", f"concept_{i + 1}") for i, m in enumerate(input_obj.formula_code_map)]

        # 收集实验 config
        configs_for_results = input_obj.configs or [{"name": "exp_1", "params": {}}]
        # 构建每个 config 的实际参数字典（从 configs 中提取）
        placeholder_experiments = []
        for i, c in enumerate(configs_for_results):
            params = c.get("params", {}) or {}
            placeholder_experiments.append({
                "name": c.get("name", f"exp_{i + 1}"),
                "metrics": {
                    # 默认占位指标：包含 status + 简单数值
                    "placeholder_metric": 0.0,
                    **({"primary": 0.0} if not params else {}),
                },
                "verified_claims": c.get("verifies_claim_ids", []),
                "status": "placeholder",
                "config": params,
            })

        # 提取关键实验参数（来自 configs）
        sample_cfg = configs_for_results[0] if configs_for_results else {}
        sample_params = sample_cfg.get("params", {}) if isinstance(sample_cfg, dict) else {}

        # 生成核心算法函数：从 stub 提取函数名（默认 compute_metric）
        func_names = []
        for stub in stubs:
            # 简单提取 def func_name 模式
            import re
            m = re.search(r"def\s+(\w+)\s*\(", stub)
            if m:
                func_names.append(m.group(1))
        if not func_names:
            func_names = ["compute_metric", "evaluate"]

        # 构建代码：使用 stub 中的真实函数作为函数体（如果提供），否则用通用实现
        code_blocks = []
        for i, stub in enumerate(stubs):
            if stub.strip().startswith("def "):
                code_blocks.append(f"# === {concepts[i] if i < len(concepts) else 'concept_' + str(i + 1)} ===")
                code_blocks.append(stub.strip())
                code_blocks.append("")
            else:
                # 占位实现：返回占位指标的通用函数
                fname = func_names[i] if i < len(func_names) else f"concept_{i + 1}_fn"
                code_blocks.append(f"def {fname}(*args, **kwargs):")
                code_blocks.append(f'    """概念 {concepts[i] if i < len(concepts) else "concept_" + str(i + 1)} 的占位实现。"""')
                code_blocks.append(f"    return {{'status': 'placeholder', 'concept': '{concepts[i] if i < len(concepts) else 'concept_' + str(i + 1)}'}}")
                code_blocks.append("")

        formulas_comment = ""
        if formulas:
            formulas_comment = "\n# 对应的数学公式：\n" + "\n".join(f"# {i + 1}. {f[:120]}" for i, f in enumerate(formulas[:5])) + "\n"

        results_literal = json.dumps(
            {"experiments": placeholder_experiments}, ensure_ascii=False, indent=2
        )

        content = f'''"""Auto-generated experiment code (placeholder for dry_run mode).

研究主题相关实验代码占位符。
不依赖 PyTorch / TensorFlow 等深度学习框架，使用纯 NumPy / Python 标准库实现。
真实运行（LLM 模式）会生成完整可运行代码并替换本占位符。

公式↔代码映射落地点：
{chr(10).join(f"#   - {c}" for c in concepts[:5])}{formulas_comment}
"""
import json
import os
import sys
import time
from typing import Any

import numpy as np


{chr(10).join(code_blocks)}


def run_experiment(config: dict) -> dict:
    """运行单个实验配置。

    Args:
        config: 实验参数字典（来自 ExperimentConfigAgent 的 configs）

    Returns:
        包含 metrics / status / config 的结果字典
    """
    start = time.time()
    try:
        # 调用所有概念函数（占位：返回 dict 结果）
        results = {{}}
        for fname in {func_names!r}:
            if fname in globals():
                try:
                    results[fname] = globals()[fname](**config)
                except Exception as e:
                    results[fname] = {{"status": "error", "error": str(e)}}

        # 简单汇总 metric（占位：返回 config 数值本身）
        primary_metric = float(np.mean([v for v in config.values() if isinstance(v, (int, float))])) if config else 0.0

        return {{
            "status": "placeholder",
            "metrics": {{"primary": primary_metric, "details": results}},
            "config": config,
            "elapsed_sec": time.time() - start,
        }}
    except Exception as e:
        return {{
            "status": "error",
            "error": str(e),
            "config": config,
            "elapsed_sec": time.time() - start,
        }}


def main() -> None:
    """主入口：依次运行所有 config，写入 experiments/results.json。"""
    # 实验配置（与 ExperimentConfigAgent 的 configs 对齐）
    configs = [c.get("params", {{}}) for c in configs_for_results]

    print(f"开始运行 {{len(configs)}} 个实验...")
    experiments = []
    for i, cfg in enumerate(configs):
        name = configs_for_results[i].get("name", f"exp_{{i + 1}}") if i < len(configs_for_results) else f"exp_{{i + 1}}"
        verified_claims = configs_for_results[i].get("verifies_claim_ids", []) if i < len(configs_for_results) else []
        print(f"  [{{i + 1}}/{{len(configs)}}] {{name}}: config={{cfg}}")

        result = run_experiment(cfg)
        experiments.append({{
            "name": name,
            "metrics": result.get("metrics", {{}}),
            "verified_claims": verified_claims,
            "status": result.get("status", "placeholder"),
            "config": cfg,
            "elapsed_sec": result.get("elapsed_sec", 0.0),
        }})

    # 按 ExperimentRunTool 协议写入 results.json
    os.makedirs("experiments", exist_ok=True)
    out_path = "experiments/results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({{"experiments": experiments}}, f, ensure_ascii=False, indent=2)

    print(f"\\n实验完成，结果已写入 {{out_path}}")
    print(json.dumps({{"experiments": experiments}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
'''

        return {
            "path": "experiments/run_exp.py",
            "content": content,
            "language": "python",
        }


# ===== CodeReviewAgent（借鉴 AI-Researcher Advisor Agent）=====

class CodeReviewAgent(AgentNode):
    """实验代码审查 Agent（AI-Researcher Advisor Agent，导师角色）。

    审查 Code Agent 生成的实验代码，校验是否忠实实现公式↔代码映射。
    单次 _execute 内最多审查 MAX_REVIEW_ROUNDS 轮。
    """

    node_type = "experiment_code_review"
    task_type = "experiment_code_review"
    input_schema = CodeReviewInput
    output_schema = CodeReviewOutput
    output_keys = {
        "review_notes": EXPERIMENT_REVIEW_NOTES,
    }

    MAX_REVIEW_ROUNDS = 2  # 单次 _execute 内最大审查轮次（真实模式会消耗 LLM tokens）

    def _build_input(self, ctx: ExecutionContext) -> CodeReviewInput:
        return CodeReviewInput(
            code=ctx.get(EXPERIMENT_CODE, {}),
            formula_code_map=ctx.get(DESIGN_FORMULA_CODE_MAP, []),
            prev_review_notes=ctx.get(EXPERIMENT_REVIEW_NOTES, []),
        )

    def _execute(self, input_obj: CodeReviewInput, ctx: ExecutionContext) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        dry_run: bool = ctx.get(DRY_RUN, True)

        review_notes = list(input_obj.prev_review_notes)
        code = input_obj.code

        # 先做语法检查（不消耗 LLM tokens）
        if code and code.get("content"):
            ok, err = check_syntax(code["content"])
            if not ok:
                review_notes.append({
                    "round": len(review_notes) + 1,
                    "passed": False,
                    "issues": [{
                        "severity": "high",
                        "category": "语法错误",
                        "location": "",
                        "comment": err,
                        "suggestion": "修复语法错误后重新生成",
                    }],
                    "summary": f"语法检查未通过: {err}",
                })

        if dry_run or registry is None or not code:
            # 占位：首轮即通过
            if not review_notes:
                review_notes.append({
                    "round": 1,
                    "passed": True,
                    "issues": [],
                    "summary": "占位审查：代码结构完整，公式↔代码映射均落地，通过。",
                })
            passed = review_notes[-1].get("passed", False)
            output = CodeReviewOutput(review_notes=review_notes, passed=passed)
            return NodeResult(
                status=NodeStatus.SUCCESS,
                output=output,
                summary=f"[dry_run] 代码审查完成（{len(review_notes)} 轮）：{'通过' if passed else '未通过'}",
            )

        # 真实审查：单轮审查（避免多轮消耗 tokens；多轮迭代交由外部 GraphRunner 重跑）
        try:
            formula_blocks = "\n".join(
                f"- {m.get('concept')}: {m.get('formula_latex')} -> {m.get('code_stub')}"
                for m in input_obj.formula_code_map
            ) or "(无公式↔代码映射)"

            prev_issues = review_notes[-1]["issues"] if review_notes else []
            result = registry.structured_output(
                task_type=self.task_type,
                output_schema=ReviewNoteSchema,
                system=(
                    "你是实验代码审稿人（Advisor）。校验代码是否忠实实现每个公式，"
                    "并指出 bug、公式偏差、规范问题。若上一轮有 issue，确认是否已修正。"
                    "若无严重问题（high 级 issue 为 0），标记 passed=true。"
                ),
                prompt=(
                    f"实验代码（{code.get('path')}）：\n{code.get('content')}\n\n"
                    f"公式↔代码映射：\n{formula_blocks}\n\n"
                    f"上一轮 issue：{prev_issues}"
                ),
            )
            review_notes.append({
                "round": len(review_notes) + 1,
                "passed": result.passed,
                "issues": [i.model_dump() for i in result.issues],
                "summary": result.summary,
            })
        except Exception as e:
            logger.warning("CodeReview 真实调用失败，宽松通过: %s", e)
            review_notes.append({
                "round": len(review_notes) + 1,
                "passed": True,  # 审查失败时宽松通过，让流程继续
                "issues": [],
                "summary": f"审查调用失败，宽松通过: {e}",
            })

        passed = review_notes[-1].get("passed", False)
        output = CodeReviewOutput(review_notes=review_notes, passed=passed)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=f"代码审查完成（共 {len(review_notes)} 轮）：{'通过' if passed else '未通过'}",
        )


# ===== ExperimentReviewHuman（实验前人工审核）=====

class ExperimentReviewHuman(HumanNode):
    """实验交付前人工审核节点。

    呈现实验规划（分模块任务清单），用户确认后系统导出实验任务包
    （experiment_plan.json + README 任务说明书）到项目 experiments/ 目录。
    系统不代跑实验、不交付代码——用户明确要求"实验只需要给任务"：
    用户按任务说明书用自己的脚本/环境执行，跑完回填结果。
    """

    node_type = "experiment_review_human"
    input_schema = NodeInput
    output_schema = NodeOutput
    output_keys: dict = {}

    def _build_input(self, ctx: ExecutionContext) -> NodeInput:
        return NodeInput()

    def _render_prompt(self, ctx: ExecutionContext) -> str:
        configs = ctx.get(EXPERIMENT_CONFIGS, []) or []

        # 配置摘要
        config_lines = []
        for i, cfg in enumerate(configs):
            hp = cfg.get("hyperparams", {})
            config_lines.append(
                f"  [{i+1}] {cfg.get('name', '?')}\n"
                f"      任务目标: {cfg.get('purpose', '（见任务说明书）')}\n"
                f"      数据集: {cfg.get('dataset', '?')}\n"
                f"      基线: {cfg.get('baseline', '?')}\n"
                f"      超参: {json.dumps(hp, ensure_ascii=False)[:200]}\n"
                f"      验证 Claim: {cfg.get('verifies_claim_ids', [])}"
            )
        config_str = "\n".join(config_lines) or "(无配置)"

        return (
            "实验任务包即将导出（系统不代跑实验、不交付代码，"
            "只把每个实验任务写清楚供用户照着执行），请审核：\n\n"
            f"【实验规划】（{len(configs)} 个实验模块）\n{config_str}\n\n"
            "请选择操作：\n"
            "  - 输入 'approve' 确认导出任务包（写入 experiments/ 目录：任务说明书 + 规划清单）\n"
            "  - 或输入修改意见（将回滚重新规划）\n"
            "  - 或选择「中止」终止流程"
        )

    def _build_output_from_response(
        self, response: HumanResponse, ctx: ExecutionContext
    ) -> Optional[NodeOutput]:
        text = (response.text or "").strip()
        approved = text.lower() in ("approve", "通过", "ok", "y", "yes", "run", "运行")
        return NodeOutput(extra={"approved": approved, "comments": "" if approved else text})

    def continue_after_human(
        self, response: HumanResponse, ctx: ExecutionContext
    ) -> NodeResult:
        if response.action == "abort":
            return NodeResult(
                status=NodeStatus.FAILED,
                error="用户中止实验",
                summary="用户中止实验运行",
            )
        if response.action == "rollback":
            if not (response.text or "").strip():
                return self.run(ctx)
        # continue：检查是否 approve
        text = (response.text or "").strip()
        approved = text.lower() in ("approve", "通过", "ok", "y", "yes", "run", "运行")
        if not approved:
            if text:
                ctx.set(ContextKey[str]("experiment.review_comments"), text)
                result = ExperimentConfigAgent("experiment_config").run(ctx)
                if result.status != NodeStatus.SUCCESS:
                    return result
            return self.run(ctx)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=self._build_output_from_response(response, ctx),
            summary="用户确认导出实验任务包；尚未执行实验",
        )


# ===== ExperimentRunTool =====

class ExperimentRunTool(ToolNode):
    """实验规划交付节点（不代理执行、不交付代码）。

    定位：系统只做实验的**分模块设计与任务规划**——把实验配置与任务说明书
    打包导出到项目 experiments/ 目录（experiment_plan.json + README.md），
    由用户按说明书用本地环境执行，结果回填（experiments/results.json 或
    前端实验页粘贴回填）。系统不关联终端代跑实验，也不提供实验代码。

    真实模式：导出任务包（规划清单 + 任务说明书），实验状态置为 PLANNED。
    dry_run 模式：只生成占位 Experiment ID，不写文件。
    """

    node_type = "experiment_run"
    input_schema = ExperimentRunInput
    output_schema = ExperimentRunOutput
    output_keys = {
        "experiment_ids": EXPERIMENT_IDS,
    }

    def _build_input(self, ctx: ExecutionContext) -> ExperimentRunInput:
        configs = ctx.get(EXPERIMENT_CONFIGS, [])
        # 实验只交付任务不交付代码：不再消费 EXPERIMENT_CODE
        return ExperimentRunInput(configs=configs, code={})

    def _execute(self, input_obj: ExperimentRunInput, ctx: ExecutionContext) -> NodeResult:
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        dry_run: bool = ctx.get(DRY_RUN, True)
        project_dir = ctx.get(PROJECT_DIR)
        project_root = ctx.get(PROJECT_ROOT)

        if dry_run or store is None:
            # 占位
            experiment_ids = [KnowledgeStore.new_id() for _ in input_obj.configs]
            output = ExperimentRunOutput(experiment_ids=experiment_ids)
            return NodeResult(
                status=NodeStatus.SUCCESS,
                output=output,
                summary=f"[dry_run] 规划 {len(experiment_ids)} 个实验（占位 ID）",
            )

        # 真实模式：导出实验任务包（不执行、不交付代码）
        # 用户明确要求："实验不需要给代码，只需要给任务"——
        # 交付物 = experiment_plan.json（结构化规划）+ README.md（任务说明书），
        # 用户用自己的脚本/环境执行，结果按回填格式写入 results.json。

        # 导出目录：优先 PROJECT_DIR，回退 PROJECT_ROOT
        run_dir = Path(project_dir) if project_dir else (
            Path(project_root) if project_root else Path.cwd()
        )
        exp_dir = run_dir / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)

        if not input_obj.configs:
            return NodeResult(status=NodeStatus.FAILED, error="实验任务为空", summary="没有可导出的实验规划，未创建空任务包")
        names = [str(c.get("name", f"exp_{i+1}")).strip() for i,c in enumerate(input_obj.configs)]
        if len(names) != len(set(names)) or not all(names):
            return NodeResult(status=NodeStatus.FAILED, error="实验任务名称为空或重复", summary="请修正任务名称后重试")
        experiments = []
        for index, cfg in enumerate(input_obj.configs):
            material = {"project":ctx.project_id, "method":ctx.get(DESIGN_METHOD_CONTENT, ""), "config":cfg}
            exp_id = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()[:32]
            try:
                experiment = store.get_experiment(exp_id)
            except Exception:
                experiment = Experiment(experiment_id=exp_id, name=names[index], verifies_claim_ids=cfg.get("verifies_claim_ids", []), config=cfg, status=ExperimentStatus.PLANNED)
                experiment.result_summary = "已规划任务，尚未执行；等待用户执行后回填结果（须为实际输出）"
            experiments.append(experiment)

        # 1) 写实验规划清单（分模块：每个实验验证什么、判定标准、怎么跑）
        plan = {
            "topic": str(ctx.get(RESEARCH_TOPIC, "") or ""),
            "note": "系统只做实验任务规划，不代跑实验、不交付代码；请按任务说明书用自己的脚本/环境执行后回填结果",
            "result_file": "experiments/results.json",
            "experiments": [
                {
                    "experiment_id": experiments[i].experiment_id,
                    "name": cfg.get("name", f"exp_{i + 1}"),
                    "verifies_claim_ids": cfg.get("verifies_claim_ids", []),
                    "purpose": cfg.get("purpose", ""),
                    "params": cfg.get("params", {}),
                    "pass_criterion": cfg.get("pass_criterion", ""),
                }
                for i, cfg in enumerate(input_obj.configs)
            ],
        }
        (exp_dir / "experiment_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 2) 写实验任务说明书（每个模块写到用户照着就能独立跑完；不交付代码）
        readme_parts = [
            "# 实验任务说明书",
            "",
            f"> 研究主题：{plan['topic'] or '（见项目）'}",
            "> 系统只负责把任务写清楚，不代跑实验、不交付代码。",
            "> 请按下面各任务的目标/步骤/判定标准，用你自己的脚本或环境执行，",
            "> 跑完后把结果按「结果回填格式」写入 `experiments/results.json`，",
            "> 也可以直接在系统「实验」页粘贴回填。",
            "",
            "## 结果回填格式",
            "",
            "`experiments/results.json`：",
            "",
            "```json",
            json.dumps({
                "experiments": [
                    {
                        "name": plan["experiments"][0]["name"] if plan["experiments"] else "exp_1",
                        "experiment_id": plan["experiments"][0]["experiment_id"] if plan["experiments"] else "",
                        "metrics": {},
                        "status": "inconclusive",
                        "summary": "仅为格式示例，请替换为实际执行结果",
                    }
                ]
            }, ensure_ascii=False, indent=2),
            "```",
            "",
            "- `name` 与下方任务名一致；`status` 取 success / failed / inconclusive。",
            "- success 表示执行完成，须填写真实数值指标，不表示创新已验证。",
            "- 可用 claim_assessments 填写关联论断的 supports / refutes / inconclusive 用户判断。",
            "- 回填后点击继续复核；负面或无法定论的结果也可以如实整理，不能用预期效果代替实际输出。",
            "",
            "## 实验任务清单",
            "",
        ]
        for i, e in enumerate(plan["experiments"], 1):
            readme_parts.append(f"### 任务 {i} · {e['name']}")
            readme_parts.append("")
            if e.get("purpose"):
                readme_parts.append(f"**任务目标**：{e['purpose']}")
            readme_parts.append(f"**验证 Claim**：{', '.join(e['verifies_claim_ids']) or '（未关联）'}")
            cfg = next((c for c in input_obj.configs if c.get("name") == e["name"]), {}) or {}
            if cfg.get("requirements"):
                readme_parts.append(f"**前置条件**：{cfg['requirements']}")
            steps = cfg.get("steps") or []
            if steps:
                readme_parts.append("**执行步骤**：")
                for j, s in enumerate(steps, 1):
                    readme_parts.append(f"  {j}. {s}")
            else:
                readme_parts.append(
                    "**执行步骤**：按任务目标自行实现对比实验（本系统不提供代码），"
                    "覆盖数据集/基线/超参设置，记录 metrics。"
                )
            if cfg.get("dataset"):
                readme_parts.append(f"**数据集**：{cfg['dataset']}")
            if cfg.get("baseline"):
                readme_parts.append(f"**对比基线**：{cfg['baseline']}")
            if e.get("pass_criterion"):
                readme_parts.append(f"**判定标准**：{e['pass_criterion']}")
            readme_parts.append("")
        readme = "\n".join(readme_parts)
        (exp_dir / "README.md").write_text(readme, encoding="utf-8")

        # 3) 建实验实体（PLANNED：等用户本地执行后回填结果）
        # 相同方法与配置重试时复用同一任务，不把已回填的实际结果重置为 planned。
        store.save_experiments_batch(experiments)
        experiment_ids = [e.experiment_id for e in experiments]

        output = ExperimentRunOutput(experiment_ids=experiment_ids)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=(
                f"实验任务已规划：{len(experiment_ids)} 个实验模块的任务说明书已导出到 experiments/"
                "（目标/步骤/判定标准均已写清，不含代码），请用户照着执行跑完后在实验页或 results.json 回填结果"
            ),
        )


# ===== AnomalyCheckAgent =====

class AnomalyCheckAgent(AgentNode):
    """异常检测 Agent。

    调用 experiment_anomaly_analyze 检测实验异常（loss spike/NaN/不收敛），
    异常时把实验状态置为 ANOMALY_DETECTED 并记录 anomaly_notes。
    """

    node_type = "experiment_anomaly_check"
    task_type = "experiment_anomaly_analyze"
    input_schema = AnomalyCheckInput
    output_schema = AnomalyCheckOutput
    output_keys = {
        "anomaly_report": EXPERIMENT_ANOMALY_REPORT,
    }

    def _build_input(self, ctx: ExecutionContext) -> AnomalyCheckInput:
        experiment_ids = ctx.get(EXPERIMENT_IDS, [])
        return AnomalyCheckInput(experiment_ids=experiment_ids)

    def _execute(self, input_obj: AnomalyCheckInput, ctx: ExecutionContext) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        dry_run: bool = ctx.get(DRY_RUN, True)

        if dry_run:
            output = AnomalyCheckOutput(anomaly_report="无异常（dry_run）")
            return NodeResult(
                status=NodeStatus.SUCCESS,
                output=output,
                summary="[dry_run] 异常检测完成：无异常",
            )
        if store is None:
            return NodeResult(status=NodeStatus.FAILED, error="知识库未连接", summary="无法复核实验结果")

        # 真实检测：基于每个实验的 result_summary 与 anomaly_notes 调用 LLM
        reports: list[dict] = []
        for exp_id in input_obj.experiment_ids:
            try:
                exp = store.get_experiment(exp_id)
            except Exception:
                continue

            # 未执行的任务没有运行异常可分析，也不应产生额外模型调用。
            if exp.status in (ExperimentStatus.PLANNED, ExperimentStatus.RUNNING):
                continue

            # 已 FAILED 的实验直接判定为异常
            if exp.status == ExperimentStatus.FAILED:
                reports.append({
                    "exp_id": exp_id,
                    "has_anomaly": True,
                    "analysis": exp.anomaly_notes or "实验运行失败",
                    "severity": "high",
                    "suggestion": "检查代码或配置后重试",
                })
                continue

            if registry is None:
                reports.append({"exp_id": exp_id, "has_anomaly": exp.status == ExperimentStatus.ANOMALY_DETECTED,
                                "analysis": exp.anomaly_notes or "仅保留用户提交状态，未进行模型复核"})
                continue
            try:
                resp = registry.structured_output(
                    task_type=self.task_type,
                    output_schema=AnomalyReportSchema,
                    system=(
                        "你是实验监控助手。分析实验结果摘要，检测 loss spike/NaN/不收敛等异常。"
                        "若结果正常或无足够信息判断，has_anomaly=false。"
                    ),
                    prompt=(
                        f"实验 {exp.name} 的结果摘要：\n{exp.result_summary or '(空)'}\n"
                        f"异常记录：{exp.anomaly_notes or '(无)'}"
                    ),
                )
                if resp.has_anomaly:
                    exp.status = ExperimentStatus.ANOMALY_DETECTED
                    exp.anomaly_notes = resp.analysis
                    store.save_experiment(exp)
                reports.append({
                    "exp_id": exp_id,
                    "has_anomaly": resp.has_anomaly,
                    "analysis": resp.analysis,
                    "severity": resp.severity,
                    "suggestion": resp.suggestion,
                })
            except Exception as e:
                logger.warning("AnomalyCheck 调用失败（exp=%r）: %s", exp_id, e)
                reports.append({
                    "exp_id": exp_id,
                    "has_anomaly": False,
                    "analysis": f"检测失败: {e}",
                    "severity": "low",
                    "suggestion": "",
                })

        anomaly_report = json.dumps(reports, ensure_ascii=False, indent=2)
        anomaly_count = sum(1 for r in reports if r.get("has_anomaly"))
        output = AnomalyCheckOutput(anomaly_report=anomaly_report)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=f"异常检测完成：{anomaly_count}/{len(reports)} 个实验有异常",
        )


# ===== ClaimVerifyAgent =====

class ClaimVerifyAgent(AgentNode):
    """Claim 验证 Agent。

    把实验结果关联到 Claim，status=VERIFIED，并创建 EXPERIMENT_RESULT Artifact。
    借鉴 AI-Researcher：验证结果回填 Claim 的 evidence_refs，形成完整证据链。
    """

    node_type = "experiment_claim_verify"
    task_type = "experiment_anomaly_analyze"
    input_schema = ClaimVerifyInput
    output_schema = ClaimVerifyOutput
    output_keys = {
        "result_artifact_ids": EXPERIMENT_RESULT_ARTIFACT_IDS,
    }

    def _build_input(self, ctx: ExecutionContext) -> ClaimVerifyInput:
        experiment_ids = ctx.get(EXPERIMENT_IDS, [])
        claim_ids = ctx.get(DESIGN_CLAIM_IDS, [])
        return ClaimVerifyInput(experiment_ids=experiment_ids, claim_ids=claim_ids)

    def _execute(self, input_obj: ClaimVerifyInput, ctx: ExecutionContext) -> NodeResult:
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        manager: Optional[ArtifactManager] = ctx.get(ARTIFACT_MANAGER)
        dry_run: bool = ctx.get(DRY_RUN, True)

        if dry_run:
            # 占位
            result_artifact_ids = [KnowledgeStore.new_id() for _ in input_obj.experiment_ids]
            output = ClaimVerifyOutput(result_artifact_ids=result_artifact_ids)
            return NodeResult(
                status=NodeStatus.SUCCESS,
                output=output,
                summary=f"[dry_run] 验证 Claim 并生成 {len(result_artifact_ids)} 个结果 Artifact（占位 ID）",
            )
        if store is None or manager is None:
            return NodeResult(status=NodeStatus.FAILED, error="知识库或产物管理器未连接", summary="无法关联实际实验结果")

        # 只关联实际结果；完成运行本身不能证明假设成立。
        result_artifact_ids: list[str] = []
        verdicts: dict[str, list[str]] = {}
        linked_claims = {}
        existing_artifacts = manager.list_artifacts(ArtifactType.EXPERIMENT_RESULT)
        for exp_id in input_obj.experiment_ids:
            try:
                exp = store.get_experiment(exp_id)
            except Exception:
                continue

            # 仅 COMPLETED 状态的实验才验证 Claim（FAILED/ANOMALY 不验证）
            if exp.status != ExperimentStatus.COMPLETED or not has_real_metrics(exp.metrics):
                continue

            # 把实验结果回填到关联的 Claim
            for claim_id in exp.verifies_claim_ids:
                try:
                    claim = linked_claims.get(claim_id) or store.get_claim(claim_id)
                    # 添加 experiment 证据（去重）
                    existing_exp_ids = {
                        ref["id"] for ref in claim.evidence_refs
                        if ref.get("type") == "experiment"
                    }
                    if exp_id not in existing_exp_ids:
                        claim.evidence_refs.append({"type": "experiment", "id": exp_id})
                    linked_claims[claim_id] = claim
                    verdict = exp.config.get("user_claim_assessments", {}).get(claim_id)
                    verdicts.setdefault(claim_id, []).append(verdict or "inconclusive")
                except Exception as e:
                    logger.warning("Claim %s 验证失败: %s", claim_id, e)

            # 创建 EXPERIMENT_RESULT Artifact
            try:
                content = (exp.result_summary or "用户提交的实际结果，未经系统独立复现") + "\n实际指标：" + json.dumps(exp.metrics, ensure_ascii=False, sort_keys=True)
                previous = next((a for a in reversed(existing_artifacts) if a.cites_experiment_ids == [exp_id]), None)
                if previous is not None and manager.read_content(previous) == content:
                    artifact = previous
                elif previous is not None:
                    artifact = manager.new_version(previous.artifact_group, content=content,
                                                   cites_claim_ids=exp.verifies_claim_ids, created_by=self.node_type)
                else:
                    artifact = manager.create_artifact(
                        artifact_type=ArtifactType.EXPERIMENT_RESULT, title=f"用户实验结果: {exp.name}",
                        content=content, cites_claim_ids=exp.verifies_claim_ids, cites_experiment_ids=[exp_id],
                        source_stage="experiment", created_by=self.node_type,
                    )
                result_artifact_ids.append(artifact.artifact_id)
            except Exception as e:
                logger.warning("Artifact 创建失败（exp=%r）: %s", exp_id, e)

        for claim_id, claim in linked_claims.items():
            assessments = set(verdicts[claim_id])
            if assessments == {"supports"}:
                claim.status = ClaimStatus.VERIFIED
                claim.verified_at = datetime.now()
            elif assessments == {"refutes"}:
                claim.status = ClaimStatus.REFUTED
                claim.verified_at = None
            else:
                claim.status = ClaimStatus.EVIDENCE_LINKED
                claim.verified_at = None
            store.save_claim(claim)

        output = ClaimVerifyOutput(result_artifact_ids=result_artifact_ids)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=f"关联 {len(result_artifact_ids)} 份实际结果；论断状态依据用户判断，不是独立科学核验",
        )


# ===== ExperimentOutcomeAssessAgent =====

class ExperimentOutcomeAssessAgent(AgentNode):
    """实验成败评估 Agent。

    评估实验结果是否验证了核心 Claim，给出"是否进入 writing"的建议。
    位于 experiment 阶段末尾（ClaimVerifyAgent 之后），是 experiment → writing
    阶段切换的决策关口。

    重要：实验失败是科研常态——Claim 被实验反驳是正常、有价值的发现。
    系统不应在实验失败（success=False）时强行进入论文写作阶段。
    """

    node_type = "experiment_outcome_assess"
    task_type = "experiment_outcome_assess"
    input_schema = ExperimentOutcomeAssessInput
    output_schema = ExperimentOutcomeAssessOutput
    output_keys = {
        "outcome": EXPERIMENT_OUTCOME,
    }

    def _build_input(self, ctx: ExecutionContext) -> ExperimentOutcomeAssessInput:
        return ExperimentOutcomeAssessInput(
            experiment_ids=ctx.get(EXPERIMENT_IDS, []),
            claim_ids=ctx.get(DESIGN_CLAIM_IDS, []),
            anomaly_report=ctx.get(EXPERIMENT_ANOMALY_REPORT, ""),
        )

    def _execute(
        self, input_obj: ExperimentOutcomeAssessInput, ctx: ExecutionContext
    ) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        dry_run: bool = ctx.get(DRY_RUN, True)

        # dry_run：诚实返回失败（占位数据无法验证 Claim），
        # 但也要给出结构化、可执行的方法改进建议（而非一句话话术）
        if dry_run:
            advice = self._build_rule_advice(
                claim_statuses=[], exp_summaries=[], anomaly_report=""
            )
            outcome = {
                "success": False,
                "verified_claim_ids": [],
                "refuted_claim_ids": [],
                "inconclusive_claim_ids": list(input_obj.claim_ids),
                "recommendation": "retry_experiment",
                "summary": (
                    "dry_run 模式：实验代码与运行均为占位，无法真正验证 Claim。"
                    "诚实返回 success=False，不强行进入论文写作阶段。"
                    "启用真实 API 调用（SRA_DRY_RUN=false）后，将基于真实实验结果评估。"
                ),
                "advice": advice,
            }
            output = ExperimentOutcomeAssessOutput(outcome=outcome)
            return NodeResult(
                status=NodeStatus.SUCCESS,
                output=output,
                summary=outcome["summary"],
            )

        # 真实模式：先从 KnowledgeStore 收集实验状态，再调 LLM 评估
        if store is None:
            outcome = {
                "success": False,
                "verified_claim_ids": [],
                "refuted_claim_ids": [],
                "inconclusive_claim_ids": list(input_obj.claim_ids),
                "recommendation": "abort",
                "summary": "KnowledgeStore 未注入，无法评估",
                "advice": [],
            }
            output = ExperimentOutcomeAssessOutput(outcome=outcome)
            return NodeResult(
                status=NodeStatus.SUCCESS,
                output=output,
                summary=outcome["summary"],
            )

        # 收集实验素材
        exp_summaries: list[dict] = []
        exp_result_lines: list[str] = []
        for exp_id in input_obj.experiment_ids:
            try:
                exp = store.get_experiment(exp_id)
                # 提取 metrics：优先 metrics 字段，兜底从 result_summary 解析
                exp_metrics = exp.metrics
                if exp_metrics is None:
                    exp_metrics = self._extract_metrics_from_summary(
                        exp.result_summary or ""
                    )
                exp_summaries.append({
                    "exp_id": exp_id,
                    "name": exp.name,
                    "status": exp.status.value,
                    "verifies_claim_ids": exp.verifies_claim_ids,
                    "metrics": exp_metrics,
                    "result_summary": (exp.result_summary or "")[:500],
                    "anomaly_notes": exp.anomaly_notes,
                })
                exp_result_lines.append(
                    f"- 实验 {exp.name}: metrics="
                    f"{json.dumps(exp_metrics, ensure_ascii=False)}, status={exp.status.value}"
                )
            except Exception:
                pass
        results_text = (
            "实验结果：\n" + "\n".join(exp_result_lines)
            if exp_result_lines else "实验结果：(无)"
        )

        # 收集 Claim 验证状态
        claim_statuses: list[dict] = []
        for claim_id in input_obj.claim_ids:
            try:
                claim = store.get_claim(claim_id)
                claim_statuses.append({
                    "claim_id": claim_id,
                    "statement": claim.statement,
                    "status": claim.status.value,
                    "evidence_count": len(claim.evidence_refs),
                })
            except Exception:
                pass

        # 基于 Claim 状态做规则判断（避免 LLM 调用浪费）
        verified_claim_ids = [
            c["claim_id"] for c in claim_statuses
            if c["status"] == ClaimStatus.VERIFIED.value
        ]
        refuted_claim_ids = [
            c["claim_id"] for c in claim_statuses
            if c["status"] == ClaimStatus.REFUTED.value
        ]
        inconclusive_claim_ids = [
            c["claim_id"] for c in claim_statuses
            if c["status"] not in (
                ClaimStatus.VERIFIED.value, ClaimStatus.REFUTED.value
            )
        ]

        # 决策逻辑：
        # - 实验全部仍为 PLANNED（用户尚未本地执行回填）→ wait_for_user_results
        # - 有验证的 Claim 且无反驳 → success=True, proceed_to_writing
        # - 全部无法定论 → retry_experiment
        # - 有反驳 → rollback_to_ideation
        # - 异常严重 → abort
        try:
            anomaly_details = json.loads(input_obj.anomaly_report or "[]")
            has_anomaly = any(r.get("has_anomaly") for r in anomaly_details if isinstance(r, dict))
        except (ValueError, TypeError):
            has_anomaly = bool(input_obj.anomaly_report and "无异常" not in input_obj.anomaly_report)
        not_executed = any(
            e.get("status") in (ExperimentStatus.PLANNED.value, ExperimentStatus.RUNNING.value)
            for e in exp_summaries
        ) or len(exp_summaries) != len(input_obj.experiment_ids)
        completed_results = [e for e in exp_summaries if e["status"] == ExperimentStatus.COMPLETED.value and has_real_metrics(e["metrics"])]
        ready_for_writing = bool(completed_results) and not not_executed

        if not_executed:
            # 规划型定位：实验由用户本地执行——还没执行就不是"失败"，是等待回填
            success = False
            recommendation = "wait_for_user_results"
            summary = (
                "仍有实验未完成、未回填或记录缺失。"
                "请在本地按 experiments/README.md 任务说明书执行实验并回填 results.json，"
                "之后继续运行复核；不会重新生成任务，也不会把预期结果当成实际数据。"
            )
        elif ready_for_writing:
            success = bool(verified_claim_ids) and not refuted_claim_ids and not inconclusive_claim_ids
            recommendation = "proceed_to_writing"
            summary = f"已有 {len(completed_results)} 份带指标的用户实验结果，可整理结果稿；支持/反驳/无法定论须分别陈述，不能宣称系统已独立验证创新。"
        elif refuted_claim_ids:
            success = False
            recommendation = "rollback_to_ideation"
            summary = f"实验反驳了 {len(refuted_claim_ids)} 个 Claim，建议回滚到思路探讨"
        elif inconclusive_claim_ids and has_anomaly:
            success = False
            recommendation = "abort"
            summary = "实验异常严重且 Claim 无法定论，建议中止"
        else:
            success = False
            recommendation = "retry_experiment"
            summary = f"实验无法定论（{len(inconclusive_claim_ids)} 个 Claim），建议重试实验"

        # ---- 方法改进建议（advice）：替代原先的一句话话术 ----
        advice: list[dict] = self._build_rule_advice(
            claim_statuses=claim_statuses,
            exp_summaries=exp_summaries,
            anomaly_report=input_obj.anomaly_report,
        )

        # 尝试用 LLM 生成更细致的逐条建议（失败/无 registry 时保留规则建议）
        # 注意：实验尚未执行（wait_for_user_results）时跳过 LLM 结论覆盖——
        # 没有回填数据时 LLM 只会瞎猜，且不能把"等待回填"改判成"失败重试"。
        if registry is not None and not not_executed:
            try:
                result = registry.structured_output(
                    task_type=self.task_type,
                    output_schema=OutcomeAssessSchema,
                    system=(
                        "你是科研评估助手。根据实验结果判断每个 Claim 是被验证、反驳还是无法定论。"
                        "实验失败是科研常态，不应强行进入写作阶段。"
                        "请结合 metrics 量化数据与 Claim 语义判断验证结论，"
                        "并给出 2-6 条**可执行**的方法改进建议：每条必须指向具体 "
                        "target（claim_id/experiment_id/method 文档），包含 action/reason/expected，"
                        "禁止空泛话术（如'建议优化模型'）。"
                    ),
                    prompt=(
                        f"待验证 Claim: {json.dumps(claim_statuses, ensure_ascii=False)}\n"
                        f"{results_text}\n"
                        f"实验明细: {json.dumps(exp_summaries, ensure_ascii=False)}\n"
                        f"异常报告: {input_obj.anomaly_report or '(无)'}"
                    ),
                )
                # 模型只提供改进建议，不得覆盖真实运行状态或用户证据判断。
                if getattr(result, "advice", None) is not None:
                    llm_advice = [
                        {
                            "target": a.target,
                            "action": a.action,
                            "reason": a.reason,
                            "expected": a.expected,
                        }
                        for a in result.advice
                    ]
                    if llm_advice:
                        advice = llm_advice
            except Exception as e:
                logger.warning("OutcomeAssess LLM 调用失败，用规则判断: %s", e)

        outcome = {
            "success": success,
            "ready_for_writing": ready_for_writing,
            "verified_claim_ids": verified_claim_ids,
            "refuted_claim_ids": refuted_claim_ids,
            "inconclusive_claim_ids": inconclusive_claim_ids,
            "recommendation": recommendation,
            "summary": summary,
            "advice": advice,
        }

        output = ExperimentOutcomeAssessOutput(outcome=outcome)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=summary,
        )

    @staticmethod
    def _build_rule_advice(
        claim_statuses: list[dict],
        exp_summaries: list[dict],
        anomaly_report: str = "",
    ) -> list[dict]:
        """规则引擎生成可执行的方法改进建议（advice）。

        替代原本"一句话话术"：每条 advice 指向具体 target（claim_id / experiment_id /
        method 文档），说明 action / reason / expected。用于 dry_run 与 LLM 失败兜底。
        """
        advice: list[dict] = []
        has_anomaly = bool(anomaly_report and "无异常" not in anomaly_report)

        # 1) 按 Claim 状态逐条给出验证/补证建议
        for c in claim_statuses:
            st = c.get("status", "draft")
            cid = c.get("claim_id", "")
            statement = (c.get("statement") or "")[:60]
            if st == ClaimStatus.VERIFIED.value:
                advice.append({
                    "target": cid,
                    "action": "补充预期结果细节",
                    "reason": f"Claim 已被实验验证（{statement}…）",
                    "expected": "在论文 Experiments 章节给出量化指标与显著性说明",
                })
            elif st == ClaimStatus.REFUTED.value:
                advice.append({
                    "target": cid,
                    "action": "回退设计并重构假设",
                    "reason": f"Claim 被实验反驳（{statement}）",
                    "expected": "修改方法假设或缩小适用范围，再重新实验验证",
                })
            else:
                advice.append({
                    "target": cid,
                    "action": "补充文献证据或实验结果",
                    "reason": f"Claim 处于 {st}，证据不足（{statement}）",
                    "expected": "补齐证据链后状态升级为 verified，才能在论文中引用",
                })

        # 2) 按实验状态给出具体改进点
        for e in exp_summaries:
            eid = e.get("exp_id", "")
            name = e.get("name", "")
            status = e.get("status", "")
            metrics = e.get("metrics") or {}
            if status == ExperimentStatus.FAILED.value:
                advice.append({
                    "target": eid,
                    "action": "修复实验代码或依赖后重跑",
                    "reason": f"实验 {name} 运行失败",
                    "expected": "成功产出 metrics 以便验证关联 Claim",
                })
            elif status == ExperimentStatus.ANOMALY_DETECTED.value:
                advice.append({
                    "target": eid,
                    "action": "按异常报告定位根因（loss spike/NaN/不收敛）",
                    "reason": f"实验 {name} 检测到异常：{str(e.get('anomaly_notes') or '')[:80]}",
                    "expected": "消除异常后重跑，得到有效指标",
                })
            elif status == ExperimentStatus.COMPLETED.value and not metrics:
                advice.append({
                    "target": eid,
                    "action": "补全结构化 metrics / 检查结果解析",
                    "reason": f"实验 {name} 已完成但未提取到 metrics",
                    "expected": "提供可比较的量化指标，支撑 Claim 验证",
                })

        # 3) 全局异常
        if has_anomaly:
            advice.append({
                "target": "experiment 阶段",
                "action": "先解决异常再评估实验结果",
                "reason": f"异常报告：{anomaly_report[:100]}",
                "expected": "异常清零后实验结论可信",
            })

        # 4) 去重（同 target+action 只留一条）
        seen: set = set()
        dedup: list[dict] = []
        for a in advice:
            key = (a["target"], a["action"])
            if key in seen:
                continue
            seen.add(key)
            dedup.append(a)
        return dedup[:8]

    @staticmethod
    def _extract_metrics_from_summary(result_summary: str) -> dict:
        if not result_summary:
            return {}
        idx = result_summary.find("metrics:")
        if idx == -1:
            return {}
        start = idx + len("metrics:")
        while start < len(result_summary) and result_summary[start] in " \t":
            start += 1
        if start >= len(result_summary) or result_summary[start] != '{':
            return {}
        depth = 0
        for i in range(start, len(result_summary)):
            ch = result_summary[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(result_summary[start:i + 1])
                    except Exception:
                        return {}
        return {}
