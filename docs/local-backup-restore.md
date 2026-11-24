# 本地停机备份与恢复

实施起点：2026-10-29，分支 `feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`。保留全部既有修改，不接触真实账号／历史项目，不使用 8001、8002、8077。仓库及上级未发现适用 AGENTS.md。

## 已核对的持久化范围

| 范围 | 关系及处理 |
| --- | --- |
| 账号 SQLite | users 的不可变 ID、密码散列、邮箱状态；project_owners 关联 active／归档项目；login_attempts 保留。sessions 在恢复副本中删除，强制重新登录 |
| projects/*/knowledge.db | papers／chunks、实体、关系、artifacts、kv_store；工作台及材料版本在 KV；会话、消息、待审草案、幂等请求、失败原因、上传 prepared／回执、检索报告及版本引用快照、调用审计和策略均在项目库，不只复制正文 |
| 项目文件 | 已实际保存的附件原文件、本地 PDF、artifacts 版本内容、snapshots／index、其他已保存项目资料；数据库与文件必须成组。现有 txt／md 上传主要保存数据库正文片段，并非每种上传都保留原始文件；不存在的原文件不能由备份补造 |
| .project-trash | 显式归档的项目及批次 manifest，保留归属，不自动恢复成活动项目 |
| 可重建／临时 | vectors 索引、运行锁、SQLite 零长度 sidecar、未完成 .part、Python 缓存不作为正式资料；存在非空 WAL／journal 或活跃工作锁时拒绝备份 |
| 另行配置 | 代码与 Python 依赖、环境变量、模型／来源配置及秘密、Cookie 安全选项、端口与单 worker 设置不随数据包复制。根目录 .env 不收集；不输出数据库内容、密码、Cookie、令牌或密钥 |
| 浏览器端 | 未提交文字草稿、文件选择、待上传文件、页面缓存均不属于服务端备份，需要用户另存；恢复不把它们当作已提交请求 |

账号版与免登录演示必须使用独立包和显式模式，不混合账号归属；未归属项目保持未归属。默认仅支持规范的 root/projects 与 root/accounts.db（或显式的根内账号库），外置项目／外置引用先拒绝并说明，不猜测、扫描或迁移真实目录。

本轮实现停机工具与目录运行租约；不宣称支持热备。数据库读取与文件校验不会初始化或升级项目库。源目录只有维护锁可能被建立，业务数据不改写。测试和交付只使用临时样本，未备份或读取真实账号／项目数据。

## 实现与停机前提

| 文件 | 作用 |
| --- | --- |
| `core/local_backup.py`、`tools/local_backup.py` | 预检查、创建备份、独立校验、恢复；无默认源目录、无覆盖／强制参数，不导入应用或执行科研任务 |
| `core/instance_state.py`、`web/api.py` | FastAPI 生命周期持有数据根、项目根与账号库的 OS 租约；状态标记记录模式、实际路径和停止状态。不同进程不能共享同一数据集绕过单进程资源上限 |
| `core/storage_paths.py` | 读取恢复后的项目文件时，使用明确的旧根映射，仅映射当前项目子树，包含归档位置；数据库原路径字符串、材料指纹与版本引用不改写 |
| `core/artifacts/version.py`、`core/workspace/materials.py`、`web/project_exports_api.py`、`web/workspace_api.py` | 产物文本、预览、材料投影、版本导出统一使用上述路径解析，仍保留各入口原有项目／目录范围检查 |
| `web/api.py` 的本地 PDF 入口 | 先解析恢复副本中的当前文件，再检查归属和返回文件；不会因为原源目录仍存在而下载到旧目录 |
| `tools/serve_local.py` | 沿恢复清单选择实际账号库；兼容 `projects/.local-accounts.db`，缺失时拒绝建立替代空库。账号恢复副本不能切换为免登录演示模式 |
| `tests/local_backup_fixture.py`、`test_local_backup_restore.py` | 两账号完整离线样本、实际 API／SQLite／文件与故障注入 |
| `tests/local_backup_acceptance.py`、`local_backup_browser.cjs` | 仅允许本轮临时目录与非保留端口，实际命令执行及恢复后的浏览器／下载验收 |

备份前必须正常停止整个实例及直接使用该目录的 CLI、后台工作和管理写入，关闭所有 SQLite 写连接，并显式传入 `--confirm-stopped`。该参数是操作者确认前提，不是强制绕过开关。已接入本轮运行租约的服务正在运行时，备份／维护命令不能取得租约，直接失败；有会话、检索、上传工作锁时也拒绝。新启动的应用不能与备份同时占用数据集，`SRA_WEB_SKIP_GUARD` 不跳过数据目录租约。

正常 ASGI 关闭且重任务、接收、旧流水线线程均已退出时，才写 stopped 并释放租约。若工作仍未退出或关闭标记写入失败，保留租约直至进程结束；遗留 running／unclean／未知标记不能用删除锁文件、TTL 或猜测 PID 的方式绕过。应先核对实际工作，再用匹配代码正常启动／关闭实例完成状态确认；启动不会自动重放原科研请求。

没有运行标记的旧实例只能由操作者先核对启动配置和全部写入进程，再作明确停机确认；工具不能自动证明未接入租约的外部程序已停止。存在标记时，模式和实际项目／账号库路径也须与所选范围一致；外置 `SRA_PROJECTS_DIR` 等布局会被拒绝，避免把空的 root/projects 误当完整数据。非空 WAL／journal 不自动 checkpoint、修复或忽略，应先正常关闭连接处理。该工具不能替代操作系统快照或在线备份协议。

## 格式、完整性与失败行为

备份是独立目录，含 `manifest.json`、`COMPLETE`、`payload/`。清单格式版本 1、存储契约版本 1，记录实例模式、逻辑布局、来源根、应用版本／SQLite 版本、逐文件相对路径／字节数／SHA-256，以及数据库 schema 摘要与检查结果。`COMPLETE` 校验清单本身；发布前再次使用同一个 verify 入口校验完整包。

只复制声明范围，分文件复制并核对实际字节；复制前后重新核对源文件集合和哈希。所有 SQLite 执行 integrity_check、foreign_key_check，并检查受支持的 user_version、工作台／会话版本和必要表。另核对账号归属、活动／归档项目、会话嵌套、文献片段、文件引用、产物父版本、草案采纳目标、首发幂等关联和调用审计归属。`projects/.conversation-starts.db` 必须包含在范围中；未完成的跨库首发保留原记录，但拒绝将它交付为已确认完整的备份。

科研报告、阅读范围、引用快照、内容状态和调用策略保存于原项目数据库，不从当前文献库或模型重新生成。关键结构检查不等于全面验证所有历史 JSON 语义、引文支持关系或科学正确性；本轮不对旧方案施加新生成语义校验，也不重新采纳旧草案。

路径只允许安全的相对清单项，拒绝绝对路径、`..`、盘符／ADS、重复或大小写碰撞、链接／目录联接／硬链接及范围外文件。校验时要求 payload 与清单一一对应，并拒绝夹带 WAL／SHM／journal、运行锁与 .part，即使有人重新计算清单哈希，也不视为合法停机包。单份清单最多 16 MiB、50,000 个文件；超出本地工具范围应明确失败。

创建／恢复先写同一父目录下的短暂存目录 `.sra-b-*`／`.sra-r-*`。全部检查通过才发布到指定目标。目标只能不存在或为空，不能与源／备份互相包含；目标若中途变为非空则失败。失败只留下明确的暂存目录和 `INCOMPLETE`，不发布最终目录；应用和启动器即使看不到标记也拒绝这两个暂存名前缀。工具不递归删除失败残留，留给操作者核对具体目录后处理。

Windows 复制前核对最终及暂存路径长度，保守要求小于 248 字符；过长请选更短的备份／恢复父目录。本轮没有修改系统长路径策略。SHA-256 用于发现损坏，不认证备份来源，也不提供加密；备份应保存在受控私有目录。账号库含密码散列及原会话服务端数据，不要公开或提交；恢复删除 sessions，旧 Cookie 不能登录。日志／证据只保存计数、路径与哈希，不输出密码、Cookie、会话令牌或密钥。

## 备份、校验、恢复与启动

以下变量必须替换为自己已核对的**临时样本目录**，不指向真实项目。本轮未执行任何真实数据迁移。目标父目录先存在，目标本身须为新空目录：

```powershell
$sraSource = 'C:\temporary-data\source'
$sraPackage = 'C:\temporary-data\backup-001'
$sraRestored = 'C:\temporary-data\restored-001'

.\venv\Scripts\python.exe -m tools.local_backup precheck --root $sraSource --mode accounts --confirm-stopped
.\venv\Scripts\python.exe -m tools.local_backup backup --root $sraSource --mode accounts --confirm-stopped --destination $sraPackage
.\venv\Scripts\python.exe -m tools.local_backup verify --backup $sraPackage
.\venv\Scripts\python.exe -m tools.local_backup restore --backup $sraPackage --destination $sraRestored
.\venv\Scripts\python.exe -m tools.serve_local --mode accounts --port 8094 --data-root $sraRestored
```

示例中的 C:\temporary-data 是占位路径，不是已经创建的访问目录。旧兼容账号库可显式指定 `--accounts-db projects/.local-accounts.db`；只允许数据根或 projects 直属的账号库，不收集外部库。成功命令输出 JSON、退出码 0；已知失败输出 `ok=false` 和可读原因、退出码 2，不输出数据库行内容。恢复命令不自动启动服务。

演示数据须单独使用 `--mode demo` 备份，再以 demo 启动独立恢复根（本轮验证端口选 8095，而非 8001）。演示包不能包含账号库；恢复不能把账号数据变成免登录项目，不能自动给未归属历史资料分配用户。归档项目仍位于 .project-trash，不自动变为活动项目。

`tools.serve_local` 仅回环监听并禁止真实模型／来源调用；本地 HTTP Cookie 配置是明确的开发配置。直接 `uvicorn web.api:app` 须显式正确设置 `SRA_AUTH_MODE`、`SRA_DATA_ROOT`、`SRA_PROJECTS_DIR`、`SRA_ACCOUNTS_DB`，启动前按管理命令初始化真实所需库；不能把当前本地启动器当作生产部署配置。本轮没有新增公网启动命令。

## 恢复后的请求与材料规则

恢复保留内部用户 ID、原密码散列和账号禁用／邮箱状态，保留项目所有权、同用户请求命名空间、消息、失败、attempt、预算审计、父级调用策略和幂等记录。只有恢复副本的 sessions 被删除；用户重新登录，不自动验证邮箱。登录尝试限制仍保留，不属于科研预算。

每个项目数据库及已保存文件与原包字节一致；不改写 pdf_path／content_path 字符串。`.sra-relocation.json` 保存旧根映射，文件读取仍限当前项目子树；连续再次备份／恢复也保留原材料指纹。恢复前源目录仍存在时，也只读取副本。账号库因撤销会话而不要求文件字节相同，但用户记录和所有权必须相同。

旧 processing／queued 记录不在恢复时改为成功或清零。新的进程身份与未持有的工作锁使其按原规则显示中断；GET、刷新、登录和导出均不重放。只有用户明确 retry／允许的新轮次才能执行，并继续遵守原 P1 分类、剩余额度及上层限制。结构失败仍禁止盲目 retry；上层额度耗尽不能通过重新登录或恢复绕过。正式 v1／v2 与未采纳草案保留，恢复后新生成也只成为待审阅草案。

## 验证结果与证据

最终按用例身份去重的结果见 [测试汇总](local-backup-restore/test-summary.json)。本轮新增 **45 个不同专项**：44 个真实 SQLite／文件／应用生命周期场景，另 1 个纯路径长度预检查；不把模块夹具的重复准备或子断言计作新用例。既有相关回归 **323 项**，共 **368 个不同用例最终通过**，无 skip／xfail。浏览器单列。

| 执行组 | 实际结果 | 证据 |
| --- | --- | --- |
| 目录范围补齐后的备份 44＋既有资源／补修／账号／注册 59 | 103 passed | [XML](local-backup-final-validation-v3.xml) |
| 夹带 SQLite sidecar 的拒绝补测 | 1 passed（新增第 45 个专项） | [XML](local-backup-sidecar-final.xml) |
| 暂存目录即使丢失 INCOMPLETE 仍拒绝启动 | 1 passed（上述专项的重复核对） | [XML](local-backup-publication-final.xml) |
| 科研、会话、P1、明确采纳、依据归属、报告引用／版本和下载 | 264 passed | [XML](local-backup-scientific-regression.xml) |
| 恢复副本双账号真实浏览器综合路线 | 通过，12 份实际下载逐份与源记录的哈希一致 | [JSON](local-backup-restore/browser-complete/browser-results.json) |

样本包括两账号同名项目／相同首发及上传编号、每项目四个会话、正式问题、正式方案 v1／v2、未采纳修订草案、PDF、笔记、文件产物 v1／v2、带版本引用快照的报告、超时／结构失败、已接受但未启动的消息、未执行的检索，以及持久上层调用限制。处理中记录通过确定性的启动失败与存储异常产生；任务的旧进程身份作为离线故障替身，不伪称真实外部服务已执行。

验证确认旧登录 Cookie 在恢复副本返回 401，新登录用户 ID 不变；A／B 各自列表、读取、修改、取消、重试、采纳、附件／报告／产物下载及混用会话编号受到服务端隔离。实际下载包括每账号 PDF、报告 v1、正式方案 v1／v2、文件产物 v1／v2；文件均由真实界面的下载入口保存，非仅检查响应码。对应的 [报告示例](local-backup-restore/browser-complete/account-a-report-v1.md)、[方案 v1](local-backup-restore/browser-complete/account-a-plan-v1.md)、[方案 v2](local-backup-restore/browser-complete/account-a-plan-v2.md)、[PDF](local-backup-restore/browser-complete/account-a-attachment.pdf) 保存在证据目录。报告中题名／作者／年份／DOI／URL／阅读范围与原版本保持一致，全部为明确的离线测试内容。

浏览和刷新阶段实际模型／来源执行为 0；用户明确重试原中断消息后只有 1 次离线生成、attempt=2、原用户消息不重复，正式方案仍是 v2。多标签页退出、单浏览器 A 切换 B 不混入草稿。恢复副本可新增笔记，原源目录业务文件哈希完全不变，[源不变及正常停止证明](local-backup-restore/final-v2/source-unchanged.json)。所有真实模型、公网文献、邮件、外部解析调用均 **0**；测试回调／审计中的 offline 计数不能当作真实网络调用，[夹具最终计数](local-backup-restore/final-fixture-state.json)。

桌面 1440px 与 390px 截图已实际查看，检查版本状态、繁忙／中断操作和账号切换，无水平溢出：

- [恢复后的版本目录（桌面）](local-backup-restore/browser-complete/account-a-versions-desktop.png)
- [B 的版本目录（390px）](local-backup-restore/browser-complete/account-b-versions-390px.png)
- [原请求中断、原调用额度保留（390px）](local-backup-restore/browser-complete/interrupted-390px.png)
- [退出 A 后登录 B（390px）](local-backup-restore/browser-complete/switched-account-390px.png)

### 失败及复跑记录

失败证据全部保留，没有覆盖旧下载或截图来制造通过：

- 初次目录租约把同一进程测试客户端的模式切换当成另一个实例，旧账号测试 1 项失败；修正为同进程同数据集复用，跨进程仍由 OS 锁拒绝。[原记录](local-backup-guard-initial.xml)、[局部复跑](local-backup-guard-mode-final.xml)。
- 归档＋文件版本用例首先遇到 Windows MAX_PATH；缩短暂存名并加入预检查，未打开系统长路径开关。[原记录](local-backup-archive-final.xml)，最终同用例包含在 103 项通过中。
- 兼容账号库用例发现启动器仍对 root/accounts.db 初始化替代库；改用恢复清单的真实 registry，保留缺库／模式不符拒绝。[原记录](local-backup-special-final-v2.xml)，最终用例通过。
- 加入运行标记的实际路径字段时，局部函数名被同名变量覆盖，造成退出标记写入失败和一组合并回归的级联错误（1 failed、97 errors）；已更名修正，未跳过清理或放松租约。[原记录](local-backup-final-validation-v2.xml)、[最终 103 项](local-backup-final-validation-v3.xml)。
- 两次早期浏览器路线均在“终态后消息数量”断言处失败，下载部分已通过；随后数据库核查已保存助手消息。最初等待条件的精确时序原因未独立定位；最终改为对同一个真实 GET 结果轮询，要求状态、attempt、完整消息及草案同时可读，再继续断言。每次从同一备份恢复到**新的空目录**，没有手工补消息或修改产品消息发布代码。[第一次失败](local-backup-restore/browser/failure.json)、[第二次失败](local-backup-restore/browser-final/failure.json)、[完整复跑](local-backup-restore/browser-complete/browser-results.json)。这两次诊断不计为新增浏览器路线或通过项。
- 离线样本准备时也曾因 PDF 替身未覆盖真实解析分支、以及处理中会话互斥而中止；补齐离线解析结构并改用独立会话，保留产品的解析失败拒绝和会话互斥，没有放松保护。

## 可重复验收路线

使用新 PowerShell、仓库根目录和既有虚拟环境；不继承真实服务的数据配置。下列准备命令只允许 OS 临时目录下本轮前缀，自动执行真实 precheck／backup／verify／restore，不展示密码：

```powershell
$sraBackupReview = Join-Path $env:TEMP ('sra-backup-review-' + [guid]::NewGuid().ToString('N').Substring(0,10))
.\venv\Scripts\python.exe -m tests.local_backup_acceptance prepare --root $sraBackupReview --evidence docs/local-backup-restore/review-new
.\venv\Scripts\python.exe -m tests.local_backup_acceptance serve --root $sraBackupReview --port 8094
```

另一个终端执行（Node 环境需有 Playwright 和本机 Edge；当前工作环境通过本地运行时 NODE_PATH 提供，不是新增生产依赖）：

```powershell
$env:SRA_BACKUP_URL='http://127.0.0.1:8094'
$env:SRA_BACKUP_SHOTS='docs/local-backup-restore/review-new/browser'
node tests/local_backup_browser.cjs
```

该浏览器脚本在隔离上下文中使用夹具临时账号自动输入凭据，密码不写入命令行、日志或本文。人工验收可用本地管理命令在自己的新临时实例安全交互创建两个账号，再用各自已知密码登录、保存笔记／资料，执行相同停机、备份、恢复和越权步骤；不要把公众注册、密码重置或真实资料迁移引入本轮。自动样本的两个账号和所有数据都只在准备目录内。

需要重复浏览器路线时，先正常关闭该服务，再从原包恢复到新目录，例如 `--destination (Join-Path $sraBackupReview 'restored-second')`，启动夹具加 `--instance restored-second`；截图也选新目录。完成后：

```powershell
Invoke-RestMethod -Method Post 'http://127.0.0.1:8094/__backup__/shutdown'
# 等服务终端正常退出，再核对源数据未变；第二次目录请加 --instance restored-second。
.\venv\Scripts\python.exe -m tests.local_backup_acceptance finalize --root $sraBackupReview --evidence docs/local-backup-restore/review-new
```

普通 `tools.serve_local` 启动的实例在其终端 Ctrl+C 正常停止，不具备上面的测试控制端点。`__backup__` 只存在于临时验收夹具，产品应用没有注册它们。

专项及相关回归命令如下；账号／资源组与旧本地模式科研组使用独立进程，避免模块配置相互污染：

```powershell
.\venv\Scripts\python.exe -m pytest -p tests.resource_offline_guard tests/test_local_backup_restore.py tests/test_local_account_isolation.py tests/test_local_registration.py tests/test_resource_review.py tests/test_local_resource_protection.py tests/test_local_resource_restart.py -q --tb=short

# 另一 PowerShell，仅该进程采用明确的本地模式。
$env:SRA_AUTH_MODE='local'
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
.\venv\Scripts\python.exe -m pytest -p tests.resource_offline_guard tests/test_project_conversations.py tests/test_conversation_home.py tests/test_research_tasks.py tests/test_p1_recovery_attribution.py tests/test_research_delivery_closure.py tests/test_research_delivery_supplement.py tests/test_first_batch_reliability.py tests/test_product_plan_boundary.py tests/test_real_workflow_reliability.py tests/test_latest_artifact_download.py tests/test_paper_browser_import_download.py -q --tb=short
```

## 部署兼容与未验证事项

- 当前资源准入和任务状态仍是**单进程**设计，正式启动应使用 workers=1。数据目录租约会拒绝其他进程共享同一项目／账号数据；不能用多 worker 绕过限制。本轮没有 Redis、任务队列、服务购买或公网部署。
- 实测环境为本机 Windows、Python 3.13 和 SQLite；Linux **未实测**，Python 3.10、网络盘、云同步目录、容器卷及跨操作系统迁移均未实测。POSIX 锁分支沿用 fcntl，但不能据代码存在宣称跨平台验收通过。
- 路径映射不改模型或材料内容；只支持当前规范根目录和项目内部文件。外部附件引用、未完成首发关联、未知 schema、丢失版本文件等会阻止完整备份，不自动修复真实历史资料。
- 依赖及源码需要与备份的数据契约匹配。数据包不包含代码、虚拟环境、.env 或秘密；当前工作区有未提交代码，**单独保留 HEAD 并不足以重建当前功能**，后续部署应另行保存匹配源码和依赖配置。本轮没有提交／推送代码，也未引入新依赖。
- 没有在线热备、异机灾备演练、增量备份、自动计划、包加密、签名、保留策略、掉电／磁盘损坏／空间耗尽保证，也未做真实负载验证。失败可能留下暂存文件，工具不会自动扫描或递归清理它们。
- 未验证真实模型／公网文献／真实 PDF 解析服务／邮件。本轮离线替身结果不能证明科学正确性、真实模型恢复或公网安全性。原有存储累计配额、账号恢复、真实邮箱验证、HTTPS／Secure Cookie、反向代理与运维配置仍需后续独立处理。

## 本轮数据与服务状态

实际成功准备及各 CLI 输出见 [最终准备记录](local-backup-restore/final-v2/preparation.json)。最终浏览器使用该包的新空恢复目录 `restored-browser-final`；原先失败路线的恢复目录及下载／截图未覆盖。原 source 业务文件在服务正常退出后重新核对不变，恢复副本允许的新增笔记／明确重试不会写回 source。

本轮仅启动回环 8094 的恢复验收实例（先后使用不同临时恢复目录），以及资源既有重启用例自行管理的临时端口；已全部结束。最终只读检查未发现本轮测试／服务进程残留，也未发现所检查端口监听，[进程与端口记录](local-backup-restore/service-status.json)。8001／8002／8077 从未启停或占用。本文中的验收地址在最终交付时**已关闭**，需按上面命令重新启动，不是当前可访问的系统入口。

收尾还用当前代码对最终临时 source 仅执行正常生命周期开关，补齐管理标记的实际路径信息，自动执行数为 0；随后当前 precheck 与原包 verify 均通过，10 个业务文件哈希未变，[最终数据核对](local-backup-restore/final-data-check.json)。文档链接、去重计数和 12 份已保存下载哈希已再次核对，[交付核对](local-backup-restore/delivery-check.json)。

开发准备目录、备份和恢复副本只位于本轮 `sra-backup-*` 临时范围；pytest 临时数据按框架策略留存。本轮保留这些临时样本供只读复核，不递归清理既有工作区或历史项目，也未处理此前被安全策略拒绝删除的临时目录。没有新增清理拒绝或绕过策略的操作。证据目录不存账号库、会话令牌或密码，仅保存离线下载、截图、计数与校验信息。

既有资源重启回归自身会更新原 `docs/local-resource-protection/restart-results.json`；该 JSON 属于既有脚本的重复输出，本轮以独立保存的 JUnit XML 记录执行，不把它再累计为新证据或新用例。其余早期失败和下载证据目录均保留。夹具内置账号仅用于临时验证，不能直接用于真实部署。

本轮交付后停止，等待独立复核，不自动推进部署、邮件或真实调用验收。
