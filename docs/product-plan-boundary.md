# 研究方案流程边界（第一步）

本步只统一执行边界与状态判断，不重做页面布局，不扩展领域，不迁移项目数据。

产品流程：明确目标与条件 → 了解研究现状 → 确认研究问题 → 形成并审阅方案。

## 1. 执行约定

- `Pipeline()` 默认使用 `ExecutionPolicy.PRODUCT_PLAN`，真实调用、规则和演示配置均最多运行至 `LifecycleStage.DESIGN`。
- 设计结束后返回 `stopped`，不将实验阶段标记为开始，不构造实验图、不导出新实验任务包，也不生成稿件。
- 调用方要求更早停止时仍然有效。`force_writing` 不允许突破边界；网页返回 422，Pipeline 抛出明确的参数错误，CLI 给出提示并退出。
- 内部五阶段架构测试必须在 Python 中显式传入 `ExecutionPolicy.INTERNAL_LEGACY` 枚举。HTTP、环境变量、CLI 参数、字符串策略值均不能选择它。
- `run_discovery` 旧子流程在默认产品策略下受限；选题探索仍可用，确认后交接到同样受限的主流程。
- 方法重新生成是独立的方案修订入口，只增加方法版本，不启动后续流程，也不自动采纳为当前主线。

`StageResult.stage` 始终为 `LifecycleStage`，新增的 `status="restricted"` 携带 `product_boundary`。所有阶段调用方只有收到 `success` 才能计入完成；受限或未知状态不能被当作成功。

## 2. 历史项目

`core/product_boundary.py` 直接只读核对快照、执行上下文、节点历史和待确认节点，不通过恢复函数来判断边界。

- 实验／写作已有实际阶段状态、节点或上下文标记：执行入口返回受限状态，网页导航为 `view_only`，不创建线程，不恢复、回滚、修复提纲或重审结果。
- 设计已完成、实验为 `not_started`：识别为已有方案，不因为 `current_stage()` 指向首个未开始阶段而判为已进入实验。
- 损坏或无法核对的检查点：关闭执行并返回 `checkpoint_unreadable`，不尝试修复写入。
- 历史文献、实验、结果、稿件、建议和版本仍保留；原有查看、下载和导出入口继续可用。

本步没有数据库迁移、批量状态改写或数据补造。受限请求的离线用例比较了请求前后的项目文件哈希，包括数据库、上下文、阶段快照和产物。

## 3. 统一状态接口

`GET /status`、`GET /dashboard`、`GET /workspace` 与 `POST /resume` 复用服务端主线判断。

| 字段 | 约定 |
| --- | --- |
| `product_stage` | 产品步骤投影：`literature`、`question` 或 `design`，不代替历史生命周期状态。目标与条件仍保存在已有 `brief` 中。 |
| `product_boundary.max_stage` | 固定为 `design`。 |
| `product_boundary.restricted` | 是否禁止继续该历史执行位置。 |
| `product_boundary.reason` | 如 `within_product`、`review_required`、`plan_ready`、`design_generated`、`legacy_stage_restricted`、`checkpoint_unreadable`。 |
| `workflow.flow.planReady` | 当前问题与明确关联的方案及其来源／依赖均有效，可审阅方案。多问题共用的历史方案须先复核。 |
| `workflow.flow.recordsReady` | 保留旧的结果／判断记录衔接含义，不拿它冒充方案完成或科学验证。 |
| `workflow.continuation` | 网页应使用的统一下一步；保留 `action`、`page`、`hint` 及相关记录定位字段。 |
| `current_stage`、`stage_statuses` | 状态接口保留历史执行值，不为符合新产品流程改写。 |

推进顺序：先复核问题与来源，再确认方案关联和方案复核。只有依赖有效才返回 `plan_ready`，定位现有方案页面，供审阅、修改、导出；不启动线程。

只有后台方法材料、尚未确认问题和方案关联时，返回材料确认方向，不自动采纳。历史受限项目的继续动作为 `view_only`。原始步骤统计仍保留，主线步骤只到问题与方案，旧结果步骤放在 `historicalSteps` 中。

方案已整理、后台任务结束、历史记录完整，均不表示实验完成、研究完成或结论得到验证。

## 4. 写入边界

以下产品请求返回 HTTP 422 和原有兼容的 `detail` 提示，且不写入项目内容：

- `/experiments/backfill`；
- `/workspace/items` 新增或修订 `result`、`finding`、`outline`；
- `/workspace/link-material` 重新关联 `experiment_result`；
- `/workspace/suggestions/{id}/apply` 采纳旧结果、判断或写作建议；
- `/workspace/guide` 的写作请求，以及模型返回包含后续记录类型的建议；
- 实验／写作旧人工节点的确认、回滚和续接请求。

请求模型保留历史字段，便于旧客户端得到明确限制而不是结构不兼容错误。内部历史存储契约仍供夹具验证；公开接口不再通过它增加后续科研结果。

## 5. 验证范围

测试使用临时项目、离线模型响应和检索夹具，包含实际阶段图、人工确认、选题交接、默认 CLI、单阶段、恢复、历史只读导出、版本与事务保护，以及独立临时服务的浏览器操作。

本次最终结果：242 项相关 Python 回归通过，44 项 JavaScript 状态检查通过；独立临时服务上的浏览器验收通过，未出现页面脚本错误。浏览器测试服务已关闭。Python 回归仍有既有弃用警告，未将其当作失败或隐去测试断言。连接释放另有提交／版本冲突与只读检查的专项断言。

`tests/test_product_plan_boundary.py` 为本步的专项验收用例；`tests/unified_workflow_browser.cjs` 验证关联已有材料、方案导航、后续写入拒绝、问题切换与接口一致性。旧的完整流水线测试显式选择内部策略，保留原有真实性和版本保护断言。

PowerShell 下独立复测专项用例：

```powershell
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
$env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest tests/test_product_plan_boundary.py -q
```

两个 `SKIP` 标记只用于隔离测试进程的主服务守卫与存量项目扫描，不能启用内部流水线或放开产品阶段上限。

这些验证不等于真实模型质量、公网检索或生产部署验收。本步未调用付费模型、未执行实验／训练，未重启用户主服务，未提交或推送代码；后续可依赖本页的边界与接口约定继续改造。
