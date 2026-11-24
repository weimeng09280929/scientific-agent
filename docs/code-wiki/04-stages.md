# 04 · 阶段流水线与节点清单

> 依据：`stages/*/graph.py`、`stages/*/agents.py`、`stages/*/io_schema.py`、`stages/common.py`。
> 统计口径以**代码**为准。文末说明与历史文档的差异。

## 1. 生命周期主干

`core/state/lifecycle.py` 定义 5 个阶段：

```
research（调研） → ideation（思路） → design（方案） → experiment（实验） → writing（写作）
```

另有两条旁路：

- `topic_discovery`（方向推荐）：research 之前可选的前置入口；
- `discovery`（构效/关系发现）：research 之后的可选扩展。

`LifecycleStage` 提供 `ordered()`、`index()`、`next_stage()`、`prev_stage()`、`can_rollback_to()`（只能回滚到前序阶段）。

## 2. research（10 节点 · 文献调研）

拓扑（`stages/research/graph.py`）：

| # | 节点 | 类型 | 职责 | 主要写入键 |
| --- | --- | --- | --- | --- |
| 1 | `topic_refine` | agent | 主题精炼，生成关键词与检索策略 | `research.keywords`、`research.query_strategy` |
| 2 | `subquery_decompose` | agent | 拆为 5–10 个子问题（GPT-Researcher 思路） | `research.subqueries` |
| 3 | `cp_before_confirm` | checkpoint | 用户确认前快照 | `checkpoint.cp_before_confirm` |
| 4 | `topic_confirm` | human | 用户确认/修订检索方向与检索偏好 | `research.topic_confirmed`、`research.subqueries`、`research.search_prefs` |
| 5 | `paper_fetch` | agent | 按子问题并行检索（arXiv/S2/Sciverse 等），记录证据链 | `research.paper_metas`、`research.evidence_chain` |
| 6 | `paper_filter` | agent | 相关性打分与筛选 | `research.filtered_paper_metas` |
| 7 | `paper_ingest` | agent | chunk 切分、摘要、入库 | `research.paper_ids` |
| 8 | `material_extraction` | agent | 领域知识三元组抽取（对象-指标-方法/条件） | `research.material_knowledge` |
| 9 | `cross_validate` | agent | 多源交叉验证与可信度评分 | `research.cross_validation_report` |
| 10 | `research_gap` | agent | 结构化研究缺口（Gap）识别 | `research.gap_report` |

特点：文献抓取与相关性筛选分离（失败模式与重试粒度不同）；`research_gap` 是阶段出口，供 ideation 与 discovery 复用。

## 3. ideation（5 节点 · 思路生成）

| # | 节点 | 类型 | 职责 | 主要写入键 |
| --- | --- | --- | --- | --- |
| 1 | `brainstorm` | agent | 基于 Gap/冲突生成 3–5 个候选思路 | `ideation.idea_ids` |
| 2 | `idea_discuss` | human | 用户否决/修正/补充思路 | `ideation.discussion_notes`、`ideation.idea_ids` |
| 3 | `cp_before_validate` | checkpoint | 验证前快照 | — |
| 4 | `idea_validate` | agent | 可行性/新颖性/贡献度三维评估 | `ideation.validated_idea_ids` |
| 5 | `claim_draft` | agent | 从通过验证的思路派生 draft Claim | `ideation.draft_claim_ids` |

## 4. design（6 节点 · 方法设计）

| # | 节点 | 类型 | 职责 | 主要写入键 |
| --- | --- | --- | --- | --- |
| 1 | `atom_decompose` | agent | 原子概念分解 + 公式↔代码映射 | `design.atom_concepts`、`design.formula_code_map` |
| 2 | `method_formalize` | agent | 方法形式化（公式、伪代码、步骤） | `design.method_content` |
| 3 | `cp_before_review` | checkpoint | 审核前快照 | — |
| 4 | `method_review` | human | 用户审核方法 | 修订 `design.method_content` |
| 5 | `claim_evidence_link` | agent | Claim 抽取与证据关联 | `design.claim_ids` |
| 6 | `method_artifact` | agent | 生成方法文档 Artifact | `design.method_artifact_id` |

**产品边界**：`Pipeline.run_pipeline` 在 design 阶段成功后直接返回 `stopped`（`recommendation="review_plan"`），不会进入 experiment。

## 5. experiment（7 节点 · 实验规划）

当前实现**不生成/不执行实验代码**，定位是「分模块设计与任务说明书交付」：

| # | 节点 | 类型 | 职责 | 主要写入键 |
| --- | --- | --- | --- | --- |
| 1 | `experiment_config` | agent | 规划数据集/baseline/超参/模块清单 | `experiment.configs` |
| 2 | `cp_before_run` | checkpoint | 导出前快照 | — |
| 3 | `experiment_review` | human | 人工确认实验规划 | — |
| 4 | `experiment_run` | tool | 导出任务包（`experiment_plan.json` + README） | `experiment.experiment_ids` |
| 5 | `anomaly_check` | agent | 检测规划异常（缺对照/缺指标等） | `experiment.anomaly_report` |
| 6 | `claim_verify` | agent | 标注实验与 Claim 的验证关系 | `experiment.result_artifact_ids` |
| 7 | `experiment_outcome_assess` | agent | 评估规划完备度/是否可进入写作 | `experiment.outcome` |

`EXPERIMENT_OUTCOME` 是阶段切换决策口：`success`/`ready_for_writing` 为真才进入 writing；否则返回 `experiment_pending`（等待用户回填结果）或 `experiment_failed`。

## 6. writing（7 节点 · 论文写作）

| # | 节点 | 类型 | 职责 | 主要写入键 |
| --- | --- | --- | --- | --- |
| 1 | `provenance_check` | tool | 溯源链硬校验（未验证 Claim/未完成实验拒绝） | — |
| 2 | `style_learn` | agent | 学习目标写作风格 | `writing.style_profile` |
| 3 | `outline` | agent | 层级式大纲，每章关联 Claim/Experiment | `writing.outline` |
| 4 | `cp_before_draft` | checkpoint | 起草前快照 | — |
| 5 | `section_draft` | agent | 按章节撰写 | `writing.sections`、`writing.draft_content` |
| 6 | `review` | agent | 审稿视角提出修改意见 | `writing.review_notes` |
| 7 | `revise` | human | 用户确认终稿 | `writing.paper_draft_artifact_id` |

**产品边界**：`PRODUCT_PLAN` 下 writing 阶段不可达；历史写作材料可查看/导出。

## 7. discovery（6 节点 · 关系发现）

独立子图，复用 research 的 Gap 与论文产出：

| # | 节点 | 类型 | 职责 | 主要写入键 |
| --- | --- | --- | --- | --- |
| 1 | `hypothesis_seed` | agent | 从 Gap 生成候选关系假设 | `discovery.hypotheses` |
| 2 | `search_space` | agent | 定义变量范围/约束 + 文献数据点 | `discovery.search_space` |
| 3 | `cp_before_search` | checkpoint | 搜索前快照 | — |
| 4 | `llm_guided_search` | agent | LLM 引导搜索（MCTS + 代理模型融合） | `discovery.candidates` |
| 5 | `discovery_validate` | agent | 文献交叉验证 + 物理边界硬筛 + 新颖性 | `discovery.relationships` |
| 6 | `discovery_report` | agent | 结构化发现报告 Artifact | `discovery.report_artifact_id` |

入口：`Pipeline.run_discovery()`（产品策略下返回 `restricted`，历史报告可读）。

## 8. topic_discovery（4 节点 · 方向推荐）

| # | 节点 | 类型 | 职责 | 主要写入键 |
| --- | --- | --- | --- | --- |
| 1 | `trend_fetch` | agent | 获取论文数据并统计关键词年度频率 | `topic_discovery.trends` |
| 2 | `trend_analysis` | agent | 增长率计算与新兴/稳定/饱和分类 | `topic_discovery.analysis` |
| 3 | `topic_recommend` | agent | LLM 生成 3–5 个推荐主题 | `topic_discovery.recommendations` |
| 4 | `topic_select` | human | 用户选择主题，写回研究主题 | `topic_discovery.selected_topic`（并写 `research.topic`） |

入口：`Pipeline.run_topic_discovery()`；`auto_research=True` 时会继续进入 research。

## 9. 阶段间数据流

```
project.domain / research.topic
        │
        ▼
research ──► research.paper_ids / material_knowledge / gap_report / cross_validation_report
        │
        ▼
ideation ──► ideation.validated_idea_ids / draft_claim_ids
        │
        ▼
design   ──► design.method_content / claim_ids / method_artifact_id
        │
        ▼
experiment ─► experiment.configs / experiment_ids / outcome
        │
        ▼
writing  ──► writing.outline / draft_content / paper_draft_artifact_id
```

旁路：

- `topic_discovery` 写入 `research.topic`，可接到 research 前；
- `discovery` 读取 `research.gap_report` 与论文，产出 `discovery.*`。

所有键定义集中在 `stages/common.py`（39 个域键 + 8 个系统/全局键），禁止在节点里直接写字符串键。

## 10. 节点数量口径说明

| 阶段 | 代码当前（`tests/test_node_architecture.py` 断言） | `docs/node_architecture.md` 历史口径 |
| --- | ---: | ---: |
| research | 10 | 10 |
| ideation | 5 | 5 |
| design | 6 | 6 |
| experiment | **7** | 9 |
| writing | 7 | 7 |
| discovery | 6 | 6 |
| **合计** | **41** | 43 |

差异原因：experiment 阶段已从「生成代码 + 代码审查 + 执行」改为「实验规划 + 任务说明书导出」，`code_generate`、`code_review` 节点已移除。节点类别为 **28 Agent / 6 Checkpoint / 5 Human / 2 Tool**。`topic_discovery` 4 节点不计入主流水线。

## 11. 阶段 I/O 契约文件

| 阶段 | 契约文件 | 说明 |
| --- | --- | --- |
| research | `stages/research/io_schema.py` | 关键词、子问题、论文元数据、Gap、材料知识 |
| ideation | `stages/ideation/io_schema.py` | 思路、讨论笔记、验证评分、Claim 草稿 |
| design | `stages/design/io_schema.py` | 原子概念、公式映射、方法内容、Claim |
| experiment | `stages/experiment/io_schema.py` | 实验配置、任务包、异常、结果、outcome |
| writing | `stages/writing/io_schema.py` | 风格、大纲、章节、审稿意见、终稿 |
| discovery | `stages/discovery/io_schema.py` | 假设、搜索空间、候选、关系、报告 |
| topic_discovery | `stages/topic_discovery/io_schema.py` | 兴趣、趋势、分析、推荐、选择 |

修改节点输入输出时，**契约文件与 `output_keys` 必须同步**，否则 `test_node_architecture.py` 的数据流断言会失败。
