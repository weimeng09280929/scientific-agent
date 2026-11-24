"""experiment 阶段子图构建。"""
from __future__ import annotations

from core.orchestration.graph import Graph
from core.state.lifecycle import LifecycleStage

from stages.common import StageCheckpoint
from stages.experiment.agents import (
    AnomalyCheckAgent,
    ClaimVerifyAgent,
    ExperimentConfigAgent,
    ExperimentOutcomeAssessAgent,
    ExperimentReviewHuman,
    ExperimentRunTool,
)


def build_experiment_graph() -> Graph:
    """构建实验规划阶段子图。

    定位：系统只做实验的**分模块设计与任务规划**（配置 + 任务说明书打包交付），
    不代理用户跑实验，也**不再生成实验代码**——用户明确要求"实验不需要给代码，
    只需要给任务"：任务说明书把目标/步骤/判定标准写清楚，用户用自己的脚本
    或环境执行，跑完把结果回填 results.json。

    拓扑：
        ExperimentConfigAgent（规划实验配置：数据集/baseline/超参，分模块清单）
        → StageCheckpoint
        → ExperimentReviewHuman（人工审核：实验规划确认，确认后导出任务包）
        → ExperimentRunTool（导出任务包：experiment_plan.json + README 任务说明书）
        → AnomalyCheckAgent（检测规划异常：配置缺对照/指标缺失等）
        → ClaimVerifyAgent（标注实验与 Claim 的验证关系）
        → ExperimentOutcomeAssessAgent（评估规划完备度，决定是否进入 writing）

    检查点置于实验包导出前（人工确认前的关键决策点）。

    ExperimentReviewHuman 是实验包导出前的人工门控：用户确认后才导出。

    末尾的 ExperimentOutcomeAssessAgent 是 experiment → writing 阶段切换的决策
    口：实验是用户本地执行的——结果未回填/未成功时不进入 writing，由
    recommendation 决定等待结果或回滚到 ideation（实验驱动型论文：实验成功
    验证创新点可行后再写作）。
    """
    graph = Graph(name="experiment", stage=LifecycleStage.EXPERIMENT.value)

    # 节点（按拓扑序添加：首个为入口，末个为出口）
    graph.add_node(ExperimentConfigAgent("experiment_config"))
    graph.add_node(StageCheckpoint("cp_before_run"))
    graph.add_node(ExperimentReviewHuman("experiment_review"))
    graph.add_node(ExperimentRunTool("experiment_run"))
    graph.add_node(AnomalyCheckAgent("anomaly_check"))
    graph.add_node(ClaimVerifyAgent("claim_verify"))
    graph.add_node(ExperimentOutcomeAssessAgent("experiment_outcome_assess"))

    # 边
    graph.add_edge("experiment_config", "cp_before_run")
    graph.add_edge("cp_before_run", "experiment_review")
    graph.add_edge("experiment_review", "experiment_run")
    graph.add_edge("experiment_run", "anomaly_check")
    graph.add_edge("anomaly_check", "claim_verify")
    graph.add_edge("claim_verify", "experiment_outcome_assess")

    graph.validate()
    return graph
