# 普通文字对话移动到项目（本地离线交付）

本轮只增加同账号、已有项目之间的普通文字对话移动。没有新增归档、删除、长期记忆、模型角色或部署功能；原文件夹导航、项目总览、首页说明和现有项目名称保留。

## 实施起点与实际数据关系

- 分支 `feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`。开始时已有 353 条修改／未跟踪条目，全部保留；未切换、重置、清理、提交或推送。检查仓库和上级目录，没有适用的 AGENTS.md。
- 会话实际保存在各项目 `knowledge.db`：`sra_conversations`、消息、请求、失败原因、建议和调用审计；并非单个可改的 `project_id`。检索任务、报告、论文和正式材料还有项目级关系。
- `.conversation-starts.db` 保存按账号隔离的首次创建幂等记录；账号库的 `project_owners` 决定所有权。移动不修改账号、项目归属或该首发记录的身份。
- 执行中的会话持有既有 OS 执行锁，调用预算由持久化审计计算。移动复用会话锁及项目归档共用的进程锁；不使用浏览器选择的用户身份，不启动模型或检索。

## 支持范围与用户入口

账号模式下，最近对话、项目文件夹的对话菜单及当前对话菜单，使用同一个“移动到项目”选择界面。目标只列当前账号的其他已有项目，按需分页；名称、创建时间和项目编号尾部区分同名项目。

支持已有保存消息、请求均已确定终态、无关联科研记录的普通文字对话，包括只有项目简介语境的讨论。后者不因读取过简介而被一律拒绝。完成、失败和取消的历史请求可保留，但移动前的失败请求只能查询历史，不能直接在新项目重试或开启新轮次。

预检查实际扫描所有带 `conversation_id` 的来源表和消息／请求结构。以下情况明确阻止：

- processing、interrupted 等尚未核对的请求，或底层仍持有执行锁；
- 引用论文、已选附件／材料、方案目标、科研建议（包括笔记建议）、草案、已采纳关联、检索任务及报告；
- 未识别的会话关联表、引用操作或审计父关联，不能丢弃后移动；
- 使用跨请求／上层预算组的对话，不能拆分或重置预算后移动；
- 教学案例、旧项目级兼容对话、目标未转换的旧聊天、存储版本不兼容；
- 会话／创建请求／消息／审计 ID 冲突；单类历史记录超过 1000 条；
- WAL、内存或无日志等本实现不能保证跨库原子提交的模式；不通过切换日志模式来强行移动。

本轮支持的是普通文字对话，不表示所有历史对话均可搬迁。

## 接口及写入边界

| 接口（`/api/projects/{project_id}/conversations/{conversation_id}` 下） | 行为 |
|---|---|
| GET `/move-precheck?target_project_id=…` | 两库只读；关联、材料、身份与执行状态检查，返回原因列表及核对指纹 |
| POST `/move` | 须有目标、原移动 request_id、expected_token 和 acknowledge_history=true；重新检查后提交 |
| GET `/move-requests/{request_id}` | 查询同一移动是否已完成，以及当前归属；不会补执行移动 |
| GET `/location` | 沿回执解析现归属，每个项目均检查当前账号权限 |

四条新路由沿用统一认证、归属、CSRF 和禁止缓存策略。非所有者／匿名／禁用账号的真实路由覆盖纳入 `conversation-moves/route-coverage.json`（75 个项目路由方法，分别要求 404／401／401）。账号模式之外不开放移动。

进入界面、选择、预检查和取消不建立回执、不创建会话、不中断任务。提交前必须核对以下说明：

> 历史消息会保留。移动后，新的讨论使用目标项目的资料；原项目已确认的研究条件和成果不会因此成为目标项目的正式记录。

草稿文字、待上传文件、准备中的材料目标或未确认的科研请求存在时，先回原对话处理，不自动发送或搬移。提交时再次检查本页面状态。其他标签页的草稿不由服务端读取；它们以后操作时会得到明确的新归属提示，内容留在账号内的旧项目／会话草稿槽。刷新后无法重建的 File 仍沿用原提示要求重新选择。

## 数据、一致性及恢复

1. 确认时，按固定项目顺序获取来源及目标的同 CID 会话锁；同账号发送／重试不能与移动抢占执行权。全局短锁与项目归档互斥，事务内再次检查所有权。
2. 对源库使用 `ATTACH` 加入目标库，`BEGIN IMMEDIATE`、两库 `synchronous=FULL`，只接受 rollback journal。目标建必要的现有会话表、复制、来源删除、两端写入回执在同一 SQLite 多库事务内完成。没有跨库先删后复制的文件操作。
3. 会话 ID、标题、时间、设置、chat_revision、创建身份及哈希保留；消息 ID、原 JSON、顺序及时间逐字保留。目标 `seq` 是本库分页序号，可能重排，原序号保存在回执；不能沿旧项目使用旧分页游标。
4. 请求 ID、payload/hash、attempt、状态、失败原记录、审计 ID、额度及成本记录复制保留，仅所属项目列改变。跨请求预算组直接拒绝，因此不转移半个预算组。没有清空计数或调整调用上限。
5. `sra_conversation_moves` 是两端同事务保存的版本化来源回执，保存移动身份、创建身份、代次、项目名／ID、消息与请求来源、原序号和移动时来源简介快照。不是第二套可继续发送的会话。源项目列表不再有实际会话行。
6. 请求重复提交先查原回执，原编号不能换目标。响应丢失后，浏览器保存原移动编号，手动查询原结果；查询不依赖项目列表是否已加载。版本或关联变更须重新预检查和明确确认，不后台接受新依据。数据库／文件错误回滚并返回 503，保留编号核对；409 和 422 仍区分版本冲突与不支持。
7. 原首发幂等记录保留原项目关联，旧地址／旧创建身份通过回执识别已移动，不会重建空会话。旧标签页发送、重试及恢复不会调用模型；返回 409 和经权限核对的新位置。多次移动与移回原项目可解析，链解析有 32 次上限；过长／损坏会明确失败。
8. 普通写入异常由 SQLite 回滚；进程突然退出由 rollback journal 恢复。实例取得现有数据目录租约后，在明确启动阶段检查并恢复非空项目事务日志，随后重读目录；GET 不初始化历史库，也不自动重放科研请求。故障注入包含写入三个阶段抛错以及子进程在来源删除后 `os._exit`。

移动后的旧消息通过只读展示投影标注“历史讨论 · 原项目”，原消息 JSON 不改写。旧操作卡片保存原文，但显示为不可用；直接调用检索卡片 API 同样拒绝。旧失败记录原 reason 保留，附 `recovery_blocked_reason=moved_history`，can_retry/can_restart 为 false。

新的模型上下文以目标项目当前材料构建；旧消息不放入用于现条件依据校验的当前 history，而单独放入注明原项目、仅供历史参考的 prior_project_history。未修改科研提示词、模型路由或事实校验。来源简介快照是移动时的快照，不宣称还原每条历史请求的完整模型输入；原指纹、审计与历史文字继续保留。目标简介、正式问题、方案、版本、指纹和采纳状态均不因移动而写入。

## 备份兼容

冷备份契约升为 v2，校验两端回执一致、同账号归属、会话唯一可写位置、可解析的移动链，以及旧首发幂等关联。恢复保留回执和预算，仍清除旧登录会话、不重放请求。

当前工具继续读取没有移动记录的 v1 备份；含移动记录不能伪装为 v1。恢复副本验证了重新登录、双账号隔离、历史消息相同、原创建请求不重放和继续正常讨论；源目录清单未改变。外账号目标的损坏回执被备份预检查拒绝。

## 修改文件

- `core/workspace/conversation_moves.py`：预检查、跨库原子移动、回执、定位、启动日志恢复。
- `core/workspace/conversations.py`：旧入口保护、只读历史标识、旧请求恢复约束、目标上下文。
- `core/research_tasks/service.py`、`web/research_task_api.py`：移动前卡片的服务端约束及旧归属错误。
- `web/conversation_move_api.py`、`web/conversation_api.py`、`web/conversation_home_api.py`、`web/api.py`：受保护路由、能力、错误契约、短锁、启动恢复。
- `core/local_backup.py`：契约与移动关系校验。
- `web/static/conversation-home.js`、`web/static/ui-experience.css`：统一移动界面、草稿与响应恢复、历史提示、窄屏样式。
- `tests/conversation_move_fixture.py`、`test_conversation_moves.py`、`conversation_move_test_server.py`、`conversation_moves_browser.cjs`：临时数据及离线验收。

## 验证证据和失败记录

最终去重 **131 个 pytest 用例全部通过**（21 个新增专项、110 个既有回归）；浏览器 **9 条流程通过**，单独计数。失败记录保留，中间复跑不累加。最终依据为 `conversation-moves-final-special-rerun.xml`（21/21）、`conversation-moves-navigation-regression.xml`（62/62）、`conversation-moves-isolation-backup-regression.xml`（47 通过、1 失败）与 `conversation-moves-final-api.xml` 中该路由用例的最终通过记录；最后一份包含重跑的 16 项专项，不重复累计。

- 接口／存储专项 21 个去重用例：普通多轮移动、只读检查、7 类阻止条件、身份及材料变化、三个阶段回滚、真实发送锁、并发重复确认、失败恢复冻结及卡片直调、三次移动及移回、进程异常退出、备份恢复、同名对话、v1 兼容、损坏归属。
- 必要旧回归 110 个去重用例：导航／首页／总览／文件夹／P1 共 62；账号路由与会话 3；冷备份恢复 45。路由覆盖那一个最终单独复跑，不额外计数。
- 浏览器按 9 条流程单独记录，不并入 pytest：只读／取消、草稿附件、成功移动、旧标签页及刷新／前后退、复杂关联、版本变化、响应丢失与刷新恢复、390px 与键盘、独立双账号。
- 诊断及编译检查不算新增业务用例。没有纯内存结果替代实际 SQLite、FastAPI 或浏览器操作。

保留的中间失败：

1. 初始检查把普通回复自带的无绑定 start_search 卡片也当成关联，已改为允许保存但移动后在前后端禁止直接执行；关联卡片仍阻止。
2. 旧 API 投影临时字段与持久化原文的对比混用，改为额外比较实际 message_id/data 原始字节，不忽略原文变化。
3. 新测试曾使用错误的期望 HTTP 成功码、首发内部创建 ID、配置类型和备份封印文件名，已按现有契约修正，业务断言保留。
4. 首次浏览器脚本在预检查完成前使用键盘确认、恢复时在列表载入前点击，留下失败截图；改为等待实际完成，同时修复产品中原请求查询与列表加载的耦合。
5. 全路由回归发现新路径参数名与原有统一名称不一致，已统一为 conversation_id/request_id；不修改旧测试或放弃覆盖。
6. 后续错误包装误捕获 WorkspaceError（继承 ValueError），使明确拒绝成为 503；已优先保留领域错误，专项复跑核对 409／422。

既有 asyncio_mode 配置告警、datetime.utcnow 弃用告警保留，未在本轮顺带调整。

## 示例、截图与人工路线

最终隔离实例为 `%TEMP%/sra-move-cc4f10b3`，结果在 `conversation-moves/review-final/browser-results.json`。账号邮箱仅为临时测试标识，未验证。没有截图密码、Cookie、令牌或密钥。

成功：`proj_2403d01985555d96964f9960f3e21f6b` 的“同名讨论”（`5dd98275174749f1ac5e75a4776f1559`）移到同名项目 `proj_20261029_234817_d0d6da`，源列表减少一个、目标仅出现原 CID 一份，历史来源可见，实际模型调用数不增加。阻止：“关联草案，不能移动”（`b063a8cecfb54238b4814be528d44999`）关联未采纳建议，服务端返回原因且确认按钮不可用，源记录仍存在。都是临时样本，不是用户真实项目。

截图及结果见 `conversation-moves/review-final/`：`confirm-desktop.png`、`moved-desktop.png`、`blocked-complex-desktop.png`、`draft-protection-desktop.png`、`confirm-390px.png`、`browser-results.json`。已实际查看截图后再确认布局；中间失败截图在其他子目录，不作为最终通过证据。

复现（PowerShell，在仓库根；使用本轮测试虚拟环境）：

```powershell
$sraMoveRoot=Join-Path $env:TEMP ('sra-move-'+[guid]::NewGuid().ToString('N').Substring(0,8))
venv/Scripts/python.exe -m tests.conversation_move_test_server --root $sraMoveRoot --port 8098
```

上述专用夹具拒绝产品端口、既有根目录和非规定临时路径，只绑定回环，所有模型／解析／检索边缘均为离线替身。另开终端：

```powershell
$env:NODE_PATH='C:\Users\czh05\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:SRA_MOVE_OUTPUT='docs/conversation-moves/manual-rerun'
node tests/conversation_moves_browser.cjs
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8098/__move__/shutdown
```

自动夹具使用专用临时账号。人工路线：使用临时数据中的两个账号登录两个独立浏览器上下文；A 打开普通历史对话，先输入文字／选文件核对阻止移动与保留，再自行处理草稿；通过菜单选择另一个同名项目，核对编号、预检查并确认。核对文件夹／最近条目／当前对话同 CID；在旧标签页发送应被拒绝且文字保留。再对关联草案做预检查确认原因；B 查看自己的列表，并访问 A 的已知对话 URL 应不可用。管理命令仅通过隐藏交互输入密码，临时实例运行前创建额外测试账号可用：

```powershell
venv/Scripts/python.exe -m tools.local_accounts --db "$sraMoveRoot/accounts.db" create manual-review@example.com
```

无需公开夹具的密码。工具目录中的固定测试凭据只用于自动化；本报告不展示。

专项命令：

```powershell
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_conversation_moves.py
$env:SRA_AUTH_MODE='local'; $env:SRA_WEB_SKIP_GUARD='1'; $env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_project_navigation.py tests/test_conversation_home.py tests/test_p1_recovery_attribution.py tests/test_project_overview.py tests/test_project_organization.py
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_local_account_isolation.py::test_accounts_and_routes tests/test_local_account_isolation.py::test_real_resources_and_every_project_route tests/test_local_account_isolation.py::test_csrf_cookie_session_rotation_and_restart tests/test_local_backup_restore.py
```

## 运行边界与剩余限制

- 只使用 `%TEMP%/sra-move-*`、pytest 和既有回归建立的临时账号／目录；本轮隔离端口为 8098。未访问／修改真实账号与项目，未启停或占用 8001／8002／8077。没有额外删除或清理目录；既有 TemporaryDirectory 回归只自行回收各自本次新建目录，此前安全策略拒绝的目录未处理。
- 真实模型、文献、邮件、外部解析调用均为零；自动化网络被限制在回环。夹具初始化和专项的调用计数是离线替身，不是付费请求。
- 本轮四个隔离根（`sra-move-df66c5a6`、`sra-move-e4c5745a`、`sra-move-4f784877`、`sra-move-cc4f10b3`）均正常关闭，实例标记 `stopped`。最终 8098 无监听；8077 仍为原 PID 28836，8001／8002 无监听，均未操作。本报告中的 8098 地址只在显式启动夹具期间有效，不是交付后持续运行的访问入口。
- 仅 Windows 本地单进程实测；未实测 Linux、网络文件系统、真实断电／磁盘硬件故障、真实提供商或多人压力。进程故障注入证明的是本地 SQLite 回滚恢复路径，不是硬件可靠性认证。
- 移动仍有支持范围、每类 1000 条和解析链 32 次的保守边界。归档／删除导致旧项目已不可访问时，旧链接可以返回不可用，不自动恢复项目或公开归属；当前目标的正常列表可继续进入。
- 浏览器只能保护自身及账号内已知草稿；其他标签页的未上传文件无法由服务端转移，旧槽保留并提示手动处理。未知结果时原移动编号保留，不能悄悄改成新目标。
- 没有新增已有对话归档、复杂科研对话迁移、长期记忆或稳定角色；这些仍需单独设计结构化依据、目标采纳、审计和恢复规则。

交付后停止，等待独立只读复核，不自动进入下一阶段。

## 2026-10-30 独立复核补修

目标预检查竞态、明确拒绝与未知结果区分、拒绝后重新选择，以及关闭窗口的迟到结果保护，见 [conversation-move-review.md](conversation-move-review.md)。该文档单独列本次实现、接口字段、精确拒绝回执、复现与验收结果；本页原测试数量保留为上一轮历史记录，不累加。
