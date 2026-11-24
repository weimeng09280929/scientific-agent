# 项目内多会话：数据与接口约定

本文件描述第二步的后端底座，不包含首页、侧边栏或会话切换 UI。仍遵守 [研究方案流程边界](product-plan-boundary.md)：最多到 DESIGN，历史执行阶段不改写，不新增实验结果、研究判断或写作骨架。

## 1. 共享与隔离

一个项目可拥有多段会话。会话共享项目的 brief、正式研究记录、当前问题与方案、文献、来源和版本；仅使用本会话自己的近期聊天历史。未采纳的对话建议只属于其来源会话，不自动成为其他会话的研究依据。

`guidance_mode` 与 `entry_stage` 独立保存：

- 指导方式：`beginner` / `direct` / `case`。
- 进入阶段：`orientation` / `literature` / `question` / `design`。
- `discussion_goal` 是本会话讨论目标；`case_id` 是可空的预留字段。

改变这些设置不改变项目真实阶段、正式问题、方案或 focus。`case` 当前仅有数据契约，没有教学案例内容；指导上下文明确标记 `case_available=false`，前端不能据此宣传已有教学案例。

指导仍复用现有规则／模型生成能力，仅把上下文历史替换为本会话最近 8 条消息，并附加会话设置，不新增检索、任务队列或执行流水线。服务端核对会话、论文、正式记录、建议的项目和会话归属。

## 2. SQLite 数据模型

数据保存在项目已有 `knowledge.db` 中；不依赖浏览器存储。只在经过校验的明确写入操作中事务化创建以下表和索引，GET 不负责迁移。

会话和工作台读取使用不初始化表结构的只读知识库连接（SQLite `mode=ro`）。即使旧库缺少与当前读取无关的知识库表，浏览也不会偷偷补表或补列；不存在的数据库不会因读取而被创建。正式研究记录写入仍通过独立的版本核对事务完成，不借用只读连接。

| 表 | 用途 |
| --- | --- |
| `sra_conversations` | 会话 ID、project_id、标题、目标、指导方式、进入阶段、case_id、创建／更新时间、chat_revision，以及创建请求的幂等信息。 |
| `sra_chat_messages` | 自增 `seq`、稳定 message_id、project_id、conversation_id、request_id 和完整消息 JSON。保留 role/content/area/mode、引用、问题与建议关联、原创建时间。 |
| `sra_chat_requests` | 会话内唯一 request_id、输入摘要和内容、处理状态、执行尝试次数、错误、执行权 token、材料版本与指纹。 |
| `sra_chat_suggestions` | 会话内建议及原 suggestion_id、生成依据、采纳后的 item_id。 |
| `sra_chat_meta` | 历史转换标记 `migration_version=1`。 |

会话列表按 `updated_at DESC, conversation_id DESC` 排序；消息按不可变 `seq` 升序，使用索引和 keyset 游标。每页默认 30 条，上限 100 条。请求读取只返回该请求的消息和建议，不返回整段聊天。

本地同步锁是项目目录中的 `.chat-*.lock` 小文件。它们不是研究产物，不应在请求处理期间手动删除。运行中不清理锁文件，避免文件删除导致进程间锁失效。

## 3. HTTP 接口

以下路径均以 `/api/projects/{project_id}` 开头，只支持已存在的项目。会话 ID 应视为不透明 ID，不能靠前端拼接或假定它属于当前项目。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /conversations?limit=30&offset=0` | 列出会话，返回 `conversations`、`next_offset`。 |
| `POST /conversations` | 创建会话，不调用模型、不启动研究流程。 |
| `GET /conversations/{cid}` | 读取会话设置，不携带全部消息。 |
| `PATCH /conversations/{cid}` | 修改标题、目标、指导方式、进入阶段及 case_id。 |
| `GET /conversations/{cid}/messages?limit=30&after=0` | 分页消息，返回 `messages`、`next_after`。 |
| `GET /conversations/{cid}/messages?limit=30&tail=true` | 第三步新增：读取最近一页，仍按 seq 升序返回；`next_before` 用于向前加载。 |
| `GET /conversations/{cid}/messages?limit=30&before={seq}` | 第三步新增：读取该 seq 之前的一页，返回更早的 `next_before`。 |
| `POST /conversations/{cid}/messages` | 先持久化用户消息和请求，再同步生成指导。 |
| `GET /conversations/{cid}/requests/{rid}` | 查询请求状态，不自动生成、重试或修改状态。 |
| `POST /conversations/{cid}/requests/{rid}/retry` | 明确重试失败／中断请求，沿用原用户消息和 request_id。 |
| `GET /conversations/{cid}/suggestions?limit=30&offset=0` | 分页读取当前会话建议及依据变化标记。 |
| `POST /conversations/{cid}/suggestions/{sid}/apply` | 明确采纳有效建议，原子写入正式记录并标记已采纳。 |

新前端可通过 `GET /workspace?include_chat=false` 获取共享研究材料和 workflow，避免夹带默认／历史会话的全部聊天。原 `/workspace` 默认行为继续兼容旧客户端。

### 创建会话

```http
POST /api/projects/proj_example/conversations
Content-Type: application/json

{
  "request_id": "create-001",
  "title": "梳理联邦学习的研究问题",
  "discussion_goal": "比较已有工作，确认适合当前条件的问题",
  "guidance_mode": "beginner",
  "entry_stage": "question",
  "case_id": null
}
```

最小响应（实际还包含创建、更新时间等字段）：

```json
{
  "request_id": "create-001",
  "conversation": {
    "conversation_id": "conversation-example",
    "project_id": "proj_example",
    "title": "梳理联邦学习的研究问题",
    "discussion_goal": "比较已有工作，确认适合当前条件的问题",
    "guidance_mode": "beginner",
    "entry_stage": "question",
    "case_id": null,
    "chat_revision": 0
  }
}
```

同项目同一创建 request_id、相同字段返回原会话；同一 ID 不同字段返回 409。`request_id` 可省略，由服务端生成，但前端应自行提供稳定 ID 才能在网络超时后安全重发。

### 发送消息

```http
POST /api/projects/proj_example/conversations/conversation-example/messages
Content-Type: application/json

{
  "request_id": "message-001",
  "content": "请结合当前问题和文献，帮我核对方案的对照条件。",
  "expected_revision": 2,
  "selected_item_id": null,
  "selected_paper_ids": [],
  "reading_purpose": "comparison"
}
```

`area` 可不传，由进入阶段映射到现有指导区域：orientation→overview、literature→literature、question→question、design→study。它只控制指导上下文，不推进产品阶段；传入 writing 会被 422 拒绝。

最小成功响应：

```json
{
  "project_id": "proj_example",
  "conversation_id": "conversation-example",
  "request_id": "message-001",
  "status": "completed",
  "attempt": 1,
  "based_on_revision": 2,
  "basis_changed": false,
  "messages": [
    {"message_id": "user-example", "role": "user", "content": "…", "mode": "user"},
    {"message_id": "reply-example", "role": "assistant", "content": "…", "mode": "model", "cited_paper_ids": [], "suggestion_ids": []}
  ],
  "suggestions": []
}
```

消息实际包含 conversation_id、request_id、area、创建时间、引用与建议关联、request_status 和 error 等兼容字段。分页消息带 `seq`，第三步也为请求状态／发送响应中的消息补充同一 seq。`next_after` 为 null 表示暂时没有下一页；新增消息后可从已收到的最后一个 seq 继续读取。新增的 tail / before 查询与非零 after 不能混用，旧默认升序分页不变。会话列表使用 offset，更新会话可能改变最近排序，前端切换或重命名后应刷新第一页。

第三步的首页、能力检测、幂等首次创建与前端接入流程见 [科研对话主页](conversation-home.md)。这些新增能力不改变本页的材料版本、采纳和产品边界约定。

### 重试与采纳

```http
POST /api/projects/proj_example/conversations/conversation-example/requests/message-001/retry
Content-Type: application/json

{"expected_revision": 2}
```

重试不接受新消息内容；改内容应使用新 request_id 发送新消息。可以省略 expected_revision，服务端会按当前材料重新构建上下文并保存新的生成依据。

如果原选择的记录或论文已经失效，重试会先返回 422 而不调用模型；前端应让用户重新核对引用，用新 request_id 提交修正后的消息，不能静默替换原用户消息。

```http
POST /api/projects/proj_example/conversations/conversation-example/suggestions/suggestion-example/apply
Content-Type: application/json

{"expected_revision": 2}
```

```json
{"item": {"item_id": "record-example", "kind": "note", "origin": "accepted_suggestion"}, "already_applied": false, "revision": 3}
```

重复采纳返回同一 item，`already_applied=true`，不再增加材料版本。过期建议返回 409，草案保留；越界建议返回 422，不能通过历史会话或旧接口绕过。

## 4. 请求可靠性与错误

状态：`processing` / `completed` / `failed` / `interrupted`。

1. 请求在短 SQLite 事务内登记用户消息、材料依据与请求状态。短事务内获取会话的非阻塞 OS 文件锁，并在提交后持续持锁至本次生成与结果保存结束；不会持有 SQLite 写事务等待模型。
2. 相同会话、相同 request_id、相同内容与引用重复提交，返回原请求。不重复插入用户消息或回复。同一 ID 被用于不同内容／引用返回 409；expected_revision 不纳入内容幂等摘要，避免网络重发仅因刷新版本而被误判为另一条消息。
3. 同会话的新生成请求互斥，不同会话可并行。元数据／聊天在上下文构建期间变化时，请求可返回 409，要求读取最新会话后重发；不会静默使用旧的会话上下文。
4. 模型失败时，原用户消息保留，请求记录失败及错误类型，不补造助手回复。明确重试只增加 attempt，不再插入用户消息。
5. 回复、建议与 completed 状态在同一事务提交。采纳正式记录与标记已采纳也在同一事务提交；失败不留下半条记录。执行 token 防止失去执行权的旧尝试写入回复。
6. 服务重启／进程退出后，内核会释放文件锁。GET 只读探测锁，将没有工作进程持锁的 processing 请求呈现为 interrupted，不写入、不自动调用模型。明确重试时才持锁更新请求。仍被另一个进程持锁的请求保持 processing，**不按超时或进程标识猜测其已中断**。

发送／重试：completed 返回 200；重复且仍 processing 返回 202；模型失败返回 502；材料过期、内容冲突、同会话忙或 interrupted 返回 409；无效输入或产品越界返回 422；归属错误返回 404；会话 SQLite 存储事务错误返回 503。错误使用兼容的 `detail`；已持久化的失败请求同时返回 request_id、status、messages 等定位信息。状态 GET 本身成功时返回 200，状态值仍可能为 failed／interrupted。

客户端网络超时不等于服务端调用停止。先查询原 request_id 状态，避免自动改 ID 再发送或立即重试。

**本地幂等不代表外部模型调用 exactly-once。** 如果进程在提供方已经计费后、回复提交前退出，明确重试可能再次调用和计费。本步未实现提供方幂等键、跨机器分布式锁或通用任务队列。

## 5. 聊天版本与材料版本

- `Workspace.revision` 与旧字段 `expected_revision` 保留，作为研究材料的乐观版本；普通聊天不再增加它。
- `conversation.chat_revision` 独立记录该会话消息、请求状态或设置的变化；旧 Workspace 返回默认会话的 chat_revision。
- brief、正式研究记录、主线选择等写入仍更新材料版本。论文、原始来源、方法版本、研究报告等外部变化用项目材料指纹识别，不强行改写旧阶段或补造版本。
- 建议保存 `based_on_revision`、材料指纹、来源会话及原消息关联。采纳时重新读取材料、核验引用、依赖状态、来源和产品边界。
- 当前采用保守的**项目共享材料快照**：包括正式记录、brief、focus、论文元信息与已存正文片段（含内容、顺序和页码）、来源、原系统材料等。共享材料任一部分变化可能要求建议复核，不是只比较回复明确列出的几篇引用。未采纳聊天建议不进入材料指纹。片段更新无需伪造一次 Workspace.revision 变化，也能使旧建议失效。
- 生成期间材料变化，仍保留回复与建议，并返回 `basis_changed=true`，不能直接采纳。普通聊天、改标题、指导方式或讨论目标不会因此使其他草案失效。

第一步的 `product_stage`、`product_boundary`、历史 `current_stage` / `stage_statuses` 和 `workflow.flow.planReady` 约定未改变；进入阶段不能替代这些判断。

## 6. 历史与旧接口兼容

旧 Workspace.messages 通过稳定的项目内历史会话 ID 只读呈现。反复访问不会复制数据；没有聊天的项目不会凭空出现历史会话。

首次明确写入（创建会话、修改历史会话、发送或采纳等）才在一个事务内建立新表，按原顺序导入历史消息及建议，并登记 migration_version。原消息／建议 ID、内容、引用、关联、时间和次序保留，原始 KV 历史不被批量清空。转换失败整体回滚，之后仍能读取原历史。

历史建议若已有材料指纹，按原值保留并与当前材料核对；若没有生成时的材料快照，则返回 `basis_changed=true`，直接采纳返回 409，提示在原会话重新核对。不能仅凭版本号相同，把导入当天的材料指纹补成旧建议的生成依据。原草案仍可查看，新生成的有效建议可正常采纳，历史已采纳记录的重复请求仍返回原记录。

尚未转换的旧 JSON 历史在 GET 中按页返回，但底层仍需解析原 JSON；转换后按 SQLite 消息索引分页。原历史没有 request_id 的消息不补造请求记录。

旧 `/workspace/guide` 可额外传 request_id，服务默认／历史会话；旧 `/workspace/suggestions/{sid}/apply` 复用相同采纳逻辑。旧 `/workspace` 和导出保留兼容字段，仅投影默认／历史会话，不把所有新会话拼成一条旧历史。旧聊天也不会让新版会话草案仅因聊天而失效。

旧 expected_revision 值不删除、不自动重置。第一步测试中“模型输出越界时完全不留聊天”的预期调整为：保留已登记的用户消息与失败请求，但不保存越界建议、不增加正式材料、不改变研究阶段。

## 7. 第三步前端接入顺序

1. 先选已有项目，分页列出会话；只读浏览不自动新建。用户输入首条消息后，再用稳定的创建 request_id 创建会话。
2. 拿到 cid 后，用独立的消息 request_id 发送首条消息。后续切换会话读取该 cid 的设置和分页消息，不能沿用上一会话的消息数组。
3. 共享材料用 `/workspace?include_chat=false`；项目进度仍依据服务端 workflow，不根据 entry_stage 或聊天内容自行推进。
4. 保存返回的 message_id、seq、request_id。202 显示处理中并查询原请求；HTTP／网络超时先查状态，不自动重复生成。
5. failed／interrupted 显示保留的用户消息、错误和明确的“重试”操作；只有用户确认后调用 retry。409 保留输入并刷新材料／会话；404 刷新项目和会话归属；503 先查询原请求，避免盲目重复。
6. 只展示当前会话建议；basis_changed 或 409 提示先复核。采纳以服务端返回的 item_id 去重，成功后刷新共享材料及 workflow。不能通过写作区域或旧建议进入实验、结果新增或写作。

本步未新增动态推荐按钮、案例页面、账号、权限、收费或公网部署；也不通过首条消息自动创建项目。

## 8. 离线验证与限制

专项测试为 `tests/test_project_conversations.py`，覆盖会话隔离与共享材料、项目／会话归属、创建／发送幂等及并发、失败保留与并发重试、真实子进程文件锁、中断只读识别、材料及正文片段变更、并发和失败采纳、提交失败、历史只读映射与事务转换、旧建议依据缺失保护、缺失数据库保护、旧接口兼容、有界分页和产品边界。存储层另验证只读连接不能写入、不能新建缺失目录或数据库；真实 workflow provider 的工作台读取也验证不迁移旧库。

2026-10-20 最终结果：

- 43 项多会话专项用例通过（包括同一创建／发送请求的多轮并发竞态、并发重试和本轮补强）。
- 合并工作台、版本／事务、项目继续、历史材料导出、知识库存储和第一步边界等回归，309 项 Python 测试通过。随后补充缺失库的 404 保护，并复测完整 43 项会话专项通过；两轮有重叠，共覆盖 310 个不同用例，无失败或跳过。运行仍有弃用警告，未以删除断言或跳过用例掩盖失败。
- 44 项已有 JavaScript 状态检查通过；新增文件格式检查与相关 Python 编译检查通过。
- 本次未修改前端页面，也未将这些结果描述为真实模型、公网或新版界面验收通过。

复测（PowerShell，项目根目录）：

```powershell
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
$env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest tests/test_project_conversations.py tests/test_research_workspace.py tests/test_product_plan_boundary.py tests/test_unified_workflow.py -q
```

测试仅用临时目录、离线模型夹具和本机子进程，不调用真实／付费模型，不写入用户项目，不运行实验。主服务仍在 8001 监听，之后未重启；新接口由隔离测试实例验证，当前主进程未做新接口生效验收。未提交或推送代码，保留原有未提交改动。

未验证真实提供方的超时／计费语义、公网访问、跨主机共享磁盘、多机部署、大规模压力或新前端交互。这些不因离线测试通过而自动成立。
