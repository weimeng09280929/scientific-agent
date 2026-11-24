# 项目与对话组织、首页研究引导（2026-10-29）

本轮只完成可视化组织和首页说明，保留上一轮项目总览。分支 `feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`；开始时已有 348 条工作区状态，未重置、清理、提交、推送或部署。仓库及检查的父目录未发现适用 AGENTS.md。

## 实际核对与实现

原侧栏项目名称打开无会话的对话页；“最近对话”已有所属项目信息，但项目列表没有项目内展开结构。项目内对话元数据与分页接口已存在，不需要建立新数据体系。新对话的原有归属下拉框也会出现在已有对话中，实际行为是切换到其他项目的新草稿，容易被理解为移动对话。

现在：

- 侧栏项目显示本地文件夹图标；点击名称打开已审核的项目总览。独立的箭头按钮展开／收起项目对话，支持 Enter／Space，并保留展开按钮的焦点。展开、查看、切换不创建项目或会话。
- 对话使用本地气泡图标；项目和对话均保留文字名称，分别标记“当前项目”“当前对话”。当前对话位于分页外时，复用最近对话的既有精确钉选元数据展示，不读取其他对话正文。
- 每次展开按项目读取 5 条会话元数据，继续使用原 offset 分页；显示加载中、空态、失败与有效重试。折叠期间迟到的结果只更新对应项目缓存。提供手动“刷新项目对话”，其他标签页新建内容不宣称实时推送。
- 最近对话继续是同一个会话的快捷入口，标明所属项目；重名项目使用已有时间及短标识区分，同名会话补充时间与短标识。没有复制聊天记录。
- 文件夹内可进入“在本项目新建对话”；明确发送后才保存新会话。已有对话的所属项目固定展示，不提供移动操作。新对话选择项目时，打开该项目独立草稿，原位置的文字与 File 保留，返回可继续；不自动覆盖目标草稿或携带其他项目的材料关联。
- 项目管理页的名称按钮也进入总览并使用同一文件夹图标；保留其搜索和批量项目管理用途，没有将归档管理页重做成另一套会话系统。
- 搜索、重命名、新研究、草稿／附件作用域、账号门禁、科研采纳及总览读取契约不变。刷新后 File 无法恢复时，继续明确提示重新选择。

## 修改文件与读取方式

| 文件 | 修改 |
| --- | --- |
| `web/static/conversation-home.js` | 文件夹展开、缓存与分页、当前项标识、焦点、归属说明；挂载首页模板 |
| `web/static/index.html` | 新增首页介绍 HTML 模板，保留系统名和原开场入口 |
| `web/static/ui-experience.css` | 本地图标、文件夹层次、长名、窄屏与首页流程样式 |
| `web/static/project-library.js` | 管理页名称进入总览 |
| `web/static/assets/nav-folder.svg`、`nav-conversation.svg` | 同一风格的本地线框图标，无外部图标服务 |
| `tests/project_organization_test_server.py`、`test_project_organization.py`、`project_organization_browser.cjs` | 临时账号／数据、真实接口专项和浏览器路线 |
| `tests/project_overview_browser.cjs` | 将项目列表名称的预期同步为总览；不再期待旧的空对话页 |

没有修改生产后端、数据库结构、模型路由、科研提示词、调用上限或依据校验。

复用：`GET /api/projects`、`GET /api/conversations/recent`、`GET /api/projects/{pid}/conversations?limit=5&offset=…`。仅进入会话才使用原会话／消息／材料读取。显式发送仍走原 `/api/conversations/start` 和消息接口，用户维度 request_id、项目归属及幂等规则不变。展开记录存入原账号作用域本地存储；会话元数据只在页面内缓存，不保存正文为另一份记录。

## 首页最终文案

**从一个科研兴趣，逐步形成有依据的研究问题与方法草案。**

面向科研新手，围绕研究问题组织讨论，让资料、判断依据和方法草案逐步积累在同一个项目中。

可以直接输入、先添加论文，或选择已有项目。明确发送后才建立项目或新会话。

研究引导：六步可往返，从适合你的地方开始。

1. **说说兴趣与条件**：通过对话了解方向、已有基础、时间和资源。不知道具体题目也可以开始。
2. **逐步明确研究方向**：结合你的回答缩小范围，确定值得进一步了解的问题。
3. **导入或检索文献**：整理相关资料，区分标题、摘要和正文。联网检索需明确发起，受当前服务配置、额度和来源可用性限制。
4. **结合资料讨论**：讨论研究做了什么、依据与限制，整理值得追问的问题；以实际读取内容为准。
5. **确认研究问题与创新点候选**：区分文献支持的内容与待验证设想，由你决定是否采用；不保证候选具有创新性。
6. **形成方法草案**：讨论步骤、资源、比较方式和可行性，审阅后明确采纳。

这些步骤可以往返，不必逐项完成。已有方向或已有论文，都可以从对应入口开始。

项目像一个持续研究课题的资料文件夹，集中保存文献、笔记、已确认的研究问题和正式方案。一个项目可以开启多段独立对话，分别讨论不同问题。后续讨论可以参考已保存的项目信息，具体使用范围以实际读取内容为准。

保留三个原开场选项，并提供“我已有论文，先添加资料”“选择已有项目”。前者只打开文件选择、后者只打开现有管理页，开场选项仅准备输入。桌面默认展开流程，窄屏可展开查看；介绍区独立滚动，输入区保持可用。没有宣传未接入的全文精读、划词翻译或自动完成研究；没有自动检索或自动采纳。

## 验证结果与证据

使用真实 FastAPI、临时 SQLite、临时账号和文件；模型与网络边界均为可控离线替身。

| 类别 | 去重结果 |
| --- | --- |
| 新增接口专项 | **5 passed**，[JUnit](project-organization-home/special-results.xml)：同名但不同身份、独立正文、分页只读且不重复、最近条目同一会话、跨账号／嵌套越权拒绝、显式发送及重复 request_id 只产生一次独立回复 |
| 既有导航／会话回归 | **29 passed**，[JUnit](project-organization-home/navigation-regression.xml) |
| 既有账号隔离／CSRF／会话回归 | **3 passed**，[JUnit](project-organization-home/account-regression.xml) |
| 主浏览器路线 | [通过](project-organization-home/final-v2/browser-results.json)：两项目多会话、同名、按需读取、分页、空文件夹、503 重试、迟到响应、总览／会话／最近入口、前后退与刷新、文字与 File、首页所有入口、显式创建、双账号及同浏览器切换、桌面／390px／键盘 |
| 只浏览证据 | [记录](project-organization-home/final-v2/read-only-results.json)：浏览前后 5 个临时项目数据库哈希与调用计数不变，科研写请求为 0；展开阶段未请求消息正文 |

另外只读核对了原有首页“案例体验”入口：[目录可打开且未启动案例](project-organization-home/catalog-check/catalog-results.json)。没有将这项入口检查计为完整教学流程验收。

Python **37 项（新增 5、既有 32）**，浏览器路线单列，不累加子断言或复跑次数。[计数清单](project-organization-home/test-summary.json)。主浏览器在只读阶段结束后，明确发送一次，只有所选项目新增 1 个会话和 1 对用户／助手消息；仅增加 1 次离线指导调用，其他项目字节不变，没有把其他对话正文带入新聊天。真实模型、文献源和邮件调用均为 **0**。

视觉补查发现并修正了当前项目名过早截断、文件夹操作挤在一行，以及展开重建后键盘焦点丢失的问题；主浏览器随后在新的临时根完整复跑通过。早期浏览器脚本在切换到 390px 后直接点击已收起侧栏中的“新研究”超时，已改为先打开侧栏，不强制点击隐藏元素。初次失败截图和先前结果保留在本目录，最终证据使用 `final-v2/`，没有改测试断言来跳过功能。既有 pytest 配置与 UTC API 弃用告警仍存在。

截图均实际打开检查：

- [首页桌面](project-organization-home/visual-final-v2/home-desktop.png)、[首页 390px](project-organization-home/visual-final-v2/home-390px.png)、[390px 展开流程](project-organization-home/visual-final-v2/home-flow-390px.png)
- [两个项目文件夹](project-organization-home/visual-final-v2/folders-structure-desktop.png)、[项目／对话分别选中](project-organization-home/visual-final-v2/folders-selected-desktop.png)、[390px 长名与文件夹](project-organization-home/visual-final-v2/folders-390px.png)
- [主路线错误与重试](project-organization-home/final-v2/folders-error-desktop.png)

## 重复验证与运行状态

```powershell
$sraOrgRoot=Join-Path ([System.IO.Path]::GetTempPath()) ('sra-organization-'+[guid]::NewGuid().ToString('N').Substring(0,10))
venv/Scripts/python.exe -m tests.project_organization_test_server --root $sraOrgRoot --port 8097
# 在另一终端运行。每次完整主路线使用新夹具根，避免把上次显式发送纳入初始样本。
$env:NODE_PATH='C:\Users\czh05\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:SRA_ORGANIZATION_OUTPUT='docs/project-organization-home/recheck'
node tests/project_organization_browser.cjs
# 可选只读截图复查，不重复执行主发送路线：
node tests/project_organization_browser.cjs --visual-only
Invoke-RestMethod -Method Post http://127.0.0.1:8097/__organization__/shutdown
```

```powershell
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_project_organization.py
# 以下本地兼容回归另开进程，避免账号夹具重载的模块状态互相影响。
$env:SRA_AUTH_MODE='local'; $env:SRA_WEB_SKIP_GUARD='1'; $env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_project_navigation.py tests/test_conversation_home.py
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_local_account_isolation.py::test_accounts_and_routes tests/test_local_account_isolation.py::test_real_resources_and_every_project_route tests/test_local_account_isolation.py::test_csrf_cookie_session_rotation_and_restart
```

实际使用的临时根依次为 `%TEMP%/sra-organization-d8f33a39b9`、`sra-organization-a0c828314d`、`sra-organization-3b4b441d7e`，均保留，不清理旧目录。夹具拒绝非临时根和 8001／8002／8077。三次自建实例均正常退出（退出码 0），交付时 8097 已关闭；地址不是常驻服务。服务与最终数据核对见[结束记录](project-organization-home/service-status.json)。未访问真实项目或实际账号数据，没有启停或占用三个保留端口；8077 仍为原进程 28836。

## 未验证与后续依赖

- 只验证本地 Edge 桌面和 390px CSS 视口，不代表真实手机、软键盘、屏幕阅读器或所有浏览器已验收。未做大规模项目性能或真实外部生成质量验证。
- 已有对话归档／跨项目迁移本轮未实现，未放置“移动到项目”按钮。现有项目归档不等于会话归档。后续需要独立定义归档、恢复、列表过滤，以及移动时的材料／附件／版本引用、幂等、审计与运行中请求归属，不能只改 project_id。
- 项目长期记忆／稳定角色本轮未实现。现有上下文使用本会话最近 8 条消息及本次读取的项目条件、已保存材料和选中依据，不是自动读取所有旧对话，也没有生成永久项目摘要。后续需明确用户核对、记忆更新／失效、冲突与来源追溯、账号隔离及上下文预算；不能把首页说明当作这些机制已上线。
- 没有修改模型角色、提示词、回答结构或新增阅读／翻译／联网机制。到此停止，等待独立只读复核。
