# 本地账号与项目隔离交付（2026-10-27）

> 后续用户已授权增加本地注册、将新密码下限改为 8 位，并以 8001 演示旧项目、8077 运行账号版。当前规则及运行状态见 [本地注册与双入口](local-registration-dual-entry.md)；下文保留前一轮验收时的范围记录。

## 实施前清单（2026-10-26）

开发分支 `feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`。工作区原有大量已修改及未跟踪文件，全部保留。8001、8002、8066 无监听。仓库未找到适用的 `AGENTS.md`。本轮只使用隔离数据目录。

| 入口 | 数据归属与风险 | 预定检查点 |
| --- | --- | --- |
| `/api/projects`、`/api/conversations/start`、`/api/teaching-cases/start` | 新项目、幂等开始记录、教学项目 | 会话用户、用户维度幂等键、项目归属 |
| `/api/projects`、`/api/conversations/recent` | 全局项目扫描、搜索与计数 | 在聚合前按用户归属过滤 |
| `/api/projects/{project_id}/...` | 旧工作流、会话、笔记、附件、PDF、报告、任务、目录、导出、下载、归档 | 每次请求先检查完整项目归属；嵌套 ID 继续由原项目数据库核对 |
| `/api/projects/batch-delete` | 批量归档 | 所有项目逐一预检归属 |
| `/api/conversation-capabilities`、`/api/teaching-cases`、`/api/data-sources` | 公共能力和公开教学目录 | 账号模式同样要求登录；登录页不需要研究 API |
| `/`、`/workspace`、`/static/...` | 主工作台与静态代码 | 静态目录只含公开代码，不挂载项目目录；页面本身不包含用户数据 |
| 项目目录和 SQLite 文件 | 历史未认领项目、附件、产物 | 账号库单独记录归属；未认领项目对所有账号不可见；普通读路径不修改旧项目库 |
| 前端浏览器状态 | 草稿、当前项目、附件、迟到响应、多标签页 | 身份切换清空内存和旧无主缓存；受保护请求与服务端会话同步 |

以上清单先于实现写入。本轮完成本地邮箱密码登录和服务端项目隔离；没有进入邮件、注册、账号恢复、公网部署、分享、收费或多人协作。既有未提交修改全部保留，没有切换、重置、清理仓库、提交或推送。

## 实现与数据契约

账号库独立于项目库，显式初始化为 SQLite `user_version=1`，包含 `users`、`sessions`、`project_owners`、`login_attempts`。项目仍使用原来的目录和 `knowledge.db`，没有给历史项目批量加所有者字段，没有批量迁移、重新生成或改变采纳状态。

- 用户主键为随机、不可变内部 ID；邮箱仅作登录标识。统一规则为去除首尾空白并转小写；保留点号和 `+` 别名，不做供应商特例合并。规范化后的邮箱由数据库唯一约束保护。`email_verified` 始终为 false，界面明确显示“邮箱未验证”。
- 密码使用 `argon2-cffi` 的 Argon2id，参数为 64 MiB、3 次、并行度 2；库负责随机盐和格式。密码允许 12–256 字符且至多 1024 UTF-8 字节，不截断，不存可逆值。管理命令用 `getpass` 两次输入；终端不能隐藏输入时拒绝读取。没有密码命令行参数。
- 会话为 32 字节随机不透明令牌，账号库只保存令牌 SHA-256；有效期固定 12 小时，不滑动延期。登录创建新会话并撤销同一浏览器原 Cookie 对应会话；退出撤销当前会话；禁用账号撤销该用户全部会话。每次请求重新检查过期、撤销和禁用状态。重启不清空有效会话，也不会退回匿名访问。
- Cookie 名 `sra_session`，Host-only、`HttpOnly`、`Path=/`、`SameSite=Lax`、`Max-Age=43200`；默认 `Secure`。只有明确设置 `SRA_AUTH_INSECURE_HTTP=1` 才允许本地 HTTP，并同时限制回环客户端和回环 Host。令牌不进 URL 或 localStorage。
- 登录需要同源 Origin（浏览器提供时）及 `X-SRA-Login: 1` 自定义头，防止普通跨站表单登录；认证后的状态修改需要与当前服务端会话绑定的随机 `X-SRA-CSRF`。跨站 Origin / Fetch-Site 被拒绝，CORS 不开放跨源。CSRF token 只在页面内存中使用，不能代替会话 Cookie。受保护 fetch 的 GET 也携带原会话 token，Cookie 切换后旧页面不能读入新账号列表。
- 登录错误、未知邮箱、禁用和尝试限制使用通用提示。邮箱与来源 IP 分别累计失败，15 分钟内 5 次失败后阻止 15 分钟；状态持久保存，重启不清空。登录输入校验也不回显密码。
- 账号模式下所有 `/api/` 请求先经过统一会话边界，再进入教学边界和业务路由。项目路径先校验 ID、归属，再读项目；不存在与非所有者均为 404。已知项目下的会话、任务、论文、报告版本等继续由该项目 SQLite 的原查询核对。客户端传入的所有者字段不参与授权。
- 创建项目由服务端当前用户登记归属。首次发送和教学／转普通研究复用 `ConversationStarter`，其持久开始键、项目 UUID、bootstrap 标记和锁采用同一个用户维度的内部请求键；返回仍是原客户端 request_id。相同用户重放保持原幂等结果，不同用户同号不会碰撞。中途初始化失败的保留记录只可由原用户核对完成，其他用户列表不可见。
- 后台任务继续使用原项目、会话、请求和版本记录；项目所有权独立于浏览器选中状态。退出不取消已提交任务；结果仍发布到原项目，退出后查询被拒绝。本轮没有另建任务系统。
- 所有 API 响应及鉴权错误使用 `private, no-store`，按 Cookie 区分；下载也受同一检查。静态资源只有 `web/static` 内的公开代码和图片，路径 resolve 后必须留在该目录。去掉冗余静态挂载，旧 `workspace.html` 转向主登录入口。没有挂载项目、账号库、归档或上传目录。
- 本地 PDF 和产物内容路径必须在对应项目／产物根目录内；PDF 缓存文件名不能由路径参数穿越目录。已有 PDF 下载使用只打开既有库的模式，不顺便建表或迁移。外部路径历史文件不自动搬入，需将来显式核对迁移。

默认 `SRA_AUTH_MODE=accounts`；账号库缺失、版本不符或模式拼写错误时启动失败。保留的无登录模式必须明确设为 `local`，只允许回环客户端、回环 Host，模块启动默认绑定 127.0.0.1。它用于独立的旧本地数据目录，不能作为账号模式的认证失败兜底，也不支持反向代理或公网使用。

### 主要文件

| 文件 | 本轮变更 |
| --- | --- |
| `core/local_accounts.py` | 账号、Argon2id、会话、登录限制、归属和显式 schema 检查 |
| `tools/local_accounts.py` | init / create / disable 本地管理命令；安全输入 |
| `web/api.py` | 默认模式、统一鉴权与 CSRF、账号端点、创建归属、聚合过滤、文件边界、旧入口处理 |
| `core/workspace/start.py`、`web/conversation_home_api.py`、`web/teaching_case_api.py` | 用户维度幂等开始键，复用原锁和创建流程 |
| `core/config.py` | `SRA_DATA_ROOT` / `SRA_PROJECTS_DIR`，隔离项目及归档数据位置 |
| `core/knowledge/store.py` | 可明确只打开既有库、不初始化 schema；用于 PDF 下载 |
| `core/artifacts/version.py` | 只读内容存储不建目录，文件内容路径限制在产物根目录 |
| `web/static/local-auth.js`、`index.html`、`style.css` | 登录、退出、身份确认后加载原工作台、会话失效及账号切换 |
| `web/static/app.js` | 支持登录后动态加载时初始化主应用 |
| `.env.example`、两个 requirements 文件 | 安全默认配置及 Argon2 依赖 |
| `tests/test_local_account_isolation.py`、`local_account_fixture.py`、`local_account_test_server.py`、两个账号浏览器脚本 | 临时账号、真实应用、离线边界、越权矩阵与实际进程重启验收 |

本表只描述本轮修改；`git diff` 包含大量之前的开发工作，不能把全部 diff 行数计为本轮变更。

## 路由权限覆盖

| 路由组 | 账号模式权限 |
| --- | --- |
| `GET /api/auth/me`、`POST /api/auth/login` | 公开认证入口；me 不返回无效会话数据，登录执行 CSRF 和尝试限制 |
| `POST /api/auth/logout` | 有效会话 + CSRF，撤销该会话 |
| `POST /api/projects` | 当前用户创建，不采信客户端 owner |
| `GET /api/projects`、`GET /api/conversations/recent` | 聚合前过滤用户项目；查询、总数、当前选中项和最近会话同样过滤 |
| `POST /api/projects/batch-delete` | 每个项目先做归属预检，再走原批量归档事务 |
| `POST /api/conversations/start`、`POST /api/teaching-cases/start` | 会话用户决定归属和幂等空间；已有项目须已归当前用户 |
| `/api/projects/{project_id}` 及所有子路由 | 会话、项目所有权、原嵌套资源检查；写操作另需 CSRF。含旧 run / resume / human-response、笔记、上传、PDF、审计、P1 retry / restart、草案采纳、workspace、材料目录、报告版本和历史下载 |
| `/api/conversation-capabilities`、`/api/teaching-cases`、`/api/data-sources` | 需要登录，不含其他用户研究资料 |
| `/`、`/workspace`、`/static/index.html` | 只交付登录与静态应用外壳；登录前不运行研究工作台脚本 |
| `/static/workspace.html` | 转到主入口，避免旧外壳使用无用户缓存 |
| 其他 `/static/...` | 公开程序资源，严格目录边界；无项目数据挂载 |
| `/openapi.json`、`/docs`、`/redoc` | 公共接口 schema／帮助；不含实际项目记录或文件 |

[逐路由表](local-account-isolation/route-coverage.md) 和 [原始矩阵](local-account-isolation/route-coverage.json) 列出应用实际 OpenAPI 的 **71 个项目路由方法**。非所有者、匿名、禁用账号分别验证为 404 / 401 / 401；这些 213 次请求属于一个专项测试的断言，不额外算 213 个测试。矩阵包含原有路由，不只检查新账号接口。全局创建、聚合、批量归档、认证及静态入口另有专项正反检查。

未纳入 HTTP 用户授权的部分：本地管理命令、直接操作文件的科研 CLI 和独立开发脚本属于操作系统用户权限，没有作为网络接口开放；本轮没有把它们改造成账号平台。临时测试服务的 `__account__` 路由只存在于测试模块，产品 `web.api:app` 不包含它们。

## 历史、缓存与科研保护

历史未认领项目在账号模式下对所有账号隐藏，不归给第一个登录用户。账号库初始化不扫描分配历史归属。将来认领需要单独的管理员流程：备份、列出项目及目标不可变用户 ID、人工核对后显式写入归属并审计；本轮未提供真实迁移工具，也未执行真实认领。原归档所有权保留，当前没有新增 Web 恢复接口；管理员日后恢复目录仍须保留原归属，不能借恢复转移所有者。

前端在身份确认后才加载原主应用。localStorage / sessionStorage 的应用键按内部用户 ID 加命名空间；旧无用户键不复制、不删除、不自动认领。笔记、未发文字、项目选择、待上传清单和设置均走同一存储入口。文件对象仅留原页面内存；刷新后仍遵循原来的重选／回执核对保护。

退出成功后重建页面，清除原页面内存、轮询、预览及文件对象。多标签页通过 BroadcastChannel（不支持时用不含数据的 storage 事件）通知；会话失效立即隐藏原内容、终止在途 fetch 并重载登录页，旧页面不继续重试受保护接口。返回缓存页面时重新核对身份；定时及重新聚焦检查也识别禁用、过期或其他标签页换账号。退出网络失败时明确告知，不伪称已经撤销。未发文字仍留在原用户命名空间，新用户看不到；登录同一用户可恢复原草稿。

没有更改模型路由、科研提示词、P1 的预算／重试／恢复、依据归属规则、取消状态机、报告引用快照、材料指纹或正式采纳事务。教学实际走到明确采纳问题与方案，保持 DESIGN 上限。离线研究报告保留版本引用、作者／年份／链接及摘要阅读范围，不把软件夹具当科学证据。

## 验证与失败记录

使用 Windows、真实 FastAPI／主 index.html、临时 SQLite、临时文件、Edge 的独立浏览器上下文和离线模型／来源替身。测试服务替换全部检索来源和模型注册表，并阻止 requests 公网访问；浏览器只放行相应回环来源。没有调用真实模型、公网文献源或邮件服务。

| 验证 | 结果与证据 |
| --- | --- |
| 新增账号专项 | **10 passed**，40 条既有配置／datetime 弃用警告，见 [account-results.xml](local-account-isolation/account-results.xml)。包含账号与规范化、退出／过期／禁用、CSRF／Secure Cookie／轮换、持久登录限制、历史隐藏、完整项目路由矩阵、同名文件和同号请求、嵌套混用、文件目录边界、归档、只读下载不迁移、教学守卫前的鉴权、退出后的后台任务发布归属 |
| 既有受影响回归 | 首轮 [regression-results.xml](local-account-isolation/regression-results.xml)：217 passed、1 failed；随后 [regression-delta-results.xml](local-account-isolation/regression-delta-results.xml)：89 passed。通过项交集 57，去重 **249 个既有用例通过**，包含修复后的原失败项。不能将 218 和 89 相加 |
| 真实主应用浏览器 | [browser-results.json](local-account-isolation/browser-results.json)：A/B 独立上下文、同号开始请求、各自离线报告、实际下载、教学采纳、草稿刷新、旧缓存不认领、多个标签页退出、A 上传响应延迟时换 B、B 草稿／附件保护、会话失效和返回保护。查看了实际截图，不以无 pageerror 代替归属断言 |
| 实际进程重启 | [restart-results.json](local-account-isolation/restart-results.json)：8078 上启动两次独立 Python 进程，浏览器保留原 Cookie，重启后仍为原内部用户，原项目与笔记可读；两个进程均已关闭 |
| 语法／编译 | 账号与相关 Python 模块 `py_compile`、原 app.js 与 local-auth.js 的 `node --check` 通过 |

必要命令（仓库根目录；PowerShell）：

```powershell
$env:SRA_ACCOUNT_EVIDENCE_DIR='docs/local-account-isolation'
venv/Scripts/python.exe -m pytest tests/test_local_account_isolation.py -q --disable-warnings --junitxml=docs/local-account-isolation/account-results.xml

# 既有回归显式使用受限的本地兼容模式，夹具仍是临时数据。
$env:SRA_AUTH_MODE='local'
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
venv/Scripts/python.exe -m pytest tests/test_p1_recovery_attribution.py tests/test_project_conversations.py tests/test_research_delivery_closure.py tests/test_research_delivery_supplement.py tests/test_local_product_acceptance.py tests/test_reliability_delivery.py tests/test_research_teaching_cases.py tests/test_product_plan_boundary.py tests/test_project_archive.py tests/test_conversation_home.py -q --disable-warnings
venv/Scripts/python.exe -m pytest tests/test_conversation_home.py tests/test_paper_browser_import_download.py tests/test_latest_artifact_download.py tests/test_knowledge_store.py tests/test_project_archive.py tests/test_research_teaching_cases.py -q --disable-warnings
```

Node 脚本需要 Playwright 和本机 Edge。此次使用 `NODE_PATH=C:/Users/czh05/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`，没有在仓库安装 node_modules。先独立启动 8077 的测试服务，再运行 `node tests/local_account_browser.cjs`；`node tests/local_account_restart.cjs` 自行管理 8078 的两个子进程。自动浏览器脚本只用于新建的自动临时夹具，不用于自选密码的人工验收目录。

失败记录保留说明：早期缺少 pytest 的系统 Python 已改用仓库 venv；Argon2 作为新增依赖安装。浏览器初稿曾等待窄屏中收起的侧栏、尚未加载的内容，或未先打开原有教学方案／上传菜单；已改为按实际 UI 操作和状态等待，没有强制点击或删除断言。路由初稿只枚举到直接路由，已改为实际 OpenAPI 的全部项目方法。PDF 测试去掉了未安装的测试专用 pypdf 生成器，改用有效的最小 PDF 字节；产品仍如实显示“未启用解析，只有元信息”。SQLite 测试连接未关闭造成的一次临时目录清理失败已修复。主脚本入口断言失败后改为 HTML 声明惰性脚本清单，由登录引导按顺序加载，原断言保留并通过。

另有一次早期 pytest 失败回显了临时夹具的口令值；相关临时账号已禁用、会话已撤销，最终 XML、截图和报告不保存有效凭证。夹具已使用 SecretStr 遮罩，浏览器失败输出也遮罩临时口令；管理命令和登录校验不会回显密码。不能声称整个开发过程从未发生过该回显。

## 实际文件与截图

- [实际下载的报告 v1](local-account-isolation/downloaded-report-v1.md)：正文 [1]/[2] 与两条参考文献逐项对应，含作者、年份、DOI／URL、阅读范围及内部关联 ID。`fixture.invalid`、`10.0000` 仅为软件测试来源。
- [登录桌面](local-account-isolation/login-desktop.png) · [390px 登录](local-account-isolation/login-narrow.png)
- [A 的报告工作台](local-account-isolation/user-a-desktop.png) · [B 的 390px 工作台](local-account-isolation/user-b-narrow.png) · [窄屏账号入口](local-account-isolation/user-b-account-menu-narrow.png)
- [教学明确采纳完成](local-account-isolation/teaching-completed-desktop.png) · [A 退出后切换 B，B 草稿保留](local-account-isolation/switched-to-b-desktop.png)

以上图片均实际打开检查，未见横向页面溢出或被显示的口令／令牌；报告检查了实际下载字节。

## 可重复的人工双账号路线

以下命令创建**新的临时根目录**。不要替换为真实项目路径。先在交互 PowerShell 创建两个自选密码账号，再启动离线服务；密码只在隐藏输入提示中输入，不写到命令参数或报告。

```powershell
$sraTestRoot=Join-Path $env:TEMP ('sra-local-account-manual-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $sraTestRoot | Out-Null
$sraAccountDb=Join-Path $sraTestRoot 'accounts.db'
venv/Scripts/python.exe -m tools.local_accounts --db $sraAccountDb init
venv/Scripts/python.exe -m tools.local_accounts --db $sraAccountDb create tester-a@example.com
venv/Scripts/python.exe -m tools.local_accounts --db $sraAccountDb create tester-b@example.com
venv/Scripts/python.exe -m tests.local_account_test_server --root $sraTestRoot --port 8077
```

服务运行期间才可访问 `http://127.0.0.1:8077`；本轮交付时该地址已关闭，不是常驻可用地址。此服务复用手动创建的账号，不改自选密码；项目、上传、会话、归档和账号库都在 `$sraTestRoot`，模型与检索为离线替身。

1. 用普通窗口登录 A、独立隐私窗口登录 B。双方从“新研究”输入明确的“请检索离线资料并整理现状”，按原领域确认操作建立项目；也可从案例体验完成不调用模型的教学路径。两边使用相同研究名称，分别添加笔记和同名 txt/pdf。
2. 在 A 中打开报告、来源与“下载当前产物”，核对实际文件；将 A 的项目／会话深链接放入 B 窗口，应显示无可访问项目，不能读取或操作。直接 API 验证路线由专项测试提供，不能用菜单隐藏代替。
3. 在 A 保留未发文字，另开 A 标签页退出；旧标签页应回登录页。改登 B 后不能出现 A 草稿、附件或材料；刷新与返回仍需当前会话。重新登录 A 才可恢复 A 的草稿。
4. 管理员在另一交互终端运行下列禁用命令；该账号原窗口下一次请求／身份检查应要求重新登录，旧 Cookie 无效。没有开放注册或邮件恢复入口。

```powershell
venv/Scripts/python.exe -m tools.local_accounts --db $sraAccountDb disable tester-b@example.com
```

结束时在启动服务的终端 Ctrl+C；或仅对这个测试服务调用 `Invoke-RestMethod -Method Post http://127.0.0.1:8077/__account__/shutdown`。`--root` 人工目录为便于复查而保留，应由操作者确认绝对路径确实为本次 `$sraTestRoot` 后再处理；不提供清理真实项目的命令。自动无 `--root` 的夹具则随正常退出自动删除临时目录。

产品服务的显式配置为 `SRA_AUTH_MODE=accounts`、`SRA_DATA_ROOT=<隔离数据根>`、`SRA_ACCOUNTS_DB=<已初始化库>`；本地 HTTP 测试另设 `SRA_AUTH_INSECURE_HTTP=1`。使用真实 `uvicorn web.api:app --host 127.0.0.1 --port 8077 --workers 1` 时不会安装离线替身，故本轮人工科研验收使用上面的测试服务，不对普通实例授权真实检索或模型调用。

## 边界与后续事项

这是本地功能与越权回归验收，不是公网安全认证。尚未进行真实邮件所有权验证、邀请注册、账号恢复、密码重置、HTTPS／受信反向代理／部署域名配置、安全响应头和公网负载／渗透测试、备份恢复及运维权限审计。没有自动认领旧项目，没有验证真实模型输出或真实 PDF 解析服务；PDF 本轮验证了上传、隔离、读取、下载及真实的解析降级说明。桌面和 390px 为 Edge 模拟视口，不等于所有真实手机测试。

沿用现有单进程后台任务能力，没有扩大到跨 worker 分布式任务。本轮验证了退出后的离线任务完成与归属、已持久会话的真实进程重启，以及既有任务恢复回归；没有把进程被杀时正在进行的真实模型调用写成已成功恢复。已下载到用户磁盘的文件不受之后的会话撤销回收。

本轮真实模型调用 **0**、公网文献来源 **0**、真实邮件 **0**。依赖安装曾访问软件包仓库，不属于科研来源调用。最终浏览器夹具记录的 6 次模型逻辑调用和 4 次来源调用全部为进程内离线替身，不是网络模型尝试。没有启停 8001／8002，没有修改真实项目，没有提交、推送或部署。隔离服务和临时数据的最终结束状态见本文件末尾。

## 结束状态（2026-10-27）

8001、8002、8077、8078 最终均无监听，未发现仍在运行的 `tests.local_account_test_server` 进程。8078 两个重启验收进程均正常结束并移除自己的临时目录。8077 之前的正常退出夹具自动清理；最后一个 8077 实例收到 shutdown 后停止监听，但 Windows Proactor 连接重置使进程未及时结束，随后中断该自建进程（退出码 1）。没有操作其他主服务。

以下自建临时目录仍保留，**未完成删除**：

- `C:/Users/czh05/AppData/Local/Temp/sra-auth-yf8nkc2c`：早期 Windows SQLite 清理失败留下的测试库。
- `C:/Users/czh05/AppData/Local/Temp/sra-browser-auth-el4x00l2`：最终浏览器夹具的隔离数据根，与 browser-results.json 对应。
- `C:/Users/czh05/AppData/Local/Temp/sra-playwright-acceptance`：已取消的测试依赖安装目录，不含科研账号数据；最终使用已有运行时 Playwright。

已在检查绝对路径和目录属性后尝试仅删除这些目录；自动安全检查拒绝了计算路径和明确 LiteralPath 两种 PowerShell 删除请求，返回原因均为 **blocked by policy**，没有提供更细说明。没有绕过该拒绝去换程序删除。作为更安全的收尾，两个账号库中的全部 5 个临时账号已禁用、全部会话已撤销；无运行服务，保留内容仅为离线测试资料。该残留是交付清理限制，不把目录写成已删除。

账号隔离功能与上述最终验证已完成；本轮到此停止，等待独立只读复核，不接入真实邮件、真实模型或后续部署。
