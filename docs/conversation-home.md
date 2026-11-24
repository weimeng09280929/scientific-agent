# 中央科研对话主页

2026-10-23 第二批已将全局“新研究”和“在本项目新建对话”分开，增加首次待上传资料与恢复、教学转入交接说明、统一材料目录和项目任务活动。当前使用与接口约定见[第二批交付](second-batch-workspace-unification.md)，下文早期步骤记录保留，不代表仍使用同一个“新建对话”按钮承担两种归属。

当前界面规则已在第八步 B 更新，见[界面布局与使用体验统一](ui-experience-unification.md)。以下第三步／第四步 A 的实现与验收记录作为历史保留；默认列表条数、输入与阅读布局以第八步 B 为准。接口旧默认值保持兼容。

第八步 B：当前项目资料固定在上方，最近对话与项目各默认 5 条、共用下部历史滚动区；低频菜单、讨论设置与四阶段导航按需展开。普通与教学方案共用摘要、材料审阅及顶部版本下载，详细方案页可进入同一宽阅读组件。新研究首次发送需明确确认领域，不再默认提交 computer_science。历史名字不改写，新名字本地提取短主题或采用可编辑临时名，不增加模型调用。

只读 `GET /api/conversations/recent` 新增可选 `q`（最多 200 字符），能力由 `/api/conversation-capabilities` 的 `conversation_search` 声明；匹配会话及项目名称／主题／编号，在库内筛选后分页，不读取全部聊天或触发迁移。服务端其他状态、材料版本、幂等、采纳与 DESIGN 上限不变。8001 在验收期间仍为旧后端，最终复核已无监听；本轮未自行启停，详见第八步 B 交付状态。

本步把现有主应用的项目总览改为科研对话主页，接通项目与多会话导航；不新增执行流水线，不触发自动检索，不制作教学案例。研究流程仍为：**明确目标与条件 → 了解研究现状 → 确认研究问题 → 形成并审阅方案**。

依赖：[产品阶段边界](product-plan-boundary.md)、[项目内多会话契约](project-conversations.md)。本步不改变历史生命周期状态，不新增实验结果、研究判断或写作骨架。

## 1. 实际入口与导航

`GET /` 与 `GET /workspace` 都返回 `web/static/index.html`。实际改造文件是这个 HTML、原生 `app.js`、导航定义，以及增量的 `conversation-home.js` / `conversation-home.css`；没有改造未使用的 `workspace.html` / `workspace.js`，也没有孤立演示页或新前端框架。

- 中央对话包含空态开场、三种可编辑入口、当前项目／会话名、服务端阶段提示、分页消息与底部输入框。三种入口和“新建对话”只准备草稿，不创建实体，不发送消息。
- 设置包含指导方式、进入阶段和讨论目标；已保存会话使用 PATCH 持久化。未创建会话只保存在本机草稿中。进入阶段不替换正式问题、方案或进度。
- 主导航现为新建对话、独立的最近对话／项目分组、当前项目资料（文献、方案、笔记）、待确认事项。更多菜单保留研究指南、任务活动、选题探索、研究现状、研究问题和历史材料，旧 page 链接仍可使用。
- 最近对话不再嵌在某个项目下面。点击项目打开共享资料摘要及该项目的分页会话选择；不会自动挑选、创建对话或运行研究。会话重命名在项目摘要和当前会话标题中操作。没有新增会话删除或复杂文件夹。
- 右侧旧项目级聊天在主应用中停用，避免与选中会话混合；其他页面通过“返回科研对话”进入当前会话。旧 `/workspace/guide` 后端兼容接口仍保留，但新客户端不向它降级。
- 桌面材料面板可收起，窄屏使用全高材料抽屉，提供“返回对话”。参考论文可选择至多三篇，论文导入复用原文献页面。现有记录的“讨论”入口会带入参考记录，不自动采纳或切换正式主线。

共享材料从 `/api/projects/{pid}/workspace?include_chat=false` 获取，详细页面的共享材料 GET 也不再默认夹带项目级全部聊天。阶段显示只使用 `product_stage`、`product_boundary`、`workflow.flow.planReady`，不从对话内容、进入阶段或计数推断完成。

## 2. 首次发送及幂等创建

创建与消息发送使用两个独立的稳定 request_id。界面打开、输入、设置和项目浏览均不会自动创建空项目或空会话。

1. 首次发送时冻结创建资料和首条消息，保存两个 request_id。
2. POST `/api/conversations/start`，选择已有项目，或携带新项目的短标题与领域。领域选项来自能力接口，标题直接截取首条输入，不调用模型生成标题。
3. 返回项目和会话 ID 后更新 URL、读取共享材料，再用消息 request_id 调用第二步消息接口。
4. 模型失败保留已创建的项目、会话和用户消息；只重试原消息，不再次创建。
5. 创建响应丢失后，页面保留原创建请求，用户点击“核对并继续首次发送”可继续；刷新也不会自动重新创建。

开始请求保存在数据目录 `projects/.conversation-starts.db`，不是浏览器事实或内存缓存。该表在首次有效写入时创建；能力检测和首页 GET 不初始化它。唯一键是 request_id，保存输入指纹、保留的 project_id、conversation_id 和创建阶段（reserved / project_created / ready）。备份部署数据时应包含此文件，不能只备份单个项目文件夹。

同一编号不同有效内容返回 409。相同内容复用保留身份；同步启动的同号请求可能暂时收到 409“正在处理”，用户仍使用原编号继续核对。创建中断不删除部分成果：空的部分初始化库、已经保存项目资料、已经创建会话等窗口均可接着完成；会话创建复用第二步服务和其创建幂等键。已完成初始化后数据库缺失或项目被归档，原请求返回 404，不重新建立替代库或项目。

启动过程不调用模型，不启动研究流程，没有通用队列，也没有持有一个 SQLite 写事务等待另一个数据库或模型操作。消息和采纳的可靠性仍由第二步服务负责。本地幂等不等于提供方计费或外部调用 exactly-once。

**创建竞态排查与修复记录（第五步收尾，2026-10-21）**：`test_same_start_race_is_controlled_and_eventually_returns_one_identity` 在多文件组合负载下的偶发失败已完成排查与修复。

- **复现**：竞态用例置于组合负载首项时 100% 复现——并发第二个请求返回 503"开始对话的记录暂时不可用"。
- **根因**（逐层打点定位）：Windows + SQLite 3.53 在并发首次创建 starts 库的瞬态窗口内，把新连接误分类为只读（`attempt to write a readonly database`，发生在 `CREATE TABLE IF NOT EXISTS`；现场：0 字节主库 + 残留空 `-journal`、文件可写、属性正常）。`timeout=15` 对此无效，与锁竞争无关。
- **修复**（`ConversationStarter.transaction()`）：建表阶段（均在调用方代码之前）对 readonly/locked/busy 三类瞬态错误做最多 3 次有界重试（0.15s 递增）；重试后第二请求落到幂等 200 或 409，语义不变。保护链本身（`uuid5(request_id)` 确定性项目 ID + 事务预留 + OS 锁 + phase 状态机）保持完整。WAL 方案因 ro 预检查连接泄漏 `-wal`/`-shm` 边车文件被否决回退。
- **验证**：修复后组合负载 3/3 轮 200+409 零 503；单文件 5 轮全过；9 文件相关回归 208 项全过。

## 3. 新增的最小后端接口

### 能力与领域

```http
GET /api/conversation-capabilities
```

```json
{"version":1,"project_conversations":true,"durable_start":true,"message_tail":true,"max_stage":"design","domains":[{"value":"ai","label":"AI 与机器学习"}]}
```

实际 domains 返回现有后端公开选项，不在本步扩大范围。能力接口缺失时，前端还要查看运行中后端的 `/openapi.json`，确认确实没有该路径后才提示更新／重启。任意项目、数据库或会话 404 都不能替代能力检测。核对服务失败则提示连接问题，不能静默退回旧聊天。

### 开始对话

```http
POST /api/conversations/start
Content-Type: application/json

{
  "request_id":"create-stable-id",
  "project_id":null,
  "title":"我想研究联邦学习中的隐私问题",
  "domain":"ai",
  "conversation_title":"讨论研究问题",
  "guidance_mode":"beginner",
  "entry_stage":"question",
  "discussion_goal":"明确当前条件下的问题范围"
}
```

```json
{"request_id":"create-stable-id","project_id":"proj_…","conversation_id":"…","conversation":{"conversation_id":"…","project_id":"proj_…","title":"讨论研究问题"}}
```

已有项目传实际 project_id；本步没有账号／权限层，项目归属仍由服务端项目数据库和会话服务核验。不存在的项目不能借此写入新库。

### 项目显示名称

```http
PATCH /api/projects/{pid}/identity
Content-Type: application/json

{"display_name":"联邦学习 · 问题与方案讨论"}
```

名称存于 `project.display_name`，项目列表额外返回 display_name。它只改变展示名称，不覆盖 research.topic、brief、正式主线或材料版本。旧项目没有该值时继续显示原 topic。会话重命名使用第二步 PATCH 接口。

### 消息最新页与前向历史

- `GET …/messages?tail=true&limit=30`：最近一页，仍按 seq 升序返回。
- `GET …/messages?before={next_before}&limit=30`：加载更早的一页。
- `GET …/messages?after={last_seq}&limit=100`：补读新的消息，旧行为不变。
- 每页上限仍为 100，tail / before / 非零 after 不能混用，返回新增 next_before；旧 next_after 字段保留。
- 发送和状态响应中的消息也返回同一 seq。旧 JSON 历史只读虚拟分页，首次明确写入转换后 ID、顺序与游标保持一致。

## 4. URL、草稿和异步隔离

```text
/workspace?page=dashboard&project={pid}&conversation={cid}
/?page=dashboard&project={pid}&conversation={cid}
```

项目和会话均采用服务端返回的 ID，不拼历史 ID。导航使用浏览器历史记录；刷新、前进／后退和原 `page` / `project` / `paper` / `exp` 链接继续有效。没有 project 的首页默认开始新研究。

本机草稿按 project / conversation 分开保存，未建立的项目和对话各有独立草稿键。浏览器只保存草稿、设置草案、创建参数和待核对的 request_id；正式消息、材料和采纳结果以服务端为准。

每次挂载对话拥有独立视图对象和只读请求 AbortController。切换时清除旧消息状态轮询，取消只读请求；生成 POST 不附加该导航取消信号。晚到回复只有仍属于当前视图才能更新 DOM，不能把 A 的回复写入 B。离开页面不会提示“服务端生成已取消”。

发送后及时显示待提交／处理中的用户消息，不伪造助手回复或进度百分比。消息按 message_id 去重，按 seq 排序；首次只读取最近页，更早历史由用户加载。材料和状态无变化时不重建消息列表；正在阅读历史不会被轮询拉到底部，“查看最新消息”会重新定位最近页，旧消息仍可重新向前分页读取。材料刷新保留展开和滚动位置。创建或保存设置的等待期间继续输入的草稿不会被原请求清空；刷新核对待处理请求时，不重复插入已有的用户消息。

Ctrl / Cmd + Enter 可发送，中文输入法组合期间不会触发；输入、导航和按钮可由键盘操作，并保留焦点轮廓。窄屏输入区使用实际占位的底部布局，而不是覆盖消息的浮层。

## 5. 请求状态与建议保护

| 情况 | 客户端处理 |
| --- | --- |
| processing / 202 | 显示处理中，查询原请求；同会话不再发起另一条生成。 |
| failed / interrupted | 保留用户消息与原因，只有用户点击才调用 retry。 |
| 超时／网络响应丢失 | 先查询原 request_id，不能自动换编号或重发模型；已完成则展示已保存结果。 |
| 尚未查到原消息请求 | 保留待核对记录，提供明确的原请求重新提交操作，不自动提交。 |
| 409 | 保留输入或草案，提示冲突并刷新相关材料／会话，不偷偷改版本采纳。 |
| 404 | 区分项目不存在、数据库缺失、会话归属错误、消息请求未登记；不据此自动建库或判为旧服务。 |
| 503 | 先核对原消息状态，避免盲目重复写入。 |

材料面板只显示当前会话尚未采纳的建议。缺少 material_fingerprint、basis_changed 不是明确 false、后续结果／判断／写作类型的建议都不能直接采纳。点击有效草案时，还会重新读取服务端材料与建议状态，再提交原子采纳请求；因此正文片段变化不能靠 expected_revision 不变绕过保护。

采纳成功按服务端 item_id 去重，刷新共享材料与 workflow。方案可审阅只表示方案整理状态，不表示实验完成、研究完成或结论得到验证。旧论文、实验、稿件和版本仍可查看和导出。

## 6. 验证与截图

测试使用真实 FastAPI 路由、临时 SQLite 和离线指导夹具，不把主要会话接口 mock 成静态响应。浏览器的响应丢失／超时在网络层注入，底层仍调用真实接口。旧服务检测使用一个确实缺少新路由的独立 FastAPI 子应用。

测试入口：

- `tests/test_conversation_home.py`：开始请求幂等、同号冲突、进程注册表重建、并发、部分项目与会话创建恢复、显示名、只读兼容、缺失／归档数据保护及分页。
- `tests/test_project_conversations.py`：复测第二步隔离、版本、事务、失败重试和材料依赖。
- `tests/test_product_plan_boundary.py` 及工作台等回归：复测 DESIGN 上限、历史读取与状态一致性。
- `tests/conversation_home_test_server.py` + `tests/conversation_home_browser.cjs`：主应用真实浏览器流程，1440×900 与 390×844，检查控制台未处理异常与截图。
- `tests/conversation_home_draft_browser.cjs`：延迟真实创建／设置响应、生成中刷新、草稿保护、临时消息去重，以及最新页定位后继续分页。

2026-10-20 验收结果：

- 合并第二步会话、第一步边界、工作台、恢复、版本、历史导出和存储等回归，324 项 Python 测试通过；其中第三步专项 14 项也已单独复测通过。
- 另外 5 项既有文献导入／PDF 下载离线回归通过，未访问真实文献站点或调用模型。两批合计 329 个不同 Python 用例，无失败或跳过；仍有既有弃用警告。
- 44 项 JavaScript 状态检查通过。新增 Python 文件格式检查、相关 Python 编译和 JavaScript 语法检查通过。
- 主应用浏览器验收与异步草稿追加验收均通过。覆盖空态不写入、首次创建去重及响应丢失后刷新、项目／会话切换与历史导航、迟到回复、真实请求超时后的状态核对、显式重试、中断恢复、77 条历史分页、受保护采纳与正文片段变更、缺失库、旧服务检测、设置保存、重命名、项目归档及会话分页。
- 1440×900 和 390×844 均无横向溢出，输入与返回按钮无明显遮挡。浏览器 `pageerror` 为 0，没有未处理异常；故障注入会产生预期的网络中止／404 日志，不将其冒充真实模型质量测试。

截图均来自临时软件夹具，不是真实科研结果或预置教学案例。独立验收服务完成后关闭，8001 主服务未重启。

| 画面 | 截图 |
| --- | --- |
| 首页空态（桌面） | [empty-desktop.png](conversation-home/empty-desktop.png) |
| 已有对话（桌面） | [conversation-desktop.png](conversation-home/conversation-desktop.png) |
| 材料面板（桌面） | [materials-desktop.png](conversation-home/materials-desktop.png) |
| 已有对话（窄屏） | [conversation-mobile.png](conversation-home/conversation-mobile.png) |
| 材料抽屉（窄屏） | [materials-mobile.png](conversation-home/materials-mobile.png) |
| 首页空态（窄屏） | [empty-mobile.png](conversation-home/empty-mobile.png) |

复测前设置 `SRA_WEB_SKIP_GUARD=1`、`SRA_WEB_SKIP_PROJECT_SCAN=1`、`SRA_DRY_RUN=true`，Python 测试会自行创建临时目录。浏览器单独启动 `venv/Scripts/python.exe tests/conversation_home_test_server.py --port 8013`，再运行 `node tests/conversation_home_browser.cjs`；Node 需能解析已有 Playwright，脚本使用本机 Edge。不要把该验收服务的数据目录设为用户项目目录。

## 7. 主服务现状、限制与后续步骤

第三步交付时，8001 尚未加载多会话后端；随后用户明确授权重启，已核对 capability 和 start 路由生效。第四步 A 开始时的进程为 13672，确实已加载第三步，并非“新静态页面连接旧多会话后端”造成项目缺失。第四步 A 期间没有停止、启动或重启该主服务；用户要求继续后再次只读探测，8001 已无监听，HTTP 连接被拒绝。因此**第四步 A 新后端尚未在 8001 生效**，不能把磁盘静态更新或隔离服务测试描述为主服务完成部署。

尚未验收真实提供方质量／计费、公网部署、账号权限、多机共享盘锁、大规模并发、完整跨浏览器或真实移动设备软键盘。会话设置只复用现有指导生成能力，不宣称新的教学或学科专属能力。第四步 A 为项目列表补充有界服务端分页、搜索及活动排序；跨用户隔离仍是未来部署工作。旧库若缺少读取必需的表或已损坏，应报告不可读取而不是自动补表修复；不承诺恢复任意损坏数据。

留待第四、五、六步进一步确认和实现的功能包括：动态下一步与提示词优化、自动检索和证据增强、教学案例及跨学科模板。本步不提前实现这些内容，也不改变当前止于研究方案的产品边界。下一步可直接依赖：真实主页入口、幂等开始接口、项目／会话 URL、多会话消息与恢复状态、共享材料接口和受保护的建议采纳。

未调用真实或付费模型，未运行实验，未对用户项目写入测试，未提交或推送代码；保留已有未提交工作。

## 8. 第四步 A：项目识别、导航、产物与清理

仅处理列表真实性、导航组织、下载及可恢复归档；没有实施第四步 B 的开场白或动态科研引导，也没有自动检索、教学案例或实验执行。

### 8.1 近期项目未显示：实际原因与证据

只读核对的代码根目录为 `C:\Users\czh05\Downloads\scientificResearchAgentSys\scientificResearchAgentSys`，项目根目录为其 `projects\`。8001 进程命令行为该仓库 `venv\Scripts\python.exe -m uvicorn web.api:app --host 127.0.0.1 --port 8001`；配置 `ProjectPaths.default()` 也指向同一仓库。上层工作目录没有另一份 `projects\`，未扫描整台电脑，也未切换配置目录。

该目录实际存在 153 个项目数据库，运行中 `/api/projects` 也返回 153 个。近期真实项目 `proj_20261014_181948_2e33e5`（面向非独立同分布与客户端异构的个性化联邦表征学习与采样策略）就在该目录；其 `knowledge.db` 的 artifacts 保存方法文档 v3，时间 `2026-10-17T14:24:50.045019`（存储 UTC）。没有丢失或重建项目，也不是临时验收项目。

原恢复代码遇到 validate_* 等不能解析日期的编号时，以服务启动时的 `utcnow()` 填 created_at；这些历史热电项目因此冒充 10 月 20 日新项目，按 created_at 倒序挤满侧边栏前 6 条。另一处 `ts[:13]` 还截错了正常 `YYYYMMDD_HHMMSS` 的长度，真实编号 18:19:48 曾显示为 18:01:09。前端既没有展开分页，也没有把当前项目固定显示，放大了问题。URL 示例还保留着较早的 `proj_20260916_090459_bb40d6`；它只是浏览选择，不应决定全局排序。

修正后的纯只读投影，以真实存储活动排序，该联邦学习项目在当时 153 个项目中为第 4 位。热电历史项目继续可搜索和访问；没有按领域、名称或日期隐藏和删除。只修正返回投影，不批量重写历史数据库的时间或阶段。

### 8.2 排序、分页及接口

- 项目活动来自已保存的数据库 created_at / updated_at、工作台内原消息／记录时间；包括材料、聊天和显示名称的实际保存。单纯打开页面不写入活动时间。会话 updated_at 同时包含消息和会话设置的保存，不等同于“最后发消息时间”。
- 创建时间优先使用显式 project.created_at；旧带时间编号完整解析为本地 +08:00 后归一为 UTC；其他旧项目取可读取的最早持久化时间。没有可信时间保持空值，不填当前时间。最后以稳定项目 ID 排序。
- 项目按 activity_at、created_at、project_id 倒序；会话按 updated_at、project_id、conversation_id 倒序。只读缓存用 DB/WAL 文件签名失效，文件 mtime **不参与活动时间计算**。缓存只在内存中，不修改项目。
- 最近对话默认至多 8 条，每次“查看更多”增加 8 条，带项目名及简短项目标识。当前会话不在第一页时单独投影并替换该页末条，不把项目伪装成对话。
- 项目默认至多 6 条，当前项目可固定在这 6 条内。展开先读取 12 条，之后每次增加 6 条；折叠恢复 6 条。展开状态仅保存在本机。侧边栏搜索与管理搜索均匹配显示名、原主题和编号。
- 管理全部项目每页 10 条，服务器搜索、分页；不在浏览器一次加载 153 个项目或逐个发 HTTP 请求读取所有项目会话。

新增／扩展的只读接口：

```http
GET /api/projects?limit=6&offset=0&q=联邦&include_project_id=proj_example
GET /api/conversations/recent?limit=8&offset=0&include_project_id=proj_example&include_conversation_id=cid
GET /api/projects/proj_example/exports?limit=30&offset=0
GET /api/projects/proj_example/exports/items/item_id?format=md
GET /api/projects/proj_example/exports/artifacts/artifact_id?format=md
```

项目列表保留 projects 和所有旧记录字段，新增 activity_at、activity_source、data_status（available / missing_database / unreadable_database）、total、matched_total、next_offset、current_project。不带 limit 的旧列表调用仍兼容；新 UI 总是传有上限的 limit，最大 100。不存在的 current_project 为 null，不补建。

最近会话返回 conversations、next_offset、current_conversation、unavailable_projects、sort_basis，默认 8、最大 50；current_conversation 必须核验项目归属。聚合只在既定项目根目录下逐库读取有界元数据，不读取每段聊天全部消息，不建库、建表或导入旧历史。缺失／损坏项目不变成空库，返回不可读说明；旧历史使用第二步服务给出的稳定 ID。

能力接口 version 仍为 1，新增 catalog_navigation / project_exports 布尔标记。缺少这些特定能力时准确提示更新后端；项目或产物自身的 404 仍按缺失数据处理，不能一律说重启。新前端不向旧聊天降级。

### 8.3 下载当前产物

侧边栏快速下载区域已移除。主对话顶部的“下载当前产物”按需展开，明确指**当前项目及选定正式方案**，不是只导出本段对话。项目切换会卸载旧菜单、取消读取；晚到响应必须通过视图和请求编号检查，不能把 A 产物放进 B。

exports 返回真实可读条目及服务器给出的 url、formats、group、version、status_label、recommended；列表默认 30 条、最大 100。包括当前正式方案、项目研究记录、已有研究现状；实际存在的方法文档按版本显示，其他版本和产物放次级组，实验记录与稿件放“历史产物”。没有材料显示空态，不调用生成接口、不下载占位文档。

正式方案选择始终取服务端 workflow.flow.plan，不取前端计数或“最后生成文档”。只有 planReady 才标记可审阅；上游变化、来源或方案待复核时同步保留复核提示。未正式关联的方法文档明确写“未确认为当前正式方案”。方法组先选可读取的最高版本，再比较组间保存时间；新版本内容缺失时回退条目明确标注“最新可读取版本”，不宣称缺失版本已有效。记录没有独立数字版本号时展示材料版本与记录 ID，不编造 v1。

产物版本下载按项目内 artifact_id 精确读取，不因新版本出现偷偷下载另一个版本；方法来源沿用原系统的 artifact_group 关联，不把组 ID 错当版本 ID。只允许本项目 artifacts 目录内的引用文件，也兼容原仓库相对路径和组内相对路径。记录导出保留状态、needs_review、关联、来源与原文；产物保留版本、source_stage、created_by。目录中的旧汇总下载通过原接口可选 preserve_status=true 保留历史说明及已有 mode / needs_review / 教学演示等来源标记；不传该参数的旧客户端正文仍兼容。旧结果、预期／教学文字和稿件内容不被改写成真实实验。原有下载及 workspace/export 接口仍保留。

复用真实支持的格式：工作台记录 md/json；方案与产物 md/docx/pdf（沿用原转换）。Markdown 公式源码保留，原页面仍可用净化 Markdown、数学公式预览与打印；Word/PDF 复杂公式、表格及字体的既有格式限制没有在本步被全面修复。

### 8.4 清理与恢复

管理页保留一个明确选择的项目 ID 集合；翻页和更换搜索不会新增选择。“全选当前页”只操作当前页；“选择全部搜索结果”必须主动点击，分批读取后冻结当时可归档的 ID，不会把之后新增匹配项加入。每批最多 200 个。可查看所选数量及全部名称、逐条或全部取消；确认摘要列出项目并警示是否包含当前项目。

归档复用 POST /api/projects/batch-delete，服务端重新检查全部 ID，运行中、待人工确认、方法重生成或仍持有消息生成锁的项目会拒绝整批。失败不扩选、不降级物理删除。当前项目被归档后回到无项目对话空态，列表与最近对话刷新。真实用户项目未被归档；批量行为只在临时目录验证。

归档目录是仓库根下 `.project-trash/<archive_id>/<project_id>/`，同批 manifest.json 保存明确项目集合；文件并未抹除。目前**只有手工恢复，没有网页撤销按钮**：先停止服务，核对 manifest.json，将所需项目目录原样移回 projects/，已有同名目录时不得覆盖，再启动服务恢复扫描。库损坏或状态无法核验的项目不被自动修复后归档，需人工核对。

### 8.5 部署与后续约定

本轮未修改 .env 或数据路径，未重启主服务。用户确认需要加载第四步 A 后，可在上述正确仓库目录启动：

```powershell
Set-Location 'C:\Users\czh05\Downloads\scientificResearchAgentSys\scientificResearchAgentSys'
.\venv\Scripts\python.exe -m uvicorn web.api:app --host 127.0.0.1 --port 8001
```

仅在端口空闲时启动，已有主服务时应先由用户确认停止旧实例。不要带测试用 SKIP_SCAN / SKIP_GUARD 标记，也不要把临时验收目录配置为真实项目目录。启动后核对 capability 的 catalog_navigation / project_exports，以及 /api/projects 的 activity_at；静态文件变化本身不能证明新接口生效。

第四步 B 可以依赖当前 URL 的 project/conversation、会话设置与隔离历史、正式共享材料及 workflow、活动排序与分页导航、只读产物目录及精确版本导出；仍须遵守 DESIGN 上限和已有草案采纳保护。第四步 B 尚未开始。

### 8.6 第四步 A 验证与截图

使用真实 FastAPI、临时 SQLite 和离线模型夹具；真实用户项目仅用于只读定位。没有调用付费／真实模型、执行实验、归档真实用户项目、提交或推送。

- 一轮组合回归 201 项通过（项目管理、归档、下载、工作台、会话、版本、继续、边界、历史页面和公式）；补强后的导航／导出、缓存、坏库及边界组合 69 项通过；会话及首页等追加 62 项通过；最后 5 项来源、相对路径与导出核对通过。多轮存在重叠，合计覆盖 **210 个不同 Python 用例**，无未修复失败或跳过。仍有既有 asyncio 配置及 datetime 弃用警告，未隐藏。
- 44 项 JavaScript 状态检查通过；新增 Python 文件 Black 检查、相关 Python 编译、JavaScript 语法和相关 tracked 文件 diff 空白检查通过。
- `tests/project_navigation_browser.cjs` 使用 `tests/project_navigation_test_server.py` 的 153 个临时项目，验证默认上限、分页／搜索／展开／折叠、独立最近对话、跨项目同名会话、当前项目固定、项目摘要、刷新与前后导航、精确版本下载、无产物空态、迟到 A 下载响应不污染 B、缺失库、固定 ID 多选、取消确认和受保护项目。还实际点击链接接收浏览器下载流，核对文件名和 v1 正文；通过 UI 明确归档一个临时项目，以及归档当前临时项目后退出到无项目空态。核心接口不是静态 mock；只对真实响应做延迟，另验证加载中不冒充无项目。
- 原第三步 `conversation_home_browser.cjs` 和 `conversation_home_draft_browser.cjs` 也已在隔离服务复跑通过：保留消息幂等、会话隔离、晚到响应、失败与中断、显式重试、77 条消息分页、受保护采纳、旧服务识别、异步草稿与最新消息定位。
- 1440×900 与 390×844 均检查无横向溢出、输入和关闭按钮可达，窄屏下载面板不覆盖输入区；浏览器未处理异常为 0。以下截图已人工查看导航、下载和管理布局。截图来自软件夹具，不是教学内容或科研成果。

| 第四步 A 画面 | 截图 |
| --- | --- |
| 桌面导航 | [navigation-desktop.png](conversation-home/step4a/navigation-desktop.png) |
| 桌面下载展开 | [downloads-desktop.png](conversation-home/step4a/downloads-desktop.png) |
| 桌面项目清理选择 | [management-desktop.png](conversation-home/step4a/management-desktop.png) |
| 窄屏导航 | [navigation-mobile.png](conversation-home/step4a/navigation-mobile.png) |
| 窄屏下载展开 | [downloads-mobile.png](conversation-home/step4a/downloads-mobile.png) |
| 窄屏项目管理 | [management-mobile.png](conversation-home/step4a/management-mobile.png) |

复测入口：

```powershell
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
$env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest tests/test_project_navigation.py tests/test_project_archive.py tests/test_project_conversations.py tests/test_product_plan_boundary.py tests/test_latest_artifact_download.py -q
# 浏览器专用服务仅使用临时目录；另一个终端运行对应 cjs。
venv/Scripts/python.exe tests/project_navigation_test_server.py --port 8014
# 需本机已有 Playwright（脚本使用 Edge）；浏览器驱动默认连接 8014。
node tests/project_navigation_browser.cjs
```

没有验证真实模型质量／费用、公网或多机部署、其他浏览器／真实手机软键盘、大规模跨进程负载，也没有全面修复或逐份视觉验收历史 Word/PDF 文件。当前归档恢复仍是手工，不宣传一键撤销。只有既定目录中的项目参与聚合，没有跨盘查找或自动合并其他数据目录。

交付时已关闭 8014 隔离验收服务。8001 最后只读复核仍无监听，本轮未自行启动；当前磁盘代码可用，但第四步 A 尚未在用户主服务加载。真实项目数据库仍为原目录中的 153 个，本轮没有清理、移动、合并或重建它们。
