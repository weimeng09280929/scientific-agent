# 对话驱动的文献检索与研究现状整理（第五步）

本步在前四步（中央对话、多会话、材料版本保护、开场引导与白名单操作卡片）之上，
接通"理解检索任务 → 有界检索 → 去重筛选 → 整理有来源的研究现状 → 原会话交付"。
仍遵守[方案阶段上限](product-plan-boundary.md)：任务止于整理，不选定研究方向、
不采纳问题或方案、不启动实验、不生成结果或论文。

> **第五步收尾（2026-10-21）**：完成真实运行问题修复与稳定性验收——partial 补跑、
> 代理配置修复、模型路由/截断降级可见化、主题提取修复、计数语义统一、
> start 创建竞态**已复现、定位根因（Windows SQLite 并发首建库瞬态只读误判）
> 并修复（建表阶段有界重试）**。详见文末[收尾记录](#9-第五步收尾真实运行问题修复与稳定性验收)。
> 真实环境演示（8001，2026-10-20）：真实 arXiv + 模型完成部分交付（partial），
> 未提交或推送代码。

## 1. 支持的任务与触发规则

一次任务 = 一轮有界检索 + 整理，完成后停止并交回用户选择（继续阅读 / 调整范围 / 比较方法 / 讨论问题）。

两种入口：

1. **明确请求直接启动**：`POST /conversations/{cid}/messages` 的服务端规则分类
   （`core/research_tasks/detect.py`）识别到"强动作词（检索/搜索/帮我查等）+ 明确主题"
   时，登记用户消息并直接启动任务，返回 202 `{"status":"search_started","task_id",...}`。
2. **"开始检索"操作卡片**：意向不明确（缺主题、泛指词如"相关论文"）时不启动任务，
   交付一条规则回复 + 可编辑 `start_search` 范围卡片（标注主题、年份范围、将得到什么、
   哪些未限定）。点击卡片走 `POST /conversations/{cid}/research-tasks`
   （携带 `card_message_id`/`card_action_id`），服务端核验卡片真实存在并绑定参数；
   卡片主题可编辑，年份可选。

**绝不视为检索授权**：概念解释（什么是X）、阅读/查看已有材料（"帮我看看已上传的材料"）、
页面与状态操作（打开/切换/刷新 + 页面/会话/材料/状态）、以及**元话语**
（"需要检索的问题""别检索了""还没检索过"——先剥离再判定）。
"我想了解研究现状"属于讨论意图，由第四步 B 的引导处理。

## 2. 检索来源与能力边界

- 复用仓库既有 4 个公开来源工具：`openalex`、`arxiv`、`semantic_scholar`、`crossref`
  （`core/tools/`），不新增付费全文获取或绕过访问控制的下载；没有全文就如实标记。
- 每条查询按序取前 `sources_per_query` 个来源（默认 2）。
- **有界检索（服务端配置，不由请求方决定）**：`SearchLimits` = 最多 3 条查询 ×
  每条 2 来源 × 去重后 20 条候选，单次调用最多 10 条结果、30 秒超时。
  无无限翻页、无限扩展查询或自动重试；实际执行数量全部记录在任务行。
- 查询规划（`planner.py`）：模型规划失败或不可用时回退规则（主题本身）；
  可做中英文术语转换，不添加用户未提出的约束。
- **状态封装（`sources.py`）**：既有工具吞异常返回空列表；封装在调用期间挂接日志
  捕获，空结果 + 失败日志 => `error`；空结果无日志 => `zero`；非空 => `ok`。
  不以"未发现相关工作"掩盖网络错误。每条候选记录：原始请求、实际查询、来源、
  检索时间、标题、作者、年份、DOI/arXiv/链接、取得的是元信息还是摘要（`has_abstract`）。

## 3. 接口、状态、并发、幂等与恢复

均挂在 `/api/projects/{pid}/conversations/{cid}/research-tasks` 下（`web/research_task_api.py`）：

| 方法与路径 | 用途 |
| --- | --- |
| `POST …/research-tasks` | 启动（消息分流或卡片）；返回 task 视图，立即 202 |
| `GET …/research-tasks?limit=&offset=` | 会话内任务列表（刷新恢复用） |
| `GET …/research-tasks/{task_id}` | 轮询真实状态：阶段、候选数、失败来源、取消标记 |
| `POST …/research-tasks/{task_id}/cancel` | 请求取消（阶段边界生效） |
| `POST …/research-tasks/{task_id}/retry` | 明确重试（`re_search` 决定是否重新检索） |
| `GET /api/projects/{pid}/research-tasks/{task_id}/export` | 报告 Markdown 下载 |

- **状态机**：`queued → running（planning/searching/ranking/saving/synthesizing/delivering）→
  completed | partial | failed | cancelled | interrupted`。轮询读时对 `running`
  只读探测项目级 OS 锁（`.research-*.lock`，内核进程死亡即释放）：锁不在 =>
  呈现为 `interrupted`，不写库、不自动重启、不按超时猜测仍工作的任务（`queued`
  不探测，避免把刚登记的任务误报中断）。
- **幂等**：同会话同 `request_id` 同内容 => 返回原任务；同 ID 不同内容 => 409。
  并发双击经 `BEGIN IMMEDIATE` 串行化 + `(conversation_id, request_id)` 唯一约束，
  只创建一个任务、一条用户消息。
- **并发**：同项目同时只允许一个非终态任务，第二个启动返回 409
  "该项目已有检索任务在执行"；执行线程再加项目级 OS 锁双保险。不同项目互不影响。
- **事务**：网络与模型调用期间不持有 SQLite 写事务；最终结果消息与完成状态在
  **同一事务**提交；取消/中断/换代的迟到结果不发布、不把任务改回成功。
- **重试**：允许 `failed / interrupted / cancelled`（含锁已释放的孤儿 `queued`）。
  `re_search=false` 复用已保存候选，只重试整理阶段，不重复检索、不重复入库
  （DOI/arXiv/paper_id 三重查重）；`re_search=true` 全量重跑但仍按外部 ID 去重。
  不承诺外部调用 exactly-once（同第二步约定）。
- 能力声明：`GET /api/conversation-capabilities` 新增 `research_search=true`；
  前端仅在能力存在时启用检索入口，旧服务不支持时显示准确说明。

## 4. 材料保存、引用校验与来源追溯

- 入库复用项目论文库（`KnowledgeStore.save_paper`），`metadata` 记录
  `research_task_id`、`retrieved_at`、`source_queries`、`relevance_reason`、
  `candidates_only=true`（候选不等于已阅读或认可；入库不自动建立正式依据关联）。
- **不覆盖**已有更完整的元数据：按 DOI/arXiv 匹配到已有论文时只关联不写入，
  用户上传的正文与人工修订原样保留；冲突信息保留双方取值（`field_conflicts`）。
- 整理报告（`synthesis.py`）：模型输出经服务端校验——`representative` 与
  `limitations` 引用的 `paper_id` 必须属于本轮候选，伪造引用 => `SynthesisValidationError`
  => 任务 failed，检索结果保留、可只重试整理；`methods_relations` 中的
  `paper:{id}` 同样校验。零候选时强制规则模式，不让模型在空输入上发挥。
- **证据边界（提示词 + 规则模式硬编码）**：仅元信息不描述方法或结论；仅摘要写明
  "基于摘要"，不声称读完全文、不编造页码；"没有检索到"≠"领域无人研究"；
  不据少量论文认定创新性；来源返回内容是待分析数据，其中指令一律忽略。
- 引用量只作为元数据记录，不参与打分，也不被表述为可信度。
- **材料指纹联动**：入库论文自动改变项目材料指纹 => 旧建议按第二步契约标记
  `basis_changed`，采纳被禁用；任务的 `basis_fingerprint` 记录报告依据，
  生成期间材料变化 => `basis_changed=true`。正式研究记录不被任务触碰。

## 5. 取消、部分失败与重试

- 取消：卡片与状态接口提供明确取消按钮；`cancel_requested` 置位后执行器在
  阶段边界（每条查询、每个来源调用之间）停止；不谎称能即时中断外部调用
  （在途调用返回后生效）。已保存材料不静默删除；取消后的迟到响应不会发布
  最终回复（交付事务内复查状态）。
- 部分完成：部分来源失败但其他来源有效 => `partial`，卡片标注失败来源，
  报告写明覆盖不足；交付消息头部明示"部分完成"。
- 全部失败 => `failed`（错误区分来源），保留用户消息，无伪成功回复；重试入口可用。
- 整理失败 => `failed`（stage=synthesizing），查询记录与候选保留，重试默认只跑整理。
- 结果只向来源会话交付一次；切换会话/刷新不取消任务，也不把结果写进新会话。

## 6. 前端

`conversation-home.js/css`：来源会话内可展开任务卡（`ch-task-card`）——查询与上限、
真实步骤、候选/入库数、失败来源、可折叠来源明细、取消/重试/下载按钮；
不展示编造百分比与完成时间；完成后原会话交付摘要 + 引用（view_references 卡片），
完整报告走下载目录（导出目录自动列出实际保存的报告，标注范围、时间与
部分完成等状态）与 `GET …/export`。能力检测：`research_search=false` 的旧后端
不启用检索卡片。

## 7. 验收结果（2026-10-20）

离线 Python（临时项目、真实 FastAPI、临时 SQLite、可控检索响应、离线模型夹具；
不调用真实/付费模型、不请求公网检索源）：

- 专项 `tests/test_research_tasks.py`：**23 项通过**。覆盖：明确请求启动 vs 普通问答/
  页面操作不误启动；模糊请求澄清卡片；卡片必须真实存在；查询/来源/候选上限生效
  且实际数量被记录；DOI/arXiv/标题去重、相似标题不合并、已有库不被覆盖；
  零结果/部分失败/全部失败三分；吞异常被识别为 error 而非零结果；仅元信息不出内容
  结论；伪造引用拒绝且候选保留；注入文本只作为数据存储且不触发额外任务；
  幂等重放/同 ID 冲突/并发双击单任务/重试不重复入库与消息；同项目竞争 409、
  跨项目隔离；结果只进来源会话、GET 只读不重启；整理失败保留候选可只重试整理；
  取消后迟到响应不发布；重启中断可识别、GET 不写库、持锁运行不误判中断；
  新材料使旧建议失效、正式主线不被修改；导出保留来源/范围/部分完成标记；
  任务不触碰流水线上下文（kv 无 `pipeline.context.v1`）；legacy 会话拒绝。
- 合并回归（会话/工作台/产品边界/引导/阶段图/知识库）：**247 项通过**。
  `test_conversation_home.py` 14 项单独复跑通过；其中 start 竞态用例在多文件
  组合负载下出现过两次偶发失败（该路径本次未修改，单独与全文件复跑均通过，
  判定为既有负载偶发，未修改其断言或超时）。
- JavaScript：`conversation-home.js` 与浏览器脚本语法检查通过。

浏览器（`tests/research_tasks_test_server.py --port 8017` + `tests/research_tasks_browser.cjs`，
真实应用 + 离线检索夹具，Playwright/Edge，**STEP5_BROWSER_OK**）：

- 桌面 1440×900：明确请求 → 任务卡（排队/运行）→ 已完成 → 交付消息含"检索与整理"
  → 下载目录列出"研究现状整理"条目；来源明细可展开；无编造百分比；无横向溢出。
- 部分完成卡标注失败来源；全部失败卡提供重试入口；澄清卡片（start_search）→
  编辑主题 → 启动成功；运行中取消（先确认服务端登记 `cancel_requested` 再释放
  阻塞调用）→ 已取消且无最终交付消息；窄屏 390×844 卡片可用、无横向溢出。
- 全程 `pageerror` 为空。截图：`docs/conversation-research-tasks/*.png`（completed /
  partial / failed / cancelled / clarify / mobile）。
- 隔离验收服务已关闭：8017 无监听。**（当时）8001 全程未启动**；该结论仅适用于
  2026-10-20 验收当日——收尾前的真实演示（第 9.1 节）与收尾验收（第 9.8 节）
  均在隔离环境或明确授权下进行，未提交或推送代码。

### 未验证事项（不以离线通过替代）

- **真实来源连通性未验证**：本轮未实际请求 OpenAlex/arXiv/Semantic Scholar/CrossRef
  公网接口，来源可用性、限流与延迟未知。
- 真实模型质量未验证：查询规划、相关性理由与综述整理的模型表现需在明确授权下
  单独抽样评价；`LLMRegistry` 的 JSON 兼容、费用与延迟未验收。
- 浏览器验收为软件夹具（fixture 论文），不构成真实检索质量或科研结论验收。
- 未验收：跨主机部署、账号权限、大规模并发、真实移动设备软键盘。
- `test_conversation_home` 的 start 竞态用例偶发失败已在收尾阶段完成排查、
  复现与修复（根因：Windows SQLite 3.53 并发首次建库的瞬态只读误判；
  修复：建表阶段有界重试）——详见第 9.7 节。

## 8. 第六步可复用的能力

- 任务底座：`sra_research_tasks` 表、七态状态机、OS 锁与中断探测、
  幂等与单任务互斥、分阶段重试（跳过已完成的检索阶段）——教学案例任务与
  跨学科方案模板可直接复用。
- 契约：`SearchLimits` 有界配置、`SourceOutcome` 来源审计、候选 `paper_id`
  与材料指纹联动、`GuideDraft`/`ActionProposal` 白名单动作、
  `SynthesisValidationError` 引用校验。
- 引导与卡片：`guidance_policy.CAPABILITIES` 声明式能力（新增能力 = 服务端实现 +
  声明 + 来源保存，不改提示词宣称）；`start_search` 卡片的服务端绑定与可编辑
  范围模式可直接推广到第六步的任务卡片。
- 交付与导出：任务报告 Markdown 渲染（`report_markdown`）、导出目录接入
  （`research_task_entries`）、交付消息同一事务提交模式。

## 9. 第五步收尾：真实运行问题修复与稳定性验收（2026-10-21）

### 9.1 真实环境演示（背景，2026-10-20）

8001 主服务以真实模型模式（`.env`：`SRA_DRY_RUN=false`，DeepSeek 经 TokenDance
网关）完成一次真实演示：会话内发送"帮我检索一下 sulfide solid electrolyte
这个方向的近期代表性论文"→ LLM 规划 3 条查询 → arXiv 成功返回 10 条
（OpenAlex 30 秒超时）→ 模型筛选识别 3 条 SoLid 粒子探测器术语撞名 →
10 篇入库 → 交付消息含引用列表与证据边界声明。任务状态 `partial`。
截图见 `docs/demo-screenshots/`。

演示暴露的问题与修复（见 9.2–9.5）。本轮**未重跑真实检索、未重复调用模型**，
仅只读核对该演示项目的任务、候选与报告。

### 9.2 partial 补跑（新增能力）

- `POST …/research-tasks/{task_id}/retry` 新增 `resume` 语义：`partial` 任务
  只允许补跑——**沿用原查询计划（不重新规划），仅重试此前失败的 (查询，来源)
  调用，不重跑已成功来源**。`partial + re_search=true` 返回 409
  （更换检索范围应发起新任务，不伪装成原任务重试）。
- `source_calls` 保留全部历史调用（含补跑），partial/completed 判定与
  `failed_sources` 按**每个 (查询,来源) 的最新一次尝试**计算：来源修复后
  完整性状态可更新为 `completed`，审计记录不覆盖、不丢失。
- **报告版本**：每次成功交付追加一版（`report_versions`），旧版本保留；
  补跑再次失败时旧报告与下载入口保持有效，不冒充成功（任务保持 partial，
  会话中明确说明"补跑未成功"）；补跑成功但候选与依据无实质变化时
  不重复调用模型、不生成重复报告（会话说明"保留原报告"）。
  导出接口支持 `?version=N` 下载历史版本（按其交付时状态渲染）。
- 前端任务卡：partial 状态显示"补跑失败来源"按钮；显示版本列表
  （v1、v2（补跑）（当前 vN））与历史版本下载入口；补跑交付消息
  在会话中明确标注"补跑完成 / 仍有来源失败"。
- 并发：连续/并发点击补跑经事务内终态重判 + 项目级互斥串行化，
  只允许一次执行（`attempts` 只 +1）；取消/中断沿用既有执行权保护。

### 9.3 代理配置（进程级修复，不全局禁用）

- `web/api.py` 启动时把 `localhost,127.0.0.1,::1` 并入进程级
  `NO_PROXY`/`no_proxy`（`core/tools/http.ensure_loopback_direct`，
  幂等、保留既有条目、不清空配置）——本地回环（本地模型网关、本服务
  自调用）永远直连；外部检索与模型访问默认尊重既有代理配置。
- 新增可选开关 `SRA_SOURCE_DIRECT=1`（默认关闭）：开启时仅检索来源的
  HTTP 请求显式绕过代理（`core/tools/http.http_get`，四个检索工具统一
  出口），不影响模型客户端或其他出网请求。**未在启动脚本写入
  `NO_PROXY=*`，未修改系统级代理设置。**
  （此前演示采用"清空代理变量启动 uvicorn"的临时方式，属环境操作，
  不构成代码方案；本轮修复后无需清空代理即可在代理环境下运行——
  前提是代理自身允许访问检索来源。）
- **脱敏诊断**：来源失败记录 `error_class` 分类
  （`proxy_error` / `timeout` / `rate_limited` / `auth_or_blocked` /
  `network` / `unknown`），前端来源明细显示中文类别（如"来源超时"），
  使用者可区分代理连接失败、来源超时、限流、认证失败与成功无结果；
  分类只依据状态码与异常类型，不输出密钥、代理密码或完整认证 URL。

### 9.4 模型路由与截断处理

- `config/tasks.yaml` 已补 `research_plan` / `research_rank` /
  `research_synthesize` 三条路由（DeepSeek）；路由缺失（`TaskNotFoundError`）、
  输出截断（`LLMTruncatedError`）与不可解析输出统一由
  `except Exception` 捕获并**回退规则模式**，降级原因写入任务视图的
  `planning_note` / `ranking_note` / `synthesis_note`，前端以
  "降级说明"展示——不允许静默降级后仍显示"模型已完成规划/筛选"。
- `max_tokens` 传递链路核对：`tasks.yaml → TaskConfig.max_tokens →
  LLMRequest.max_tokens → provider`，逐环节确认生效；`research_rank`/
  `research_synthesize` 设为 16384（reasoning 模型思考链 + 20 篇候选
  JSON 输出在 4096 下触发截断；registry 内置一次 2 倍升窗重试，上限
  32768）。输出预算、输入长度与请求超时三者分离（`max_tokens` /
  prompt 截断上限 / `call_timeout`）。
- 截断内容**不发布**：整理环节截断/不可解析回退规则模式报告（只汇总
  元数据）并标注；引用伪造（`SynthesisValidationError`）仍为任务 failed，
  候选保留、可只重试整理。重试上限：registry 内 1 次、任务层人工触发，
  无自动循环。日志只记录状态与用量，不含模型推理内容。
- 以上分支均以模拟提供方响应验证（`FlakyRegistry` / `NoRouteReg` /
  `TruncReg` 夹具），未反复调用真实模型。

### 9.5 主题提取修复（保留合法术语）

- `core/research_tasks/detect.py`：新增**连接词截断**——"，并总结 /
  然后归纳"等整理类动作连接词之后的内容（对结果的处理要求）不属于主题；
  "并行计算""并联电路"中"并"后接非动作词，不受影响；尾部
  "近期/最新/代表性 + 论文/工作"等对象修饰一并清理；中英混合、
  括号与连字符术语保留原样（`Li7La3Zr2O12 LLZO garnet-based 电解质`）。
- 覆盖规范用例（8 组，含"帮我了解这个方向""我只是想知道需要检索哪些问题"
  不误启动），已固化为 `test_topic_extraction_safe_terms`。
- 误启动防护（页面操作一票否决、泛指黑名单、元话语剥离）全部保留。

### 9.6 检索计数语义统一

- **上限含义（配置=代码=界面=文档一致）**：`max_queries=3` 任务级查询数；
  `sources_per_query=2` 每条查询取前 2 个来源；`max_results_per_call=10`
  单次来源调用返回上限；`max_candidates=20` 是**去重合并后的任务级候选
  总上限**（不是每查询每来源各自 20）。报告新增"检索计数"说明段，
  前端任务卡同步标注。
- **计数链条**：来源原始返回（source_calls.count）→ 去重合并候选
  （candidates_count）→ 纳入整理（included_count）/ 标记不相关
  （excluded_count，模型显式判定，规则模式不做排除以免中文整串匹配
  误杀）→ 新入库 / 关联已有（saved_papers_count）。被标记不相关的候选
  保留在候选列表与论文库（`candidates_only` 元数据），不进入报告引用，
  前端显示排除原因。真实演示中"检索 10 条、排除 3 条撞名、入库 10 条"
  与此语义一致：排除发生在综合引用层面，候选材料完整保留。

### 9.7 start 创建竞态排查（已复现、已定位根因、已修复）

**复现与定位（临时诊断脚本，跑后已删除；产品代码经多轮打点收敛）**：

- 组合负载稳定复现：竞态用例显式放 pytest 命令行第一项 + 4 个重测试文件
  跟随时 100% 复现（hunt 1-3 轮 + diag 3 轮全部出现）——并发第二个请求
  返回 503 `{"detail":"开始对话的记录暂时不可用，请保留原 request_id 后重试"}`。
- 逐层打点（环境变量门控 `SRA_START_DEBUG`）定位根因：错误发生在
  `ConversationStarter.transaction()` 的 `CREATE TABLE IF NOT EXISTS` 阶段
  （BEGIN IMMEDIATE 本身成功），底层异常为
  **`sqlite3.OperationalError: attempt to write a readonly database`**。
  现场证据：主库 0 字节、旁边残留空 `-journal`、文件属性正常（非只读）、
  Python 可正常以写模式打开该文件（`write_open=ok`）、路径长度正常。
- **根因结论**：Windows + SQLite 3.53 在并发首次建库的瞬态窗口内，把新
  连接误分类为只读（0 字节主库 + 残留空回滚日志的状态），并非锁竞争或
  文件权限问题；`timeout=15` 对这种状态无效。

**修复（`core/workspace/start.py` `transaction()`，正规并发容错而非掩盖）**：

- 建表阶段（connect / BEGIN IMMEDIATE / CREATE TABLE，均在调用方代码之前）
  外加**有界重试**：最多 3 次、间隔 0.15s 递增，仅对 readonly / locked /
  busy 三类瞬态错误重试；非瞬态错误仍以 ConversationStorageError 抛出。
- 重试成功后并发第二请求走幂等复用路径返回 200，或走冲突校验 / OS 锁
  返回 409——响应语义不变，未删除测试、未加自动重试断言、未放大超时。
- 曾尝试 WAL 模式方案（可从机制上消灭回滚日志），但 ro 预检查连接会泄漏
  `-wal`/`-shm` 边车文件、破坏"被拒绝请求不产生任何文件"契约，已回退并
  在代码注释中记录原因。
- 修复过程中发现重写版本遗漏 `closing()` 导致异常路径连接不关闭（4 项
  文件树断言失败），已补回。

**验证（修复后）**：

- 组合负载 3/3 轮：`200 + 409`，零 503（此前同负载 100% 复现 503）。
- 单文件 5 轮全过；9 文件相关回归 **208 项全部通过**（包含竞态用例）。
- 组合回归期间出现过 1 次 `test_resume_failure_keeps_old_report` 失败
  （5 文件负载 158 秒长跑），针对性复现（两测试组合 4 轮 + 5 文件 3 轮）
  均通过——按约定如实记录为**未复现的偶发时序**（`wait_terminal` 20 秒
  轮询在组合长跑下的夹具时序干扰最可能），保留观察、不写"已彻底修复"。

### 9.8 收尾验收结果（离线，2026-10-21）

- Python 专项：`tests/test_research_tasks.py` **35 项通过**（23 项原有 +
  12 项新增：partial 补跑只重试失败来源、partial+re_search 409、补跑失败
  旧报告保留、零新增不重复整理、并发补跑单次执行、路由缺失/截断降级可见、
  计数与排除语义、版本导出、主题提取、回环 NO_PROXY 保留）。
- 组合回归：9 文件（会话/任务/导航计数/产品边界/项目会话/产物下载/引导/
  知识库/验收回归）——见文末运行记录。
- 浏览器（8017 隔离服务 + Playwright/Edge，**STEP5_BROWSER_OK**，7 场景）：
  新增场景 2b——partial 卡展开 → 旧报告下载入口可见 → 点击"补跑失败来源"
  → 状态变为"已完成" → 版本列表 v1、v2（补跑）→ 历史"下载 v1"入口可用；
  窄屏与桌面无横向溢出；全程 pageerror 为空。8017 已关闭。
- 本轮未进行的真实验证：未重跑真实检索、未重复调用模型、未验证代理环境下
  的真实来源连通性（`SRA_SOURCE_DIRECT` 的实际效果需在真实代理环境单独
  授权验证）；OpenAlex 真实超时行为未重新测量。

### 9.9 尚未解决的问题

1. **OpenAlex 真实环境 30 秒超时**（演示中两次出现）：来源级直连开关
   `SRA_SOURCE_DIRECT` 与补跑可缓解，但根因（真实网络路径或来源限速）
   需在真实环境单独确认。
2. start 创建竞态 503 **已修复并组合验证**（见 9.7）；修复后组合回归期间
   另出现 1 次未复现的 `test_resume_failure_keeps_old_report` 偶发，最可能
   为组合长跑下的轮询时序干扰，保留观察。
3. `tests/test_research_scope.py`（早期"范围契约"轮次的未跟踪文件）存在
   10 项与本轮无关的遗留失败：其断言 materials/chemistry/physics/energy
   创建应被 422 拒绝、前端目录只有 3 个计算机领域、guide 文案含"不代跑
   实验"——但为配合 GOAI Track 3 材料方向，后端 `PUBLIC_DOMAINS` 已扩至
   9 领域（支持 materials）、前端与文案同步演进，该契约测试未跟随更新。
   **本轮未改动**（不在本轮 9 个回归文件之列，且属范围契约的产品决策），
   需后续单独决定：按现行 9 领域实现更新该测试，或恢复更严格的范围。

   后续状态（第六步 A，2026-10-21）：已按明确的理工科研究方案定位统一九领域，
   同步前端、别名、画像与范围测试，保留临床/人体/药物/危险操作等不支持项。
   原范围测试的过时预期已经更新并通过组合回归；这里保留的是第五步当时的记录。
   当前契约见 [研究方案模板与领域范围](research-plan-templates.md)。
4. 模型相关性判定的质量（relevant 标记的准确率）未做真实抽样评价——
   需要单独授权的真实模型评估，本轮仅验证机制与边界。

本步（含收尾）到此停止；未开始第六步理工科方案模板与教学案例。
