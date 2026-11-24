# 项目背景复用第一版

2026-10-30。本版实现的是**用户确认的项目背景复用**，不是自动记忆全部聊天或完整阅读全部文献。未增加记忆数据库、聊天自动总结、模型提取、研究功能或账号功能。

实施起点：`feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`；开始时 398 项既有工作区变更全部保留。仓库与上级未发现适用 AGENTS.md。未切换、重置、清理、提交、推送或部署。

## 原结构与实施范围

核对后确认：四项 Brief 原本已经持久化并进入指导／方案上下文；`save_brief` 原本具备全工作台 `expected_revision` 比较保存及正式材料待复核规则。实际缺口是背景不易查看／明确编辑，以及背景更改或清空后，同会话旧消息仍可能再次成为条件校验依据。

| 唯一持久化内容 | 页面名称 | 长度上限 |
| --- | --- | --- |
| `Workspace.brief.goal` | 研究目标 | 2000 字符 |
| `Workspace.brief.background` | 已有基础 | 2000 字符 |
| `Workspace.brief.resources` | 资源条件 | 2000 字符 |
| `Workspace.brief.time_budget` | 时间安排 | 1000 字符 |

项目总览新增“项目背景”，四项逐一展示，空值显示“尚未明确”。对话研究材料面板复用同一个组件与编辑器。对话输入上方可展开“本次讨论的背景与资料范围”，说明已保存背景、原正式记录、实际摘要／片段和本次临时说法的区别；没有自动开场消息。

正式问题、方案、笔记、文献仍引用现有记录，没有复制到第二套记忆。入库数量与材料目录不代表模型已经完整阅读。

## 用户操作

1. 点击项目名进入项目总览，在“项目背景”点击“编辑项目背景”；也可从对话的范围说明或研究材料面板进入。
2. 编辑器重新读取当前项目，预填全部四项。可以单独补充、修改或清空字段；未读成功显示错误与重试，不展示假空背景。
3. 点击“预览本次修改”，查看修改前／后和清空提示，再点击“确认保存项目背景”。取消编辑不发送保存请求。
4. 保存后显示服务器实际四项内容及待复核的问题／正式方案数量；在原记录中复核旧材料，不自动宣布仍然适用。
5. 在同一项目开启新对话，后续请求可参考当前保存的背景。聊天里的不同条件可以讨论，但不会自动覆盖背景；需要长期使用时应明确编辑保存。

浏览、打开编辑器、预览、取消、保存背景均不调用模型、检索或采纳接口，不创建会话，不改写历史聊天。原对话的输入文字与待上传文件选择不受编辑器刷新影响。

## 保存、冲突与未知结果

继续使用现有路由：

- `GET /api/projects/{pid}/workspace?include_chat=false`：只读当前项目背景和正式材料状态。
- `PUT /api/projects/{pid}/workspace/brief`：`{expected_revision, brief:{全部四项}}`。保留原来的**完整替换**语义，并非 PATCH；新界面总是从完整已知快照构建请求，不能将未编辑字段当作空值省略。

版本是全工作台版本，其他标签页修改材料也会引起冲突。409 后先读取最新版本，保留编辑输入，展示最新四项。用户点击“我已核对，保留我的修改”后，只把自己编辑过的字段叠加到最新快照，未修改字段保留他人的最新值；仍须再次预览、确认才写入。不会后台自动接受新依据。

发送前将原版本、完整拟保存内容和编辑输入保存在本账号、本项目、**本标签页**的 sessionStorage 恢复记录中，复用现有账号隔离封装。它只是浏览器未提交／待确认编辑状态，不是第二份服务端项目背景，不进入生成上下文，不属于服务端备份。关闭标签页、清理浏览器数据或浏览器崩溃时不保证保留，应自行另存重要未提交输入；不会声称已经保存。

保存结果未知时，关闭窗口不等于撤销。重开／刷新后先 GET 核对：

- 更高版本且四项与拟保存一致：显示当前内容已核对，不重复写入；提示不能单凭相同内容认定是哪次提交写入。
- 更高版本且内容不同：原版本保存不能再提交成功，进入明确冲突核对流程，保留输入。
- 仍是原版本和原内容：**不据此认定原提交一定未执行**。保留 pending；只提供用户主动的“用原版本重新提交本次修改”，提交前再读取核对，并只允许完全相同的原版本／原内容。现有事务 CAS 保证两次在途请求最多成功一次，不暗换新版本。
- 读取失败或内容不一致：继续保留输入与 pending，不盲目重交。

本地恢复记录损坏时阻止保存，不使用缺字段快照覆盖背景。账号切换沿用原会话校验、用户存储隔离和硬刷新；编辑器另有项目、窗口身份与读取序号检查／AbortController，迟到响应不能填入重开的其他项目或账号窗口。

## 当前背景与历史的边界

新增 `Workspace.brief_revision`，仅记录**最近实际更改 Brief 的工作台版本**，不保存第二份背景。旧数据默认为 0，无读取时迁移／补写；第一次明确修改才保存此元数据。原材料指纹的输入不包含该新增元数据，旧材料不会因读取默认值而被伪造为更新。

会话构造上下文时，用原请求的 `based_on_revision` 与最近背景更改版本比较，为旧片段添加临时 `background_outdated=true` 标记；无法定位版本的旧消息在已有背景变更时保守标为历史参考。标记不写回消息，不修改原请求和审计。

背景任一字段变化后，之前的同会话消息仍可作为历史参考，但不会经用户条件语料、路线条件列表或推荐回复事实检查补回为当前确认条件。保存中未修改的背景字段仍在 Brief 中有效。这个策略较保守，不尝试自动证明每句旧话的语义是否失效。当前新说法可以作为本次讨论假设，仍不会自动保存。

实际上下文增加 `project_background` 的来源／变更版本说明，保留现有稳定科研指导角色、模式与模型调用预算；方案生成的精简上下文也带上同一说明。新对话只携带自身最近历史，不拼接其他会话正文。移动对话的来源历史继续放在 `conversation.prior_project_history`，不是目标项目事实。

保存继续沿用原规则：实际 Brief 改变时 active 非 note 的正式研究材料标为 `needs_review`；不改正文、版本链、指纹快照或采纳状态。空字段不会从旧聊天补回为当前背景；旧方案内历史条件保留在原文并显示待复核。已提交的生成请求继续使用提交时捕获的上下文，不在保存后替换依据或自动重跑。

## 修改文件

| 文件 | 改动 |
| --- | --- |
| `web/static/project-background.js` | 共享背景展示、预填编辑、差异预览、明确保存、冲突核对及未知结果恢复 |
| `web/static/workspace-overview.js` | 项目总览背景区域，保存后重新读取实际状态 |
| `web/static/conversation-home.js` | 材料面板复用共享组件，增加可展开范围说明，保留输入与附件 |
| `web/static/index.html`、`ui-experience.css` | 加载共享模块，局部桌面／窄屏样式 |
| `core/workspace/schema.py`、`service.py` | 最近背景更改版本元数据；保留现有 CAS／needs_review |
| `core/workspace/conversations.py`、`guide.py` | 原请求版本关联、历史失效标记及实际上下文说明 |
| `core/workspace/plans.py`、`research_coach.py`、`guidance_policy.py` | 历史条件不再冒充当前条件；未重写稳定角色规则 |
| `tests/test_project_background.py` | 新增真实接口、上下文、并发与恢复专项 |
| `tests/project_background_test_server.py`、`project_background_browser.cjs` | 临时账号隔离实例和实际浏览器流程 |

原权限统一检查及 CSRF 保持，身份不由客户端字段决定。新背景没有匿名入口。备份直接保留原项目数据库中的 Brief 和元数据；恢复应使用本版或兼容的新代码，不保证旧代码读取新增字段。

## 验证结果与证据

所有 pytest 使用真实 FastAPI、临时 SQLite 和离线提供商边界；部分既有回归是规则／schema／HTTP 替身单元检查，不把它们统计成独立浏览器验收。

| 类型 | 去重用例数 | 结果 |
| --- | ---: | --- |
| 新增 `test_project_background.py` | 9 | 全通过 |
| 既有指导交互回归 | 33 | 全通过 |
| 既有第一批可靠性回归 | 18 | 全通过，包括两项已对齐的 structure_invalid 测试 |
| 既有 P1 恢复／依据归属 | 20 | 全通过 |
| 既有稳定科研角色 | 31 | 全通过，含 7 个纯检索意图判断用例 |
| 既有项目总览 | 8 | 全通过 |
| 既有账号、移动、会话选定回归 | 7 | 全通过 |
| 实际浏览器场景 | 11 | 全通过，单列，不与 pytest 重复累计 |

pytest 合计 **126 个不同用例 = 9 新增 + 117 既有**，0 失败／跳过。语法编译检查不计为测试用例。浏览器重复完整运行不累加数量。

专项包括：全四项读取／取消／保存／清空、跨会话当前背景、清空后旧条件不回流、事件控制的在途生成保持原依据、屏障控制的两次原版本保存仅一次成功、问题／方案待复核及未采纳草案不变、跨项目／双账号／移动历史、旧数据只读、正常重启与停机备份恢复不重放。旧请求／消息／调用审计行在更改背景时逐行不变。

浏览器实际执行：空状态取消无 PUT；预览后明确保存；单字段清空保留其他字段；材料面板共享编辑及文字／附件保留；新会话实际提供商上下文；双标签页冲突后手动合并；保存已成功但响应丢失后刷新只读确认；原 PUT 暂未到服务端且查询仍旧版本时保留 pending、明确重交相同 CAS、原迟到提交 409；读取失败重试；关闭后项目切换与账号切换的迟到读取隔离；390px 与键盘 Enter／Escape。

最终浏览器记录：9 次 PUT 请求（包括受控 409 和原请求核对），只在明确保存／明确原版本重交时发生；1 次离线提供商调用用于捕获新会话实际上下文，另有 2 次离线方案样本初始化调用，**均不是真实模型调用**。浏览器没有外部站点请求、pageerror；这些检查之外还核对了内容、版本、实际写入及输入状态，未仅以无 pageerror 为通过。

证据：

- [pytest 去重记录](project-background/pytest-results.json)、[新增专项 XML](project-background-tests.xml)、[指导／P1 XML](project-background-regression.xml)、[边界回归 XML](project-background-boundary-regression.xml)。
- [实际生成器上下文](project-background/actual-contexts.json)：新会话 history=[]，brief 为当前保存值，project_background 标明唯一来源；records 保留原正式记录及 needs_review，papers 仍标实际摘要范围。离线回复是软件占位示例，不代表真实模型记忆质量。
- [浏览器结果](project-background/browser-results.json)。
- [桌面修改预览](project-background/preview-desktop.png)、[双标签页冲突](project-background/conflict-desktop.png)、[对话材料与原输入／附件](project-background/shared-material-editor-desktop.png)、[390px 预览](project-background/preview-390px.png)、[390px 可操作确认区](project-background/confirm-controls-390px.png)、[B 账号独立总览](project-background/account-b-desktop.png)。均已实际打开查看；窄屏编辑器可滚动，未出现横向溢出。

失败／复跑记录：第一次浏览器脚本尝试定位常驻 file input，现有附件选择实际使用动态 input，因此测试工具定位超时（见保留的 `project-background/failure.png`）。改为既有“资料 → 添加待上传论文”的实际选择流程，未删除任何输入／附件断言；之后完整专项通过。随后补充账号迟到读取与窄屏确认区证据，使用新临时样本完整复跑仍通过。没有功能断言失败后放宽校验。pytest 初跑均通过；既有 asyncio_mode 配置警告及 utcnow 弃用警告保留，未作为本轮无关改造。

## 可复现路线

在仓库根目录运行，使用项目 venv，所有环境参数仅作用于该 PowerShell 进程。不要替换为真实数据根目录。

```powershell
$env:SRA_AUTH_MODE='local'
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_project_background.py
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_research_guidance.py tests/test_first_batch_reliability.py tests/test_p1_recovery_attribution.py tests/test_research_partner.py
```

选定边界回归的完整 nodeid 列表见 `project-background-boundary-regression.xml`。它包含总览全组、账号真实资源及会话重启、移动及移动备份、会话共享材料／在途材料变化／旧历史读取，合计 15 个用例。

启动仅供验收的临时实例（端口预先确认空闲），在另一终端执行浏览器脚本；本 fixture 自动建立两个临时账号，账号秘密只用于测试内存输入，不打印或放入命令行／报告：

```powershell
$bgRoot=Join-Path $env:TEMP ('sra-background-' + [guid]::NewGuid().ToString('N').Substring(0,8))
venv/Scripts/python.exe -m tests.project_background_test_server --root $bgRoot --port 8098
# 另一终端
$env:NODE_PATH='C:\Users\czh05\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
node tests/project_background_browser.cjs
# 完成后，请求本 fixture 的关闭接口
venv/Scripts/python.exe -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8098/__background__/shutdown',method='POST')).read()"
```

fixture 接口只存在于临时验收脚本中，产品应用未增加此类接口。手工复核可在运行中的隔离实例使用临时账号：进入总览编辑两项、预览保存；另开同项目对话核对背景；两标签页同时编辑观察冲突；清空资源后检查总览及下一次生成器 trace；切到另一个项目／账号核对四项独立。不要把验收地址当作常驻系统。

## 服务与限制

本轮三个隔离根目录保留作证据：

- `%TEMP%/sra-background-cad3c988`：首次浏览器工具定位失败后的样本。
- `%TEMP%/sra-background-99918856`：首次完整通过样本。
- `%TEMP%/sra-background-5d18f4a0`：补充后最终完整通过样本。

交付时三个 fixture 均已正常关闭，8098 不再监听；`stopped.txt` 为本脚本停止标记。8001／8002 未占用或启停；8077 的既有服务 PID 28836 保持不动。未读取／修改真实账号和资料，未操作此前安全拒绝清理的目录，未删除本轮证据目录。

真实模型、文献源、邮件、外部解析调用均为 **0**。没有使用先前体验轮的真实模型费用授权。

未验证／不能保证：真实模型是否每次正确理解清空与历史参考标记、真实科研教学质量、全部聊天语义推断、全部文献完整阅读、浏览器清除本地存储后的未提交编辑恢复、旧版代码对新增元数据的前向兼容、跨设备同步未提交草稿。本轮离线证据证明规则接入、当前数据传递及持久化／恢复约束，不替代少量真实模型质量复核。未进行全系统重验、线上负载或公网安全认证。完成后停止，等待独立只读复核。
