# 科研开场与每轮引导（第四步 B）

本步延续[方案阶段上限](product-plan-boundary.md)、[多会话契约](project-conversations.md)及[中央对话主页](conversation-home.md)。只完善开始讨论、指导方式及每轮选择；没有自动检索、教学案例、实验执行、结果生成或论文写作。8001 本轮不启动。

## 1. 用户看到什么

新研究使用开场白：

> 你好，我会和你一起把研究兴趣整理成清晰的问题，再形成有文献依据的研究方案。你可以从零开始，也可以带着已有方向、论文或想法继续。

三个入口仅设置 beginner/direct、进入阶段及可编辑草稿，不发送消息、不建项目、不调用模型。首次发送仍走原幂等 start → 会话消息接口。

已有项目的新对话显示正式问题、服务端方案状态及可讨论事项，不要求重新填写背景，不读其他会话的未采纳想法。返回会话恢复历史，另显示只读的继续提示，不插入开场消息。历史受限、问题／来源待复核和无论文时分别说明；所有阶段判断仍用 product_boundary、workflow、planReady，不用聊天内容认定完成。方案可审阅不表示结论已验证。

beginner 用简明中文，必要术语短解释，说明当前步骤的意义，一次优先解决一个主要问题，允许“不确定”；direct 优先直接处理任务，减少科普与重复背景，必要时才追问。两者通常至多两项追问，先答当前问题，尊重简短／暂停／结束要求；证据标准和 DESIGN 上限不因模式改变。历史 case 设置继续可读，但新入口不可用，不虚构案例。

模式、进入阶段、目标仍使用原 PATCH 会话接口，不增加材料 revision，不替换正式主线。未采纳草案只属于来源会话。

## 2. 输出与持久化契约

模型输出使用 `GuideDraft` 严格结构（额外字段拒绝）。原 reply、cited_paper_ids、questions、suggestions 均保留，新增：

```json
{
  "reply": "先核对拟用方法是否能回答当前问题。",
  "questions": ["尚未确定的数据条件是什么？"],
  "suggested_replies": [
    {"label":"比较替代方法","content":"有没有更简单的方法可以回答同一个研究问题？"},
    {"label":"核对局限","content":"先区分已有依据和仍需核对的条件。"}
  ],
  "actions": [{"type":"open_settings"}]
}
```

- `questions` 是助手向用户的追问，页面独立显示“需要你确认”，不会变成用户消息。
- `suggested_replies` 是可发送的用户表达。显示短标签及实际内容；点击发送原文，用户可忽略并自由输入。
- `actions` 是操作提议，不是聊天内容，页面明确显示“不发送聊天、不启动研究”。每条正常回复通常合计 2—4 项，不按追问凑按钮；结束、明确不要选项或无合适选择时可更少。
- 两个字段随助手 Message 在现有 `sra_chat_messages.data` JSON 内原子保存，无新数据库或表迁移。讨论回复保存原内容；服务端生成的 action_id、真实目标与标签也一起保存。
- 旧消息字段缺失时按空列表展示，GET 不补写历史、不调用模型。刷新读取保存的推荐，不重新生成。
- 请求、seq、message_id、request_id、mode、引用和 suggestion_ids 沿用第二步；旧客户端字段不删除，普通聊天仍只改变 chat_revision。

### 允许的操作

| type | 模型可提供的选择 | 服务端绑定与点击结果 |
| --- | --- | --- |
| view_references | 仅 type；本回复必须有真实 cited_paper_ids | 绑定校验过的论文 ID，打开文献页面，不检索／生成 |
| open_plan | 仅 type；必须存在当前正式方案 | 绑定 workflow 中的 plan.item_id，打开方案页面；方案变化后旧卡片失效 |
| open_settings | 仅 type | 打开当前会话设置并聚焦指导方式 |
| adopt_suggestion | 本回复 suggestion_index（0 起），或 allowed_suggestions 的 suggestion_id；不得同时提供 | 绑定当前会话具体有效 question/plan 草案，复用原子 apply |

模型不能提供任意 URL、接口、代码、操作 ID、正式记录 ID 或任意操作名称。资源标签由服务端依据真实记录生成，不采纳模型所写“已保存”等文字作为事实。问题或方案草案仍须通过原有引用、关联、来源及产品边界校验。

模型上下文含自身最近 8 条消息及其中的推荐、当前会话设置、正式共享材料和真实能力声明。有效待采纳问题／方案最多提供近期 8 条摘要（正文至多 1500 字）；唯一有效方案即使较早也保留在该窗口。有效方案总数单独提供，避免把截断窗口误当作“唯一方案”。不存在把项目其他会话草案自动加入依据的逻辑。

“就采用这个方案”不会自动采纳。唯一、有效、对应明确时可以提供具体采纳卡片；多个候选先澄清，过期先复核。缺少依据快照的历史建议不能借新卡片绕过保护。

## 3. 新增的最小接口

原消息发送、查询、重试和采纳接口不变。能力接口仍 version=1，新增 `research_guidance=true`、`automatic_search=false`、`case_content=false`。不把普通资源 404 当作“需重启”。

### 刷新可见卡片的有效性（只读）

```http
GET /api/projects/{pid}/conversations/{cid}/guidance-actions?message_ids={mid1},{mid2}
```

一次 1—100 个消息 ID；全部校验项目和会话归属。返回：

```json
{"actions":{"mid1":[{
  "action_id":"server-generated-id",
  "type":"adopt_suggestion",
  "label":"采纳草案：方案 A",
  "suggestion_id":"real-suggestion-id",
  "item_id":null,
  "paper_ids":[],
  "enabled":false,
  "disabled_reason":"草案依据已变化或缺少快照，请重新讨论或复核"
}]}}
```

普通消息分页和请求状态响应中的 actions 也投影同一实时 enabled / disabled_reason；两者是读取时状态，不回写消息历史。页面读取材料、恢复焦点或手动刷新时重新核对已加载卡片；未加载历史按原分页读取。不是新的后台任务队列或自动生成轮询。

### 用户明确点击操作

```http
POST /api/projects/{pid}/conversations/{cid}/messages/{mid}/actions/{aid}
Content-Type: application/json

{"expected_revision":2}
```

服务端按已保存消息和卡片解析，不接受调用方另传 type、URL 或目标。导航操作返回固定 type、page、item_id、paper_ids；设置的 page 为 null。采纳返回 `type` 加原 apply 的 item、already_applied、revision，复用原事务，不重复写入，也不附带运行研究。

错误仍用 detail：归属／卡片缺失 404；失效或版本冲突 409；无效关联／越界 422；存储失败 503。重复采纳仍返回原 item_id（already_applied=true）；前端通常已经禁用已采纳卡片。即便绕过前端点击失效卡片，apply 仍核验材料指纹、引用、正文片段和依赖。

## 4. 生成、校验、降级与失效

`core/workspace/guide.py` 保留产品角色、阅读支持和问题分析；`guidance_policy.py` 集中管理指导方式、场景、证据范围、推荐规则及结构约束。取消聊天指导中的论文起草提示词叠加和“最小试跑”安排；仍可讨论拟采用的验证方法及方案局限。讨论目标和材料作为上下文数据传入，不拼成新的系统指令。

推荐先由模型产生，服务端再验证类型、数量、目标和引用；去掉重复、刚发送过的表达及同标签选项。缺少可用推荐时，仅在整体指导已成功后，按当前需求、进入阶段和最近已选内容补充保守讨论表达。不复制 questions，不声称做了额外检索。

服务端还保守过滤没有在当前输入、历史或 brief 中出现的明显第一人称资源断言，例如“我有4张GPU和20万元预算”。这是启发式防误填，不是对所有自然语言事实的完整语义验证；真实模型内容质量仍需单独验收。

畸形结构、非法动作或伪造引用走现有受控失败路径，保留用户消息与错误请求，不写可执行卡片。模型整体失败不能被规则成功回复替代。真正的 dry_run 规则模式有明确 mode=rules 标记，不假装模型或检索结果。规则讨论不再每轮自动制造笔记；用户明确要求保存笔记时才生成待采纳草案，仍需主动采纳。

生成期间依据变化：保留助手回复和原推荐，但草案标为 basis_changed，采纳禁用。刷新时重新计算卡片状态；原消息 JSON 不被重写。过期点击返回 409，保留草案并刷新材料，不偷偷改 expected_revision。论文正文片段变化同样受保护，不是只比较 revision。

## 5. 前端交互与可靠性

实际改造仍在 index.html 加载的 conversation-home.js / css，不增加新框架或独立页面，不重新设计第四步 A 的导航、排序或下载。

- 讨论选项调用原 firstSend / postMessage，继续使用独立的创建与消息 request_id、并发控制、状态核对和明确重试。
- 点击后同步加锁并禁用讨论按钮，双击不重复发送。同会话处理中不能再点推荐生成；切换页面不取消服务端调用，迟到回复不能写入另一会话。
- 有未发送草稿时弹出明确选择：“保留草稿，暂不发送”或“保留草稿并发送推荐”；不静默覆盖。确认发送推荐后原草稿仍保留。
- 操作点击另走绑定卡片接口；查看资料不产生模型调用。采纳后按 item_id 去重，刷新共享材料和 workflow。
- 历史分页、超时查询原 request_id、失败／中断的显式重试、刷新不自动调用模型等契约不变。本地幂等不承诺外部模型计费 exactly-once。
- 原生按钮支持键盘和焦点；窄屏讨论选项换行，消息在独立区域滚动，底部输入区不覆盖最后的操作。暂停或明确不要选项时不继续强推按钮。

## 6. 离线验收与截图

所有测试使用临时目录、真实 FastAPI 和 SQLite。没有调用真实／付费模型，没有写入真实用户项目，没有运行实验，没有提交或推送。

专项 `tests/test_research_guidance.py` 覆盖以下场景及实际操作：

| 场景 | 检查的行为 |
| --- | --- |
| AI 新手无科研经历 | 一项易理解追问、允许不确定、没有整套方案或长问卷 |
| 已定方向想了解现状 | 明确材料范围和没有自动检索，提供讨论关键词而非假执行 |
| 已有问题核对可行性 | 直接讨论方法与条件，不退回零起点；已有资源不重复询问 |
| 只有两周、方向太难 | 讨论缩小范围，不覆盖正式 brief |
| 采用方案 | 唯一有效草案绑定具体卡片；多个候选澄清，文本不自动采纳 |

另覆盖结构错误、无效动作、伪造资源与跨项目／会话 ID、保存与刷新稳定性、缺少快照、片段和 brief 变化、生成期间依据变化、重复原子采纳、资源断言过滤、case 预留、暂停及拒绝选项。模式测试检查实际进入生成的上下文、结构响应和操作结果；规则模式从真实接口进入并断言 mode=rules 与零模型调用，不只检查提示词字符串。

浏览器 `tests/research_guidance_browser.cjs` 连接 `tests/research_guidance_test_server.py`：实测三种开场、打开页面不生成、推荐持久化、草稿取消／保留发送、双击去重、有效方案采纳、正文片段变化后的实时禁用、查看／设置不生成、键盘焦点、推荐发送中切换会话及旧消息兼容。未把主要会话接口 mock 掉。原第三步浏览器和异步草稿回归也复测；故障注入的中止／404 是预期网络日志，pageerror 为空。

原浏览器夹具曾在乐观“处理中”出现后、生成器进入前收到释放信号，随后 Event.clear 丢失信号导致一次 40 秒等待。现用计数信号量保留早到释放，再完整复测，没有放宽超时、删除断言或掩盖服务失败。

截图均为软件验收夹具，不是教学案例或真实科研成果；已人工查看 1440×900 和 390×844，无明显横向溢出或输入遮挡。窄屏截图滚至消息末尾，确认操作卡片在输入区上方可达。

| 画面 | 截图 |
| --- | --- |
| 新研究开场（桌面） | [opening-desktop.png](research-guidance/opening-desktop.png) |
| 已有项目新对话 | [project-opening-desktop.png](research-guidance/project-opening-desktop.png) |
| 推荐回复与真实操作卡片 | [reply-actions-desktop.png](research-guidance/reply-actions-desktop.png) |
| 窄屏推荐及失效草案说明 | [reply-actions-mobile.png](research-guidance/reply-actions-mobile.png) |
| 新研究开场（窄屏） | [opening-mobile.png](research-guidance/opening-mobile.png) |

### 复测入口

```powershell
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
$env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest tests/test_research_guidance.py tests/test_project_conversations.py tests/test_research_workspace.py tests/test_product_plan_boundary.py tests/test_conversation_home.py -q
# 浏览器服务仅用 TemporaryDirectory；另一终端运行 cjs（需已有 Playwright / Edge）。
venv/Scripts/python.exe tests/research_guidance_test_server.py --port 8015
node tests/research_guidance_browser.cjs
```

测试统计及服务关闭状态见本页末尾交付记录；不要把测试标记用于用户正式服务。

## 7. 第五步接入位置与尚未验证范围

第五步可复用 guidance_policy.CAPABILITIES、模型 allowed actions 的严格结构、真实 ID 绑定、消息持久化及卡片实时校验接口。当前 `automatic_search=false`，没有自动检索 type，不显示立即检索按钮；只能讨论关键词或打开原文献页面。

未来确实接入检索时，再增加有实现、有权限与幂等保护的服务端能力，声明相应 capability，保存来源和实际检索结果后才提供对应动作。不能仅改提示词或按钮文字来宣称支持检索。本步没有开始第五步或第六步。

离线测试不能证明真实模型能稳定理解所有隐含约束、避免所有近义推荐、判断创新性或识别所有虚假资源表述；也未验收真实提供方 JSON 兼容、费用、延迟、公网、多机或真实手机软键盘。长会话目前仍是自身最近 8 条上下文，不新增自动记忆压缩。模型质量需后续在明确授权下单独抽样评价。

## 8. 本轮交付记录（2026-10-20）

- 第四步 B 专项最终 **33 项通过**；工作台回归 **17 项通过**。另有会话、产品边界、首次创建、项目导航／归档、版本、继续、历史下载及页面等组合回归 **178 项通过**。这些不同文件合计 **228 个不同 Python 用例通过**，无最终失败或跳过。追加的会话／主页／工作台组合复测 104 项有重叠，不重复计数；随后补测长草案窗口、规则模式差异及自动绑定卡片后仍至多四项的保护。
- **44 项 JavaScript 状态检查通过**；相关 Python 编译、Black 格式及 JavaScript 语法检查通过。仍有现有 asyncio_mode 配置和 datetime.utcnow 弃用警告，没有隐藏。
- 第四步 B 浏览器、原中央对话完整浏览器流程及异步草稿浏览器流程全部通过，pageerror 均为空；截图如上。
- 一条旧工作台测试原本要求每次规则聊天自动产生笔记。本轮改为先断言普通讨论不产生草案，再明确请求保存笔记，保留“采纳前无正式记录、采纳后实际生成记录”的断言，没有删除原子采纳验证。
- 只启动临时目录下 8015／8016 验收实例，完成后均已关闭；最后检查 8001、8015、8016 无监听。**8001 未自行启动，新后端尚未在用户主服务加载。**未重启用户服务、触碰真实项目写入、调用真实模型、提交或推送。

下一步可依赖：保存于消息的 suggested_replies / actions、服务端真实目标绑定、只读卡片状态核验、原子采纳及明确的未接入能力声明。本步骤到此停止。
