# 第八步 A：真实流程可靠性修复与验证

验收日期：2026-10-21。基于当前未提交工作区，不代表一个已发布版本。

## 1. 结论与范围

本轮只修正真实研究流程的可靠性，没有开始第八步 B。产品仍止于 DESIGN；未执行训练、实验或论文生成，未写入真实用户项目，未提交或推送。

**软件侧离线与隔离浏览器路径通过；真实端到端未通过。** 真实样例在来源阶段阻断，不能据此宣称真实方案生成、采纳与导出已经验证成功，更不能证明方案科学正确。

### 问题—原因—改动—证据

| 问题 | 本轮定位 | 改动与结果 |
| --- | --- | --- |
| 来源失败被当成零结果 | 任务层曾捕获 WARNING，再用日志内容判断业务状态 | 四个实际来源适配器返回显式诊断；关闭日志、改变级别不改变状态。成功无结果与超时、限流、认证、网络、解析错误分开 |
| 排除被写成未检索到 | 模型承担范围统计；补跑关联已有论文时还可能把排除者带回综合输入 | 服务端生成覆盖快照和范围文案；综合只接收纳入资料；已有论文重新关联仍遵守筛选。4 返回／1 纳入／3 排除回归通过 |
| 长方案依赖网页持续等待 | 网页请求约 30 秒、旧验收等待 60 秒、生产 SDK 默认 120 秒是不同层级；上下文还含重复方案和历史材料 | 明确方案请求可及时返回 202，复用持久请求、锁与状态查询；精简模型输入；增加实际分段耗时。不改模型、不扩大生产超时。离线交付／失败／恢复通过，真实方案仍未验证 |
| 次数与费用审计不全 | 旧验收假定 httpx；实际环境是 OpenAI 3.0.0 及其使用的 httpx2 客户端，逻辑调用不等于网络尝试 | 使用 SDK 实际客户端事件钩子、关闭 SDK 内部重试，由有界机制重试并逐次预留。未知 usage／费用为 null，不补造历史次数或金额 |
| 上传丢响应后重复 | 原上传每次产生新 paper_id，没有稳定请求身份 | 项目 SQLite 中保存上传收据、内容摘要和准备数据；同 request_id 重放复用结果；提交论文和完成收据原子化。响应丢失、并发与进程死亡恢复通过 |
| 旧会话伪造任务操作归属 | 取消／重试原先在操作后才核对会话 | 改为写入前核验归属；错误会话返回 404，任务状态不变 |

## 2. 来源状态契约

涉及 `core/tools/search_result.py`、四个来源工具、统一 HTTP 出口和 `core/research_tasks/sources.py`。

- `SearchResults` 保持 list 兼容；旧调用者仍能迭代论文。新任务层读取 `status`、`raw_count`、`available`、`parse_errors`、`error_class`、`retryable`、`retry_after_seconds`、`attempts`。
- 任务接口保留原 `ok / zero / error`，避免破坏调用方。有部分有效结果且部分解析失败时保留结果，增加 `degraded`，任务标为部分完成；全部不可解析为失败，不是成功无结果。
- `raw_count=null` 表示原始返回数量未知，例如网络失败或整个响应不能解析；`count` 是实际解析、过滤后可供任务使用的记录数。`available` 如存在，只是来源报告的匹配总数，不是已读取数量。
- 错误类别按 HTTP 状态和异常类型产生：`rate_limited`、`timeout`、`authentication`、`access_denied`、`proxy_error`、`network`、`server_error`、`http_error`、`parse_error`、`unexpected_error`。不从日志或错误文本猜状态。
- 每次来源尝试保存主机、时间、耗时、HTTP 状态或异常类型，不保存认证信息。日志不参与业务判断。
- 不隐式跟随重定向，不进行隐藏的来源重试。429 的 `Retry-After` 持久保存，等待期内补跑返回 409；没有额外来源请求。
- arXiv API 地址从 HTTP 改为 HTTPS，避免明文入口重定向。此项已做离线地址断言，**修改后的公网可达性未再次验证**。
- 保留环境代理和既有来源级直连开关，没有启用 `NO_PROXY=*`，没有全局关闭代理。限流约束以项目内持久记录为范围，不是跨项目或平台级限流器。

这些修改不批量改写历史任务。普通 list 自定义适配器被视为其声明的成功结果；维护者应升级为显式结果，而非依赖其日志。

## 3. 报告范围与证据

新报告附加 `report.coverage`（版本 1），由实际任务记录生成：

| 字段 | 含义 |
| --- | --- |
| `source_calls` | 每个查询与来源最近一次状态；全部历史调用另保留在任务原字段中 |
| `raw_returned_attempt_total` | 各次来源原始记录累计，含重复；任一数量未知则整体为 null |
| `valid_returned_attempt_total` | 各次有效返回累计，含重复与补跑 |
| `latest_valid_returned_total` | 各查询／来源最近一次有效返回之和，不等于去重数 |
| `unique_before_limit`、`screened_candidates` | 候选上限前的去重数、实际处理候选数 |
| `omitted_by_candidate_limit`、`sources_at_result_limit` | 候选截断数量、达到单次上限的来源；提示覆盖有限 |
| `included_count`、`excluded_count`、`excluded` | 纳入与排除数，以及逐条理由和判定来源 |
| `newly_saved_count`、`linked_existing_count` | 新入库与关联已有，二者分开，不混称纳入综合 |
| `metadata_only_count`、`abstract_count`、`full_text_read_count` | 纳入资料的可用阅读范围，本任务未读取全文 |

保留旧 `saved_papers_count` 含已关联文献的兼容含义。需要精确区分新增与已有时读 coverage，不重新解释旧字段。

服务端负责 summary、范围统计、证据范围缺口和 DESIGN 内的下一步。模型只解释允许引用的代表资料、方法关系、内容局限；被排除文献不成为综合依据。旧模型返回的统计字段即使存在也不会取代程序事实。

引用校验继续执行；对明显的“其余几篇未返回”和“已阅读全文／执行实验”等冲突做有限检查。**不是通用语义或科学真实性验证**，仍需人工核对论断与原文。少量摘要整理不称全面研究现状，证据缺口不等于创新性。导出保存覆盖快照、排除原因、来源历史与证据边界；旧任务原始结果不被改写。

## 4. 模型与网络审计

项目 SQLite 新增 `sra_call_audit`，仅明确执行时初始化。读取接口不建表、不建库：

```text
GET /api/projects/{project_id}/call-audit?operation_id=...&limit=30&offset=0
```

结果保持有上限的分页：`{entries: [...], next_offset: null|number}`。研究任务与消息请求状态同时附加 `call_audit`。消息操作标识为 `chat:{conversation_id}:{request_id}`，研究任务使用 task_id；关联项目、会话、请求、任务与预算组。

三类记录互不混算：

- `logical`：产品调用，例如 research_plan 或 design_method_formalize。
- `network`：SDK／来源 HTTP 尝试，包括失败和重试；发送前在短事务中预留次数和可配置预算。
- `phase`：上下文准备、生成、结构解析、依据校验和保存；不是额外模型调用。

使用安装环境的公开 `DefaultHttpxClient`，不是猜测某个 httpx 版本并猴补它。SDK `max_retries=0`；兼容提供方有界重试至多配置值且封顶 3 次额外尝试，注册表既有结构错误／截断一次重试仍通过同一计数闸门。长限流等待不密集自动重试。未接入可约束传输的提供方在产品审计作用域内拒绝调用；本轮未给 Anthropic 等其他传输补全审计。

已检查当前安装的 `httpx2` 默认 HTTPTransport 源码，底层连接重试参数默认 0；生产构造没有注入额外重试传输。离线注入的是可计数 MockTransport，不把任意第三方自定义传输也宣称为已审计。

默认持久预算为同一消息请求／任务累计模型 6 次、来源 12 次；重试不悄悄清零。内部授权验收可显式进一步收紧。本轮真实样例模型最多 6、来源最多 4，并启用首个模型失败即停止及模型名称核对。GET 不消耗预算，不自动重新调用。

返回 usage 才保存 usage；未提供则 null。可配置有出处的单价和预留费用闸门；计价是基于 usage 的**估算**，不是账单。没有可核验单价就不计算费用；要求程序自身费用上限而没有单价时在请求前拒绝。网络中断或进程死亡留下的未完成审计记录只表示结果未知，不能当作免费或成功。

本轮用户确认账户后台已设置测试 ¥1 硬额度；此为用户确认，应用没有独立查询或修改该额度。未取得网关费率或账单，实际费用仍未知。没有给第七步漏记请求补造精确次数。

## 5. 方案等待、恢复与测量

发送和重试沿用会话接口，兼容默认同步行为：

```json
{
  "request_id": "stable-plan-request",
  "content": "请为当前问题形成计算研究方案",
  "plan_request": {"method": "computational"},
  "wait_for_completion": false
}
```

有效方案请求先保存用户消息和处理状态，再在持有原请求执行锁的工作线程中生成，及时返回 202。它不是新队列，也没有新增聊天／方案数据库。`wait_for_completion` 是交付偏好，不参与请求内容身份；相同 request_id 不因同步／异步切换而重复生成。

```text
GET  /api/projects/{p}/conversations/{c}/requests/{request_id}
POST /api/projects/{p}/conversations/{c}/requests/{request_id}/retry
     {"wait_for_completion": false}
```

刷新、切换、查询状态不取消服务端生成、不自动重试。失败保留用户消息；明确重试复用原消息并重新核对材料快照。进程终止后通过原有 OS 锁判为 interrupted，不把其他进程正在持有的执行误判为中断。没有保证外部模型 exactly-once；请求已经发送但进程死亡时，用户重试可能再次计费。

模型上下文只保留当前问题、已知条件、会话历史、关联资料、必要笔记和指定修订目标；不把同一旧方案从记录／建议／修订目标重复携带，也不带无关实验和写作材料。校验仍用完整材料快照。草案先保存，用户明确采纳后才形成正式方案；迟到草案依据变化仍不可直接采纳，部分文本不会使 planReady 为真。

### 本轮耗时证据

| 环节 | 观察 | 能说明什么 |
| --- | --- | --- |
| 离线夹具方案上下文 | 约 0.18 秒 | 本地准备可单独测量，不代表大项目性能上限 |
| 离线受控等待＋生成 | 约 0.75 秒，含浏览器刷新与释放门闩 | 可脱离持续页面请求完成；不是实际模型速度 |
| 离线结构解析 | 约 0.003 秒 | 与实际 HTTP 耗时区分 |
| 离线依据检查／保存 | 约 0.004／0.055 秒 | 有效草案保存链路完成 |
| 本轮真实规划 HTTP | 3.123 秒；逻辑调用 4.377 秒 | 当前模型接口能返回规划；不是研究方案生成 |
| 本轮真实方案生成 | 未发出 | 因来源全部失败按授权停止，无法判断原方案延迟根因 |

网页 30 秒等待不再决定方案是否继续；生产 SDK 默认 120 秒未扩大。第七步约 60 秒验收等待不被当成统一生产超时。网关排队、长输出或实际方案结构质量，仍需另一次有预算的真实验收定位。

## 6. 上传幂等与文件／事务边界

```text
POST /api/projects/{project_id}/upload-paper
multipart: file, title（可选）, request_id（可选，主应用会稳定提供）

GET /api/projects/{project_id}/upload-requests/{request_id}
```

请求身份包括文件名、完整文件 SHA-256 和标题，不只看文件名。相同身份重放返回原 paper_id，完成后 `already_uploaded=true`；相同 request_id 不同内容／参数返回 409。并发相同上传只有一个解析者，其他请求收到 202 processing。用户主动以新的 request_id 另行导入相同文件允许创建另一份资料，不擅自全局去重。

旧客户端不传 request_id 仍可上传，但不能获得响应丢失后的稳定重放保证。新版前端保存当前项目的请求 ID 和文件摘要，不保存文件字节；恢复时先“核对上次上传”，不会 GET 时重新解析。重试需用户重新选择同一文件与标题。

`sra_upload_requests` 先持久化收据和稳定 paper_id，解析在数据库写事务外完成。已解析的论文、片段、元信息、证据准备数据先保存为检查点，再在同一事务中发布论文／片段／证据并标记 completed。提交失败不会留下半份正式记录；准备数据已保存的重试不再解析。

PDF 文件先写独立 `.part`，fsync 后原子替换到当前 paper_id 独有文件名，不覆盖其他同名资料。SQLite 与文件系统不是跨系统原子事务：进程在检查点前死亡可能需要重做解析，可能残留尚未发布的准备文件；它们不冒充已入库资料，本轮不自动清理用户目录。此处不承诺解析外部服务 exactly-once。

PDF 提取失败仍可保留元信息与明确 parse_error／阅读范围，不冒充读到正文；同一完成收据不会偷偷重解析。真实 MinerU／扫描 PDF 质量不在本轮验证范围。无效文件与缺失项目数据库不创建替代项目或空库。

## 7. 本轮验证记录

全部软件测试用临时数据。浏览器使用真实主应用 index.html、实际 FastAPI、SQLite、SDK 和来源解析器，仅替换外部 HTTP 边界，禁止其他外网调用；没有 mock 掉主要业务接口。

| 验证 | 本轮实际结果 |
| --- | --- |
| 跨模块回归（会话、方案、指导、边界、工作台、历史兼容、导出、教学等） | 347 passed，0 failed，0 skipped；`.pytest_cache/step8a-regression-final.xml` |
| 后续可靠性＋SDK＋上传／研究任务专项 | 101 passed；`.pytest_cache/step8a-delivery-complete.xml`，含实际子进程死亡后恢复和 HTTPS 地址断言 |
| 筛选后关联已有论文、旧报告补跑保留 | 2 passed；`.pytest_cache/step8a-task-last.xml`。测试保留关键断言，并补充排除者不能重入综合 |
| 最后受影响消息／上传／研究任务组 | 42 passed；`.pytest_cache/step8a-last-affected.xml`，不计为额外不同用例 |
| 审计闸门与交付最终专项 | 53 passed；`.pytest_cache/step8a-audit-delivery-final.xml`，含模型失败后也禁止后续来源请求；与上面用例重叠 |
| 上述主要报告去重 | 349 个不同用例；不是三组数量相加，也不包含历史第七步通过数量 |
| JS 语法和受影响 Python 模块编译 | 通过 |
| 浏览器 1440×900 与 390×844 | 报告、方案等待／刷新、采纳、实际 MD 下载、上传丢响应核对、超时失败／刷新／主动重试、来源部分失败及限流补跑 409 均通过；无未处理 pageerror 或横向溢出 |
| 实际截图查看 | 已查看桌面方案等待、窄屏材料、来源部分失败、生成失败；操作可见，不用强制点击绕过遮挡 |

测试过程中保留的失败证据：

- 首轮跨模块 343 pass／1 fail：旧 SourceControl 夹具把异常对象当函数调用，产生 TypeError 而非其声称的 TimeoutError。修正夹具实际抛出目标异常，保留“超时不能当无结果”的断言；后续整组通过。
- 新增进程死亡测试最初把 UploadService 构造参数顺序写反，未进入准备检查点；修正测试后单项和完整专项组均通过，失败报告未删除。
- 初期还发现上传路由漏导入 JSONResponse、解析失败丢失网络尝试信息及 JSONDecodeError 归类错误；均在实际代码修复，相关测试随后通过。
- `test_resume_failure_keeps_old_report` 本轮整组与受影响专项均通过，没有本轮复现证据；仍保留历史偶发问题，不宣称根因已消除。pytest 原有 asyncio_mode 配置和 utcnow 弃用警告尚存，不是本轮通过被跳过的理由。

### 真实调用（与离线分开）

用户本轮单独授权一个公开联邦学习样例，并确认账户侧 ¥1 限额。使用临时项目，未上传私人材料。

| 实际尝试 | 结果 | 时间／usage |
| --- | --- | --- |
| deepseek-v4.1-flash 规划 | HTTP 200 | HTTP 3.123 秒；输入 174、输出 243、总 417 token（其中响应报告 reasoning 160） |
| OpenAlex | ReadTimeout，来源失败，不是无结果 | 约 37.316 秒（30 秒读取超时设置不等于含连接的总墙钟时间） |
| arXiv HTTP 入口 | HTTP 301，被明确报告为配置重定向错误，不隐式追加请求 | 约 1.904 秒；随后代码改 HTTPS，仅离线验证 |

实际合计模型 **1 次**、来源 **2 次**；无隐藏补跑。来源全部失败即停止，未耗尽授权次数后继续试运气。费用未知，未记录为零。没有生成研究现状报告、正式问题、方案或假结果。本轮真实端到端**未通过**；真实方案时延、内容质量、采纳和导出未验证。此前 OpenAlex 曾可达，本轮失败原因没有足够证据归因于服务端、代理或本地链路，未用改代理／扩大超时掩盖。

## 8. 截图与简短复现

截图呈现说明（第八步 B，2026-10-22）：本目录的部分同名截图在复跑浏览器时刷新为第八步 B 界面，不能再充当第八步 A 的原始视觉基线；不改变本文第八步 A 的真实调用次数和未通过结论。第八步 B 后续输出已分离至 [独立目录](ui-experience-unification/reliability/report-desktop.png)，前后布局对比见[第八步 B 记录](ui-experience-unification.md)。

- [范围报告（桌面）](real-workflow-reliability/report-desktop.png)
- [方案等待与恢复（桌面）](real-workflow-reliability/plan-processing-desktop.png)
- [方案正式版本下载（桌面）](real-workflow-reliability/plan-download-desktop.png)
- [上传核对（窄屏）](real-workflow-reliability/upload-mobile.png)
- [材料（窄屏）](real-workflow-reliability/materials-mobile.png)
- [生成失败与原请求重试（窄屏）](real-workflow-reliability/plan-failed-mobile.png)
- [部分来源失败（窄屏）](real-workflow-reliability/source-partial-mobile.png)

```powershell
# 只用临时数据的隔离实例，不是 8001。
venv/Scripts/python.exe tests/real_workflow_test_server.py --port 8051
# 另一终端，本机需 Playwright 与 Edge：
node tests/real_workflow_browser.cjs
# 关闭上述自己启动的隔离实例：
Invoke-RestMethod -Method Post http://127.0.0.1:8051/__reliability__/shutdown
```

手动路线：在此离线隔离实例首页明确检索联邦学习 → 展开来源与统计 → 讨论并采用问题 → 方案生成与审阅准备请求并发送 → 刷新核对原请求 → 审阅后明确采纳 → 下载当前正式方案。文献页上传测试 MD，可使用“核对上次上传”。固定夹具不代表真实研究建议质量。

真实脚本 `tools/verify_real_workflow.py` 仅供**重新取得当轮授权后**使用，同时要求 `--allow-live --account-cap-confirmed`；不要在 CI 或为重现截图随手执行。当前授权运行已经结束，不能自动重跑。

## 9. 交付状态及第八步 B 可依赖能力

8001 只读检查仍为 PID 30416；其能力响应没有 `background_plan`、`upload_request_identity`、`call_audit`，**尚未加载本轮后端**。没有自行启停。新版静态页面会检测这些能力：不会向旧接口强塞异步字段，不会把旧上传当成幂等上传。要体验全部修复需之后由用户授权更新后端，不能把静态文件生效说成后端已更新。

本轮隔离服务仅 8051，结束后关闭；实际子进程中断测试只终止自己创建的临时测试进程。未更改真实项目和 8001。

第八步 B 可依赖：来源显式诊断与降级字段、report.coverage、持久请求状态及 call_audit、异步方案交付偏好、上传 request_id 与状态查询；材料指纹、事务采纳、planReady 和 DESIGN 边界保持原契约。

剩余限制：真实外网检索仍阻断，真实方案未验收；其他研究方法／提供方真实质量、全格式 Word/PDF、真实 PDF 解析、实体手机键盘、平台账单核对、长时间并发负载未验证。后台方案线程不是可跨进程续算的任务队列，进程死亡需明确重试。没有进行公网安全、收费或部署准备。用户体验认可仍待用户确认。

到第八步 A 停止，不自动开始第八步 B。
