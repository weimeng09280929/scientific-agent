# 第二批：项目、对话、材料与任务的一致使用流程

日期：2026-10-23（Asia/Shanghai）。验收对象是当前未提交本地工作区，不是 GitHub 发布版本。仅完成第二批；没有开展第三批内容整理或第四批整体视觉改造。

后续独立复核发现 P1：迟到创建响应可能清除下一项新研究的同名附件元信息。已改为独立选择身份和请求快照，并补充 8 种竞态／恢复验证；详见 [专项修复说明](attachment-selection-race-fix.md)。原七条主路径、上传幂等及笔记保护本轮另行复跑通过；原通过记录不等于此前覆盖了该竞态，也不代替用户签收。

## 1. 用户现在怎样使用

- **开始新主题**：左侧“新研究”，不携带旧项目归属。输入兴趣、方向、问题，或者先选一份论文。领域尚未确认时只询问领域，不弹出整套讨论设置。确认提交之后才创建项目和会话。
- **继续同一主题的另一次讨论**：“在本项目新建对话”。清楚显示归属项目，沿用正式材料，但不带入其他会话聊天历史。点击入口、选文件、移除文件、取消领域确认均不创建数据。
- **找报告**：对话顶部“研究材料”中的报告入口，或“更多 → 研究现状”。任务报告与旧报告在同一目录中；来源对话、任务、时间、版本和部分完成标记保留。查看和下载使用同一个版本地址。
- **找方案**：“研究方案”默认展示当前已确认问题、正式方案及待审阅草案。草案回原会话审阅／明确采纳；正式记录在共享审阅组件打开。其他正式版本保留，旧方法文档、实验记录和稿件进入明确的历史区域，不要求新项目先面对空的实验模块。
- **看进展**：对话顶部区分“本次讨论”和“已确认进展”；其他页面顶部及项目管理显示正式材料推导的进展。任务活动显示每次指导、检索整理及方案请求的真实状态，可返回来源会话。任务完成不表示问题／方案已经采纳，更不表示结论经过验证。

保留深蓝、灰白风格。输入、研究问题、方案、来源和历史执行阶段仍使用原数据库及接口，不另建聊天、方案或主线系统。

## 2. 问题对应与完成状态

审查编号以 `user-experience-audit-20261022.md` 为准。第一批报告的表格编号已校正：推荐溢出为 01（两次请求），方案交付为 02，预算为 03，等待／筛选同属 14，终态残留为 15，笔记快捷键／切页为 30／31，晚到笔记响应为 32。没有因文档错号重新实现第一批。

| 原问题／本轮任务 | 修正与验证 | 状态 |
| --- | --- | --- |
| 04／A 入口归属含糊 | 全局新研究显式清空 project/conversation；项目内入口保留 project。原开始请求幂等机制不变 | 已完成 |
| 07／B 首次发送大表单 | 单独的简短领域确认；本地候选领域必须点击确认。指导方式、进入阶段仍分别保存 | 已完成 |
| 08／B 带论文开始 | 无项目可暂存 PDF/TXT/MD，显示待提交与移除。明确提交后创建归属并上传；失败留在原项目、原会话、原上传编号 | 已完成，文件对象不能跨刷新恢复 |
| 05／C 教学新对话死路 | 教学项目无会话页不展示必然拒绝的输入框，提供继续原案例／转入普通研究。交接资料未就绪时转换按钮禁用 | 已完成 |
| 06／C 转入普通项目空白 | 从持久化来源标记、实际论文及项目 brief 展示交接说明；教学问题和方案仍只是参考 | 已完成，无额外模型调用 |
| 09／F 名称不易辨识 | 新项目保守提取明确主题；泛开场句使用临时名称；对话用本轮讨论目标命名。只在首次创建使用，不覆盖手动名称、不批改旧名称 | 已完成，命名是本地启发式而非通用主题理解 |
| 10／D 对话有报告而页面没有 | 统一报告目录包含任务报告、各保存版本与旧报告；侧栏改为“已保存报告份数”，旧 report_gaps 字段保留 | 已完成 |
| 27／D 多套方案流程混杂 | 当前问题／正式方案／待审阅草案分清；历史方法、结果、稿件只读进入历史区域；不展示旧方法重生成来冒充正式方案更新 | 已完成，未重排整体阅读布局 |
| 11／E 活动漏掉对话任务 | 项目作用域聚合持久会话请求和检索任务，每次请求只一行；审计阶段、网络重试不另算任务 | 已完成 |
| 12／E 正式方案仍显示尚未启动 | 项目列表、顶部进展采用相同 workflow；旧执行 status/current_stage/stage_statuses 不改写 | 已完成 |
| 13／E 阶段含义不清 | 会话 entry_stage 仅标“本次讨论”；正式进展取 workflow，任务卡另表示运行状态 | 已完成 |

材料有效性、引用／归属、过期草案、正文片段变化、采纳事务和 DESIGN 上限均未放宽。

## 3. 统一读取契约

### 共同事实来源

`core/workspace/read_model.py` 只投影原数据：

- 正式主线使用原 `WorkspaceService.workflow()`；`flow.planReady` 仍要求当前问题、关联方案及依赖有效。
- 报告目录读取 `sra_research_tasks` 的报告版本元信息；不复制报告正文，不改变历史任务。原 `cross_validation_report` 继续作为旧报告读取。
- 草案从已有会话建议表读取，保留来源 conversation_id、suggestion_id 和依据指纹；材料页可发现项目内方案草案，但生成指导的上下文仍不混入其他会话历史。采纳必须回到所属会话走原接口。
- 活动按项目读取 `sra_chat_requests` 和 `sra_research_tasks`，核对会话项目归属。处理中／中断使用原锁探测，GET 不更新状态或自动重试。
- 项目列表只为当前分页及固定当前项目推导研究进展，不逐个请求全部会话；原活动排序、搜索、归档规则不变。

### 新增接口

能力声明：`GET /api/conversation-capabilities` → `workspace_read_model: true`。

```http
GET /api/projects/{pid}/workspace/materials?kind=reports&limit=20&offset=0
GET /api/projects/{pid}/workspace/materials?kind=plans&limit=20&offset=0
GET /api/projects/{pid}/workspace/materials?kind=history&limit=20&offset=0
GET /api/projects/{pid}/activities?limit=20&offset=0
```

单页 1—50 条。材料返回 `entries/next_offset`，方案草案另有 `drafts/draft_next_offset`；使用同一 offset 时，两者按各自游标判断是否仍有下一页。返回 `workflow`、`progress` 和 `material_counts`。活动只给用户任务，不将审计明细合并为任务行。

```json
{
  "project_id": "…",
  "progress": {
    "label": "方案可审阅",
    "planReady": true,
    "needs_review": false,
    "product_stage": "design"
  },
  "material_counts": {"research_reports": 1},
  "entries": [{
    "key": "research-task:…",
    "task_id": "…",
    "conversation_id": "…",
    "version": 1,
    "status": "partial",
    "status_label": "部分完成",
    "url": "/api/projects/…/research-tasks/…/export?version=1",
    "formats": ["md"]
  }]
}
```

`material_counts.research_reports` 计“有已保存报告的任务＋旧报告”，不累计版本、不拿候选问题数或入库论文数代替。共享 workspace 和 status 返回同一计数。旧 `counts.report_gaps` 继续保留给旧调用方，语义不改成报告数。

产物目录、材料页、来源对话都复用原导出地址。旧版报告没有数字版本时保持未标版，不补造 v1。旧方案无结构字段仍能读取、导出；正式方案与历史／其他方案不混作当前版本。

任务活动默认显示指导／检索／方案请求，真实状态限于各请求实际支持的状态；有活动中的任务时只读刷新，终态不再安排轮询。失败重试回到原会话核对材料与原 request_id，不转接到旧 `/run`。旧执行器只放在独立历史说明中，保留位置、待确认入口及产物读取，不提供含糊的“继续当前任务”。

## 4. 首次创建与上传恢复

1. 浏览、输入、选择／移除文件只改变本机草稿，不创建项目。
2. 明确发送后，领域需要确认时显示短确认框；取消仍没有项目。
3. 用已有 `/api/conversations/start` 和稳定创建 request_id 建立归属，再使用独立上传 request_id 上传到原 `/upload-paper`。
4. 文件身份包含内容 SHA-256，而不是仅文件名。响应不确定时保留身份；再次明确继续先查询原 `/upload-requests/{rid}`。已完成直接使用原 paper_id，不重新解析。
5. 上传确认后才发送原消息；创建成功但上传失败，保留项目、会话、原消息快照和输入，后续不会再建项目。上传处理中不冒充完成。
6. 文件对象只保存在当前页面进程。刷新可恢复文件名、哈希、请求编号、文本和归属，但会明确要求重新选择同一文件；不会承诺恢复磁盘文件访问权。
7. 用户完成一次导入后主动另行选同一文件，会作为一次新导入，不一律禁止重复资料。已有上传请求不能携带不同内容绕过冲突校验。

旧项目、当前会话、新研究各自保留草稿。创建／上传迟到响应只操作原视图及匹配快照，不跳到别的项目或清掉新输入。笔记仍沿用第一批的项目草稿与提交快照保护。

## 5. 七条连续浏览器路线

实际 FastAPI 主应用、临时 SQLite、Edge 1440×900 和 390×844。模型 SDK 及来源出口是离线传输响应，主要项目、会话、任务、采纳、上传、下载接口未被伪造。

| 路线 | 本轮实际结果 |
| --- | --- |
| 1 从零开始 | 首页输入 → 单领域确认 → 创建响应已提交但丢失 → 刷新、原号继续 → 一项目／一会话／一用户消息；项目主题与对话目标分开 |
| 2 带论文开始 | 选择、移除、取消确认不建项目；明确提交后故障注入一次 503，原项目重试成功。另测上传已保存但响应丢失＋刷新＋重新选文件：仅一次解析、一份资料、一条消息 |
| 3 项目内多对话 | 新讨论保留当前项目，旧消息不混入；切 A/B 和返回后文本草稿独立，论文与正式材料共享 |
| 4 教学转普通研究 | 教学无会话页没有不可发送的输入框，可回原案例；创建普通研究后有来源和用户条件说明，问题／方案仍未确认。交接慢读取期间不允许提前点击转换 |
| 5 报告一致 | 对话完成部分检索整理 → 研究现状同一 task/version/status → 实际下载 MD → 任务活动同一任务 → 原会话；侧栏显示 1 份报告 |
| 6 方案一致 | 明确采用候选问题 → 离线边界生成草案 → 共享组件审阅并手动采纳 → 方案页／项目列表／顶部／活动一致；修改条件后显示待复核，旧记录保留 |
| 7 历史与异常 | 深链接、前进／后退、刷新、失败返回原会话、跨项目与缺失库保护；旧库读取不转换。另测普通请求运行中离开对话，在活动页变为完成，原消息／回复各一条 |

证据：[七路径结果](second-batch-workspace-unification/browser-results.json)、[上传与迟到创建](second-batch-workspace-unification/recovery-results.json)、[活动终态](second-batch-workspace-unification/activity-results.json)。

### 测试结果（不累加重复用例）

- 相关会话、检索任务、方案、教学、可靠性、计数及新专项组合：**215 通过**，见 `regression-results.xml`。
- 最终新增版本目录测试后专项：**11 通过**，见 `targeted-results.xml`；与上组有重叠。
- 收尾材料读取／精确版本导出／导航计数组合：**18 通过**，见 `final-read-model-results.xml`，同样与上述有重叠；包含旧 JSON 对话中 45 条方案草案的只读分页，不补造依据快照。
- DESIGN 边界及第一批交付保护：**66 通过**，见 `boundary-delivery-results.xml`。
- 前期开始对话、153 项目分页、产物、边界、第一批组合 **97 通过**，属于本轮前期检查，部分又包含在上述复测中。
- 第一批笔记浏览器回归通过：快捷键一次提交、切页刷新、跨项目、保存中输入、迟到响应及失败输入保留。
- Python 编译、实际加载 JavaScript 语法与相关 tracked 文件 diff 空白检查通过。环境没有 Black，未安装新工具，也未宣称 Black 格式验收通过。

仍有既有 datetime.utcnow 弃用与 pytest asyncio_mode 配置警告。任务组合中 `test_resume_failure_keeps_old_report` 本轮通过，不声称历史偶发问题已永久根治。

过程失败如实记录：早期新夹具误用 send 参数、只读 store 写入及 PaperChunk 字段，已按真实类型契约纠正；加入任务归属 JOIN 时发现排序列歧义，修正别名并保留分页与归属断言。旧计数静态测试改为报告数契约，同时保留旧 report_gaps HTTP 断言。慢交接真实发现的按钮竞态已修复。一次活动脚本使用错误的 CSS 类名，并发共享离线 gate 也干扰了走查，最终修正脚本、串行执行并通过；没有放宽业务断言或用强制点击掩盖遮挡。

## 6. 关键截图与人工路线

截图已实际打开检查，不只读取 pageerror；阶段性 `failure.png` 等是排查证据，不是最终通过截图。

- [新研究／桌面](second-batch-workspace-unification/new-research-desktop.png)
- [带论文／桌面](second-batch-workspace-unification/paper-start-desktop.png)
- [教学交接／桌面](second-batch-workspace-unification/teaching-handoff-desktop.png)
- [研究现状同一报告／桌面](second-batch-workspace-unification/report-desktop.png)
- [研究现状／390px](second-batch-workspace-unification/report-mobile.png)
- [正式方案／桌面](second-batch-workspace-unification/plan-current-desktop.png)
- [条件变化后待复核／390px](second-batch-workspace-unification/plan-mobile.png)
- [活动列表／390px](second-batch-workspace-unification/activities-mobile.png)
- [活动从运行到完成／390px](second-batch-workspace-unification/activity-completed-mobile.png)
- [上传丢响应恢复／390px](second-batch-workspace-unification/upload-recovered-mobile.png)

五分钟人工路线：点新研究 → 选一份 TXT/MD 或输入兴趣 → 确认领域并发送 → 在本项目新建对话检查材料共享／聊天独立 → 从材料入口看报告与方案 → 任务活动返回对应会话。也可从案例目录进入原案例，再点项目内入口看到“继续案例／开始自己的研究”，转入后核对交接说明。方案采纳必须自己点击，不能只输入“采用”就算成功。

## 7. 实现文件与兼容

主要文件：`core/workspace/read_model.py`、`workflow.py`、`teaching.py`；`web/project_exports_api.py`、`workspace_api.py`、`conversation_home_api.py`、`api.py`；实际主应用的 `index.html`、`conversation-home.js`、`teaching-cases.js`、`workspace-overview.js`、`project-library.js`、`app.js` 与少量配套样式。

没有修改未使用的 workspace.html/workspace.js，没有新数据库、迁移或后台队列。HTTP 读取不建库、不建表、不恢复执行。缺失库返回 404，损坏／不可读取给出错误，不创建替代项目。版本、引用、指纹和历史执行阶段不批量改写。历史材料仍沿用已有导出接口，未生成实验结果或稿件。

## 8. 限制、服务状态与下一批约定

- 本轮真实模型 HTTP **0 次**、真实外部文献来源 HTTP **0 次**；离线 usage 只是夹具字段，不能当成真实 token 或计费。本轮没有新的真实费用数据，历史费用仍未知。没有借用第一批真实样例把本轮离线验收包装成真实全链路成功。
- 实际下载并检查 Markdown；Word/PDF 复杂公式与排版未重验。桌面模拟窄屏不代表真实手机软键盘已验收。
- 报告／产物目录在服务端聚合现有元数据后分页；大规模单项目产物目录尚未做压力验收。会话及任务有上限分页；没有扫描整机寻找项目。旧 JSON 聊天的只读兼容仍需解析原 JSON，这不是本轮新增迁移。
- 简短命名是保守启发式，不能保证总能提取最理想主题；手动修改仍然有效。模板、引用措辞、长回复、菜单和阅读布局继续留给第三、四批。
- 旧流水线在活动中提供独立只读历史说明，不提供含糊的一键重跑；现有待确认汇总、详细材料与导出仍可访问。新的会话失败重试只能回原会话，不调用旧启动入口。
- 本轮只使用 8065 隔离实例和临时项目；8001 未被启停，检查时无监听。最终隔离实例已通过本轮专用 shutdown 接口关闭，并复核不再监听；磁盘代码变化不表示用户主后端已自动加载。隔离实例的临时目录由验收服务退出时清理，截图、测试 XML 与结果 JSON 留在本报告目录。
- 未删除、归档、迁移或批量重命名真实项目，未提交、推送、部署。用户体验认可仍待用户本人确认。

第三批可直接复用：统一材料条目的 key/url/version、来源 task_id/conversation_id、project progress、material_counts、原建议依据及采纳接口。内容整理不能改变这些身份与状态，也不能将候选／教学参考升级为正式证据。第二批到此停止。
