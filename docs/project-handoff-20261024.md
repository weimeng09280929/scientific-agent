# Scientific Agent 项目接手理解报告（2026-10-24）

本轮仅只读检查仓库、文档、代码、发布引用与既有验收证据；没有修改产品代码、真实项目、服务进程或模型配置，也没有新增模型／文献源调用。本文把“已有实现”“离线检查”和“真实体验”分开，不能将其中一项代替另一项。

## 1. 产品目标与边界

Scientific Agent 是以中央科研对话为入口的理工科研究方案工作台，面向需要逐步引导的新手，也面向带着方向、论文或问题继续的用户。主线是明确条件、了解现状、确认问题、形成并审阅方案；用户可从任一步进入，进入阶段不证明前序工作已经完成。系统负责有界检索、按实际阅读范围整理、提出候选、保存可追溯材料及提示复核；研究问题、方法取舍、事实核对与正式采纳由用户决定。

公开产品执行上限为 DESIGN。计算、理论、实验三类方法均可形成**方案**，不执行实验、训练、设备操作或论文写作，不补造结果。历史实验、结果与稿件可读取和导出。研究缺口是待核实线索，`planReady` 表示材料依赖满足可审阅条件，不表示创新、科学正确或结论验证。当前是本地单用户体验；公网仍缺账号与项目权限、多租户隔离、HTTPS、调用限额、安全交付、备份恢复及跨进程任务能力。

## 2. 实际入口与请求链路

启动入口为 `python -m uvicorn web.api:app --host 127.0.0.1 --port 8001 --workers 1`。`web/api.py` 导入时调用 `runtime/cli.py::_load_env`；已有进程环境变量优先于 `.env`，任务路由取 `config/tasks.yaml`，模型注册见 `core/llm/registry.py`。`/` 与 `/workspace` 均返回 `web/static/index.html`。它实际加载 `conversation-home.js`、`teaching-cases.js`、`research-document.js`、`app.js`、`ui-experience.css` 等组件；`workspace.html/workspace.js` 不是主应用入口。

```text
主应用输入／明确操作
  → /api/conversations/start 首次建项目和会话，或复用已有归属
  → conversation_api / research_task_api / workspace_api 做意图、边界、归属检查
  → ConversationService 保存用户消息、request_id、依据快照和执行权
  → guide / guidance_policy / research_coach 或 plan_request 选择指导、检索、方案路径
  → LLMRegistry、受控来源工具与 AuditScope 记录逻辑调用及网络尝试
  → 来源状态、引用 ID／原文、结构、条件、操作和材料依赖校验
  → 原会话保存回复、任务和未采纳建议；失败保留原用户消息
  → 用户明确 apply 后以事务更新正式 WorkItem、focus／版本
  → workflow 投影当前可审阅状态；材料面板、独立页面和导出目录读同一项目材料
```

检索链在 `core/research_tasks/`：`detect.py` 判意图，`planner.py` 规划有界查询，`sources.py` 包装 `core/tools/` 的来源结果，`normalize.py` 去重，`ranking.py` 筛选，`synthesis.py` 整理及导出，`service.py` 持久化任务与交付。`SourceOutcome` 分开记录成功无结果、来源失败与解析降级；筛选排除也与未返回区分。候选、纳入综合、实际引用是不同集合；资料可能只有元信息、摘要、正文节选或全文，不能把入库或引用 ID 存在当作读过全文。`core/workspace/plans.py` 将陈述标为文献事实、用户提供、系统建议或未核实，并校验部分结构与原文存在性；它并非完整语义蕴含或科学验证器。

## 3. 项目、会话、任务与版本

“新研究”准备独立主题，首次明确发送才建项目；“在本项目新建对话”保留项目 ID，聊天历史隔离。`core/workspace/conversations.py` 的 SQLite 会话、消息、请求和建议表承载原消息、稳定 `request_id`、处理尝试与草案。正式 brief、文献、问题、方案与版本归项目共享；其他会话的未采纳建议不进入本会话历史或正式依据。普通聊天增加 `chat_revision`，正式材料写入增加 `Workspace.revision`；论文元信息和正文片段等外部变化另由材料指纹发现。旧依据草案保留可读，但 `basis_changed` 时采纳返回冲突，不能通过改版本号绕过。

方案先作为当前会话建议，明确采纳后成为与一个当前问题关联的正式 `WorkItem`；修订用 `supersedes_id` 保留旧版并增加正式 `plan_version`，失败的修订不替换 v1。`core/workspace/workflow.py` 是项目进度投影，聊天进入阶段和后台任务状态不代替它。`web/project_exports_api.py` 的材料目录、方案页及下载读取正式记录、原会话草案、报告任务版本和历史产物的真实状态；旧报告无编号时不补造 v1。

首次创建使用独立的 start 与 message 请求 ID。附件选择身份保护草稿槽，SHA-256 核实际字节，上传 `request_id` 保护幂等；创建或上传响应不明时查询原请求，刷新后需重选本地文件。迟到响应只作用于原项目／会话及原附件快照，不清掉新的输入或笔记。只读浏览不建库、迁移或补写历史。

两个教学案例由 `content/teaching-cases` 中的已发布版本包和 `core/workspace/teaching.py` 驱动；标准体验复用正常会话、来源、问题采纳、方案审阅与导出，模型和实时检索调用为零。案例进度、包版本与选择收据持久化，返回改问题／范围会留下旧产物及复核状态。转普通研究只带有出处的公开资料和参考笔记；教学条件、问题、方案不自动成为用户事实或正式主线。

## 4. 仓库、发布与服务核对

- 当前开发目录：`feat/code-update-v2`，HEAD `903d1fc`，相对 `origin/feat/code-update-v2` ahead 1；有大量已修改及未跟踪文件，含主要工作台代码与文档。未切换分支、reset、清理或提交。
- `publish/research-workbench-20261023` 与本地 `scientific-agent/main` 均指向 `d92b37f`；本轮 `git ls-remote scientific-agent refs/heads/main` 也返回完整提交 `d92b37ff07ff61507980ad3d0f67f33de99136fe`。远端状态只代表本次查询时刻。
- 对 `web/api.py`、四个主要 API 模块、主 `index.html/app.js/conversation-home.js/ui-experience.css`、调用审计、会话、方案、任务整理／交付及 `config/tasks.yaml` 共 15 个文件逐一比较发布提交与当前磁盘，归一化行尾后内容一致。这是定点比较，不宣称整个仓库完全一致。开发 HEAD 与发布提交处于不同历史，不能用 HEAD diff 推断发布功能缺失。
- 本轮只读端口检查：8001、8002 均无监听进程，因而无法确认当前有任何服务加载了哪一版。既有真实验收曾在 8002 使用新版，不能把当时状态当作现在的运行状态。
- 未发现适用的仓库或上层 `AGENTS.md`。

## 5. 能力与证据分层

| 层级 | 可确认内容 | 边界 |
| --- | --- | --- |
| 代码具备 | 多会话与共享材料、持久请求和显式采纳、有界检索与状态、三类方案、版本导出、两个版本绑定教学案例、DESIGN 执行限制 | 存在实现不等于每条真实路径稳定 |
| 离线验证 | 既有文档记录临时 FastAPI/SQLite、浏览器主应用、来源和模型替身回归；`release-review-20261024.md` 的 62 项回归＋2 项诊断探针通过 | 两个探针通过表示缺陷复现，绝非修复；本轮未重跑全部回归 |
| 真实验证 | `real-guided-user-experience.md` 记录受测试者辅助的联邦学习：6 次真实来源请求，20 篇入库，摘要范围阅读／讨论，正式问题、v1 方案采纳与下载；manifest 为 15 条消息、`planReady=true` | 第二版修订连接失败；没有全文阅读、科学结论验证或未经辅助的新手独立完成证明；费用未知 |
| 仍待验证 | 修订 v1→v2 的真实成功与重启后版本下载、理论和实验设计真实样例、真实 PDF 解析及复杂 Word/PDF 排版、真实新手理解、公网与多用户安全、长期并发 | 不能用单个样例或离线通过推广到全部领域 |

## 6. 四项已知问题的当前核实

1. **P1 原请求重试耗尽。** `core/invocation_audit.py` 的 `AuditScope` 默认同一 group 模型网络尝试上限 6；`core/workspace/conversations.py::_complete_attempt` 对同一会话与 `request_id` 建 operation，`retry` 复用请求；`core/llm/providers/openai_provider.py` 单次调用最多初次加 3 次重试。`_complete_attempt` 将 `CallLimitError` 归入通用 502 文案，`web/static/conversation-home.js` 对 failed/interrupted 一律显示“重试此消息”。既有真实会话 API＋SDK 离线替身探针为 4＋2＋恢复后 0 次传输，仍失败，原用户消息仅一条（见 `docs/release-review-20261024/probes.py`）。本轮代码定点比较确认相关路径与发布版一致；未新增网络复现。验收样例 v2 另有专用总预算与网关故障，不能混同根因。
2. **P1 方案依据归属。** `core/workspace/plans.py::validate_plan` 对章节 `basis=user` 只要求 quote 非空且出现在用户上下文；`conditions` 的 value/quote 则有更严格的逐字片段与否定限定校验，方法字段还检查典型 CPU/GPU 冲突。离线探针给“只有 CPU、没有 GPU”的真引文配上“已确认有 GPU”的反义正文仍被接受。真实 v1 方案另有将“建议 3–5 个种子”、脚本准备写入“用户提供”陈述的混写（见正式方案约第 99、141 行）；这是内容归属缺口，不能说已有引文校验能证明语义受支持。
3. **P2 单份报告来源映射。** `core/research_tasks/synthesis.py::report_markdown` 渲染代表工作和方法关系时保留 `cand-…`，没有在单份 Markdown 中附标题、作者、年份、DOI／URL 的完整对照；真实 `research-status-report.md` 第 37 行起可见。独立 `public-source-catalog.md` 可辅助核对，但不能代替报告自足。正式方案已有部分题名与 DOI，不应概括为所有导出无来源。
4. **P2 检索交付偏运行统计。** `core/research_tasks/service.py::_delivery_text` 主要交付查询、覆盖、入库数与限制；`_deliver` 将全部本轮新保存／关联论文 ID 放入消息引用列表，而非只列支撑正文要点的文献。真实逐轮对话中用户随后再问才取得较清晰的方法差异和研究问题解释。报告本身已有代表研究及局限内容，问题主要在会话首屏交付与引用语义，不应说系统完全没有研究分析。

## 7. 后续必须保护的契约与建议范围

保持 request_id 幂等、同会话互斥与跨会话隔离；错误、超时和中断须保留原消息并先查状态；预算、冷却和实际网络尝试审计不能被清零或隐藏。保留 start／upload 的身份、哈希和回执，草稿与迟到响应保护。保留正式采纳事务、材料 revision／fingerprint、过期 409、修订链与原版导出、只读旧库不迁移、历史材料只读和 DESIGN 上限。来源失败、成功零结果、筛选排除、候选／纳入／引用、元信息／摘要／正文／全文以及费用未知都要继续明确区分。

相关回归入口：`tests/test_project_conversations.py`、`tests/test_reliability_delivery.py`、`tests/test_research_plan_templates.py`、`tests/test_research_tasks.py`、`tests/test_second_batch_workspace.py`、`tests/test_product_plan_boundary.py`、`tests/test_research_teaching_cases.py`；前端路径看 `tests/conversation_home_browser.cjs`、`tests/attachment_selection_race_browser.cjs`、`tests/research_plan_browser.cjs`、`tests/research_tasks_browser.cjs`、`tests/workbench_material_navigation_browser.cjs`。复测须在临时项目和实际主应用进行，真实模型质量单列。

下一轮最小合理修改范围：先完成 **P1 失败恢复与方案依据归属**。在现有请求／审计接口暴露结构化失败原因及可重试性，对预算耗尽提供关联原请求的明确新一轮操作；保留累计上限与额外费用提示。随后强化章节陈述的事实归属，将用户原话与建议拆开，并加反义、额外数值／任务反例。P2 报告自带文献映射和交付要点可作为独立后一轮；修复后再按明确授权做真实 v1→v2 修订验收。本轮不实施上述修改。
