# 项目名称入口与项目总览（第 1 步，2026-10-29）

本轮仅修改项目浏览入口和总览展示。分支 `feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`；保留工作区原有修改。仓库与已检查的父目录未发现适用 AGENTS.md。不进入首页文案、模型角色或科研回答的后续批次。

## 核实结果与最终行为

在独立账号实例复现：点击侧栏“人工智能RAG”后，实际 URL 仍为 `page=dashboard`，会话编号被清空，中央区域是准备新对话的项目工作区，没有“项目总览”标题。与“在本项目新建对话”的路由相同。[复现记录](project-overview-entry/before-results.json)、[修改前截图](project-overview-entry/before-desktop.png)。复现前后项目数据库字节一致，原问题不是后台已新建会话。

现在侧栏当前项目名、对话头部“查看所属项目”均进入 `page=project-overview`，保留项目编号和刚才的会话编号。显示原项目名称、已有目标与条件、正式研究状态、资料与笔记、已确认问题、正式方案与未采纳草案，以及继续讨论入口。项目名不改写。

“在本项目新建对话”保持原行为：当前项目、无会话的对话编辑页，明确发送才保存新会话。总览无对话时显示空态；有原会话时“返回刚才的对话”，否则提供“继续最近对话”。刷新和浏览器前进后退沿同一 URL 契约恢复。

简介只展示已保存目标、正式问题及基础／资源／期限；缺少目标和正式问题时显示指定的简介未完善提示。不从聊天推断个人条件，不生成简介。研究状态继续采用现有 workflow/progress，候选或未采纳草案不变成正式成果，入库篇数不冒充已阅读全文。

## 修改与读取来源

生产修改仅 5 个前端文件：

| 文件 | 作用 |
| --- | --- |
| `web/static/conversation-home.js` | 当前项目名与头部项目按钮进入总览；新对话行为不变 |
| `web/static/research-navigation.js` | 注册“项目总览”页面和标题 |
| `web/static/app.js` | 挂载总览；总览不启动任务状态轮询或额外侧栏材料重建 |
| `web/static/workspace-overview.js` | 复用现有读取和方案审阅组件，增加只读总览；原活动页仍独立 |
| `web/static/ui-experience.css` | 仅总览作用域的双列／窄屏单列样式 |

| 现有 GET 接口 | 用途 |
| --- | --- |
| `/api/projects/{pid}/workspace?include_chat=false` | 原名称、brief、正式记录、论文摘要目录、workflow 和报告计数 |
| `/api/projects/{pid}/workspace/materials?kind=plans&limit=3` | 正式版本、状态、项目范围未采纳方案草案、现有 progress；超出时进入完整目录 |
| `/api/projects/{pid}/notes` | 原笔记；另与工作台已保存 note 记录分别计数，不计聊天正文 |
| `/api/projects/{pid}/conversations?limit=1` | 最近会话或无会话空态 |
| `/api/projects/{pid}/conversations/{cid}`（如携带原会话） | 核对嵌套归属和返回原对话 |

没有新增生产接口、表、迁移、科研流程状态或模型调用。读请求继续受原服务端会话与项目所有权保护；组件也核对响应项目编号，页面脱离或项目变化后丢弃迟到结果。账号切换继续复用原账号门禁和清空页面机制。

原对话草稿仍使用既有账号／项目／会话作用域；附件 File 保留在原有会话内存映射。总览保留原会话编号，返回不会误进项目新对话草稿。整页刷新后浏览器无法恢复 File，原文件名及“重新选择同一文件；文本草稿已保留”的提示继续有效，没有伪装附件已恢复。

## 验证

全部使用临时账号、SQLite 和离线边界，实际 FastAPI 及 Edge 浏览器。没有读取真实项目或修改真实账号，没有访问／启停 8001、8002、8077。

| 类别 | 最终结果 |
| --- | --- |
| 新增专项 | **8 passed**，[JUnit](project-overview-entry/special-final.xml)：接口材料与原记录一致、正式 v1／未采纳草案分离、空项目、不写库、5 类输入的非所有者／匿名访问、会话混用拒绝 |
| 既有导航与会话回归 | **29 passed**，[JUnit](project-overview-entry/navigation-regression.xml)：只读历史、分页、项目名、幂等、新会话、版本目录及嵌套读取 |
| 既有账号回归 | **3 passed**，[JUnit](project-overview-entry/account-regression.xml)：真实路由隔离、CSRF、会话轮换及重启 |
| 浏览器 | **1 个脚本通过**，[结果](project-overview-entry/browser-results.json)：13 组检查，包含文字／附件往返、刷新重选、前进后退、资料入口、独立新对话入口、空项目、503 重试、同页切项目及迟到响应、独立双账号与同浏览器账号切换、桌面／390px |

Python 用例去重合计 **40 项（新增 8、既有 32）**，不把复跑或浏览器子断言累加为 Python 测试。浏览器前后 4 个项目数据库 SHA-256、会话／消息／请求／草案／审计条数完全一致；科研写请求为 0，离线生成计数未增长。真实模型、文献源及邮件调用均为 **0**。

过程记录：

- 初次新增断言把现有导出目录的 `version` 文本误当整数；[初次失败](project-overview-entry/special-initial.xml)保留。已改为核对原有 `v1 · 短标识` 契约，并额外核对正式记录数值 `plan_version=1`，没有改生产版本契约。
- 初次组合运行后，账号夹具重载的 `web.api` 保留 accounts 模式，而旧导航夹具直接调用本地处理器、不安装账号上下文；组合运行出现失败后中止，未形成完整计数。最终按上述分进程命令验证，未改变权限检查来迁就夹具。
- 浏览器脚本最初未经过“添加待上传论文”对话框便等待文件选择；已改走实际按钮。增强迟到响应检查时，最初错误地同时暂停了目标项目的材料读取，造成测试等待；已只暂停原项目响应，并在退出时释放测试屏障。[等待失败时截图](project-overview-entry/failure-1793274369665.png)保留。最终同页切换验证通过，没有为这两项脚本问题修改产品逻辑。
- 既有 asyncio_mode 配置、datetime UTC API 弃用告警仍在；没有删断言、skip 或 xfail。

截图已实际打开检查：

- [桌面总览](project-overview-entry/overview-desktop.png)
- [390px 总览](project-overview-entry/overview-390px.png)
- [空项目](project-overview-entry/empty-desktop.png)
- [读取失败与重试入口](project-overview-entry/read-failure-desktop.png)

## 重复验证路线与限制

```powershell
$sraOverviewRoot=Join-Path ([System.IO.Path]::GetTempPath()) ('sra-overview-'+[guid]::NewGuid().ToString('N').Substring(0,10))
venv/Scripts/python.exe -m tests.project_overview_test_server --root $sraOverviewRoot --port 8096
# 另一终端：使用本机已有 Playwright 运行时，不安装前端框架。
$env:NODE_PATH='C:\Users\czh05\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
node tests/project_overview_browser.cjs
# 仅本测试实例提供的关闭接口：
Invoke-RestMethod -Method Post http://127.0.0.1:8096/__overview__/shutdown
```

API 专项与账号回归分开运行：

```powershell
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_project_overview.py
$env:SRA_AUTH_MODE='local'; $env:SRA_WEB_SKIP_GUARD='1'; $env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_project_navigation.py tests/test_conversation_home.py
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_local_account_isolation.py::test_accounts_and_routes tests/test_local_account_isolation.py::test_real_resources_and_every_project_route tests/test_local_account_isolation.py::test_csrf_cookie_session_rotation_and_restart
```

隔离夹具仅接受系统临时目录的 `sra-overview-*` 新目录，拒绝 8001／8002／8077。自动测试账号由夹具在临时库创建；密码不写入证据、截图或本说明。人工路线：在隔离实例登录测试账号 → 打开已有对话并输入未发文字／选择附件 → 点击当前项目名 → 核对总览和草案／正式记录 → 返回原对话 → 刷新、前后退和切换测试账号核对。

本次临时根为 `%TEMP%/sra-overview-5417c7df25`；前一启动失败根 `%TEMP%/sra-overview-275adc80d0` 也保留，未执行目录清理。交付时自建 8096 已关闭，地址不是常驻可用服务。服务状态见[结束核验](project-overview-entry/service-status.json)。原 8077 用户体验实例未启停。

关闭细节：测试 shutdown 已停止 8096 监听，但 Windows Proactor 的连接重置异常使进程未及时退出；随后中断本轮自己的 exec 会话（退出码 1）。最终无本轮测试进程，8077 仍为开始前同一进程 28836。没有把这次退出描述为正常关闭，也没有修改临时实例标记来伪装停机状态。

未验证真实模型内容、真实用户项目、真实手机及屏幕阅读器，也没有重新验收整站所有功能；没有冒充公网或负载验收。总览是多个现有 GET 的展示，不是跨接口的事务快照；其他标签页并发修改材料后可手动刷新。仅摘要级资料、历史校验范围、版本与需复核状态继续沿用原记录。第 2、3 步尚未实施，本轮结束等待独立只读复核。
