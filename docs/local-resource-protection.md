# 本地资源保护

本轮实施起点：2026-10-28；分支 `feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`。工作区存在大量前轮修改，全部保留。本轮不操作 8001、8002、8077，不读取或修改实际账号库和历史项目。

## 实施前入口与归属清单

| 实际入口 | 执行方式与已有保护 | 本轮缺口／处理位置 |
| --- | --- | --- |
| 会话 messages、retry、restart；workspace/guide 旧入口 | ConversationService.send；会话 OS 锁、事务幂等、调用累计上限、P1 恢复、材料指纹与发布校验；方案可后台线程执行 | 在幂等判定后、processing 登记前统一准入；工作真正退出后释放 |
| research-tasks 创建、消息检索分流、retry／resume 补跑 | ResearchTaskService；项目 OS 锁、事务幂等、阶段取消、来源调用超时与数量上限；每次 runner 启动线程 | 登记前准入；runner 启动失败需落明确失败状态；实际工作结束后释放 |
| run、resume、run-topic-discovery | 项目状态互斥、线程存活检查、人类确认、DESIGN 边界；推荐后可继续 pipeline | 统一名额，覆盖自动衔接工作整个生命周期 |
| regenerate-method-doc | 项目内 job 互斥、后台线程、原版本保留 | 启动前准入，异常和启动失败释放 |
| papers/import；无本地文件的 PDF 获取 | 同步外部元信息／PDF 获取；已有本地 PDF 直接 FileResponse | 外部获取统一准入；已有下载不占名额 |
| upload-paper | FastAPI 自动 multipart；await file.read 整文件入内存；UploadService 锁、request_id、prepared 数据与回执事务 | 接收前限制并发；实际请求字节和 multipart 文件／字段／头限制；有界分块读取；解析失败清理本次文件 |
| upload-topic | 自动 multipart；整文件读取；更新当前项目主题 | 同样接收边界与解析准入，不产生半成品主题 |
| 登录／注册、项目与会话创建、列表、材料／审计／回执读取、笔记、采纳、已有文件导出 | 统一 Cookie 会话、CSRF、项目及嵌套归属、用户幂等空间；教学使用保存的离线内容 | 不占重任务名额；保留所有权限与科研保护 |

身份沿现有 account_boundary 服务端会话传入，不接受客户端所有者字段；旧本地演示模式为一个明确的本地使用者。后台执行保存提交时身份，不跟随页面切换。

## 最小设计

单进程内集中配置、线程锁保护的非阻塞准入，拒绝时不登记科研 processing／queued、不建立等待队列、不消耗模型预算。接收上传有独立小容量，解析和其他科研生成共用重任务容量。取消只表达请求，名额不能在底层调用退出之前释放。进程结束后内存名额消失，数据库已有恢复／幂等和 OS 锁继续决定是否可明确重试，不自动重放。

## 实施结果与文件

资源保护实现及新增专项已完成。新增专项 **38 项通过**；既有回归 **278 项通过、2 项失败**。两项失败是旧用例对结构校验失败后直接重试的期望与 P1 契约冲突，见下文，未删断言或放松校验。因此本轮**未完成“全部既有回归全绿”**，不能把交付写成无剩余复核事项。

| 文件 | 本轮修改 |
| --- | --- |
| `core/resource_limits.py` | 集中配置、服务端身份上下文、线程锁下原子准入、幂等释放、后台任务执行键；不建队列 |
| `core/workspace/conversations.py` | 会话锁后、processing 前申请；后台方案移交原 ExitStack 时一起移交名额；同步生成也用相同名额 |
| `core/research_tasks/service.py` | 开始／补跑／重试登记前申请；启动失败明确落 failed；后台执行到实际返回才释放；queued 执行键防止尚未领取时重复启动；跨进程旧 queued 显示中断 |
| `core/workspace/uploads.py` | 回执与文件哈希幂等判定后、processing 前申请解析名额；失败回执保留；原 prepared／发布事务保护继续使用 |
| `web/bounded_uploads.py` | 接收准入、有界 multipart 解析、文件／请求／字段／头／数量上限、匿名 spool 句柄清理、接收超时、有界远端 PDF 响应读取 |
| `web/api.py` | 会话身份传给准入；429 原因码；受保护配置接口；旧后台生成／推荐衔接／导入／远端 PDF／格式转换准入；手动有界上传并保留 OpenAPI multipart 契约；PDF 解析失败不入库、只清理本次文件 |
| `web/static/resource-protection.js`、`index.html` | 登录后读取服务端大小配置，公开显示限制，文件哈希／上传前检查；不信任浏览器检查代替服务端校验 |
| `web/static/conversation-home.js` | 繁忙后恢复输入并保留原 pending 请求编号；不自动重试；修改附件／采纳目标时不误用旧提交；取消仍运行的说明；附件选择和迟到响应保护保留 |
| `web/static/app.js`、`workspace.js` | 现有上传入口提示大小、提前检查、失败保留选择／标题及原回执；无工作台改版 |
| `.env.example` | 集中资源参数示例；不改模型路由、科研提示词或预算配置 |
| `tests/test_local_resource_protection.py`、`test_local_resource_restart.py` | 原子并发、真实 HTTP／ASGI、临时数据库与文件、进程重启、离线替身专项 |
| `tests/local_resource_test_server.py`、`local_resource_browser.cjs` | 限定临时目录与非保留端口、两个账号、事件控制的实际工作、主应用浏览器验证 |
| `tests/resource_offline_guard.py` | 可选 pytest 插件，阻断非回环 socket；不修改产品网络配置 |
| 两个既有测试文件 | 账号正向 PDF 用例提供明确的离线解析结果、补全新配置路由清单；原会话互斥测试明确设置每用户容量 2，保留其独立会话在容量内可并发的原断言 |

没有更改真实用户账号或项目，没有迁移、认领、重建历史材料，也没有提交、推送、部署。

## 资源限制表

环境变量在**进程启动前**配置，前缀均为 `SRA_RESOURCE_`。所有数值为正整数；请求体上限须大于文件上限。大小使用字节，1 MiB = 1,048,576 字节，1 KiB = 1,024 字节。

| 类别／参数后缀 | 用户默认 | 全局默认 | 规则 |
| --- | --- | --- | --- |
| 重任务 `HEAVY_USER` / `HEAVY_GLOBAL` | 1 | 3 | 对话、方案、检索、解析、旧生成／外部获取及 PDF／DOCX 格式转换共用；原子申请，立即拒绝，无等待队列 |
| 上传接收 `RECEIVE_USER` / `RECEIVE_GLOBAL` | 2 | 2 | multipart 接收、暂存、读取期间占用；解析期间保持接收名额，另申请上面的重任务名额 |
| 文件 `FILE_BYTES` | 8 MiB | 每文件 | 8,388,608 字节；论文、主题文件和旧远端 PDF 文件入口都按实际字节检查 |
| 上传请求 `BODY_BYTES` | 9 MiB | 每请求 | 9,437,184 字节，含 multipart 边界和字段；不只信任 Content-Length |
| 文本字段 `FIELD_BYTES` | 4 KiB | 每字段 | 4,096 字节；每次最多 2 个文本字段（title、request_id），最多 1 个文件；重复或无关字段拒绝 |
| multipart 头 `HEADER_BYTES` | 8 KiB | 每部分 | 8,192 字节，也约束 Content-Type／boundary 长度，避免在头解析时无限积累 |
| 内存 spool `SPOOL_BYTES` | 64 KiB | 每文件 | 超过后由 SpooledTemporaryFile 滚动到匿名临时文件 |
| 分块 `CHUNK_BYTES` | 64 KiB | 每次 | 65,536 字节；接收向解析器馈送、文件读取、远端 PDF 读取均有界 |
| 接收 `RECEIVE_SECONDS` | 120 秒 | 每请求 | 等待接收／multipart 解析超过时限返回 408，清理本次句柄；兼容项目 Python 3.10 下的 asyncio.wait_for |

接收与重任务是两个不同计数：两个账号各上传一份文件时，最多两个接收／解析链路；它们的解析还必须满足全局重任务总数 3。普通会话不占接收名额。注册人数与可同时运行的重任务数量无对应承诺。

这些是本地保守默认值，**没有经过真实服务器、真实大文件或 30 个重任务的负载验证**。限制的是外层操作；旧流水线内部仍有原有固定小线程池（例如子查询最多 4 路、每子查询的两个来源并行、质量增强最多 6 路），外层持有一个名额直到其子工作结束。因此“全局 3”不等于“全系统最多 3 个网络连接”。未把原子查询提示词、来源路由或原调用上限改掉。

## HTTP 契约与覆盖

`GET /api/resource-limits` 在账号模式下需要登录，只返回公开配置，不返回其他用户的占用、邮箱、项目或任务。状态修改继续使用 Cookie 会话和 CSRF，准入身份只取服务端会话。明确的本地兼容模式共享一个本地使用者配额；不因鉴权失败退回该模式。

容量不足：HTTP 429，`reason_code=account_busy` 或 `system_busy`，`detail` 为可读说明，`retryable=true`、`retry_after_seconds=5`，`Retry-After: 5`。这是建议等待时间，不自动建立重试或处理轮次。文件／请求／字段／数量超限是 413；接收超时 408；格式不完整／解析失败等为相应 400／415／422，存储或启动失败明确提示 503。响应继续使用 private、no-store。

| 路由组 | 申请／不申请 |
| --- | --- |
| POST conversations/{cid}/messages；requests/{rid}/retry、restart；workspace/guide | 共用 ConversationService.send，先处理重复与旧状态，再取会话锁、申请名额，最后登记 processing；原模型调用预算不变 |
| POST conversations/{cid}/research-tasks；消息检索分流；任务 retry（含 resume） | 共用任务服务，事务内幂等判断后申请，登记后移交给 runner；同一活跃执行键的重复 retry 返回原任务 |
| POST run、resume、run-topic-discovery | 先走 DESIGN 边界和原项目互斥；确需新工作才申请。推荐转 pipeline 时父工作等待子工作实际退出，同一个名额覆盖整个衔接 |
| POST regenerate-method-doc | 原项目 job 锁和活跃检查后申请；创建／启动异常和实际完成各有释放路径 |
| POST upload-paper、upload-topic | 不用 FastAPI 自动 File 参数收完整请求；显式接收限制在 multipart 解析前生效；论文解析在回执判重后申请重任务；主题更新也申请 |
| POST papers/import | 旧同步外部获取入口使用同一重任务准入 |
| GET papers/{paper_id}/pdf | 已有本地文件仍免费占用名额数为 0；确需远端获取才申请；响应分块计数超限立即失败，不继续换 URL 下载 |
| GET download/{artifact_type} | 已有 Markdown 下载不占重任务；PDF／DOCX 重新格式转换占重任务 |
| POST human-response、任务 cancel | 控制已经持有名额的工作，不额外申请；取消不作为释放依据 |
| 登录／注册、项目和会话创建、列表／搜索／计数、材料、笔记、审计、状态、回执、报告版本导出、明确采纳 | 不启动重任务，不占名额；保留原完整归属、CSRF、幂等、版本及采纳保护 |
| 教学保存内容的选择和读取 | 不调用模型／来源／解析，不占名额；教学不能偷偷转成实时研究 |

[逐路由表](local-resource-protection/route-coverage.md) 与 [JSON](local-resource-protection/route-coverage.json) 来自实际应用 OpenAPI，覆盖 **89 个产品路由方法**。表中另列出的 `__resource__` 只是本轮隔离服务的控制端点，产品 `web.api:app` 不注册它们。科研 CLI／操作系统直接文件操作不是本轮 Web 准入边界；不提供多进程或分布式限额承诺。

## 幂等、取消、失败与重启

- 新拒绝不插入 processing／queued，不插入科研用户消息，不进入调用审计的模型／来源调用，也不消耗它们的预算。已有项目／会话若在首次发送的独立建档步骤已成功建立，仍归原用户所有；前端保留该身份和草稿，不重复建档。
- 同一有效消息、检索请求先按原 payload_hash／request_id 判重，返回原结果／处理状态；上传先完成有界接收及哈希核对，再复用原回执，不重复解析或占重任务名额。接收本身仍受独立容量限制，超出接收容量时可用轻量回执查询核对原上传。
- 容量拒绝后的原请求编号可以再次使用。前端同一输入的手动恢复沿用原编号；更换文字、附件或目标时按现有用户编辑流程处理，不把新增内容塞进旧提交，也不暗建 P1 新轮次。
- 正常结束、生成／解析异常由 finally 释放；Thread.start／runner 启动失败释放并记录明确失败。research 的 queued 阶段也登记内存执行键，避免 OS 项目锁尚未被 worker 领取时，被 retry 误判为可重复执行。
- 取消只置 cancel_requested。当前来源／模型调用仍在运行时，名额保持，重复取消／重试不会创建第二个执行。刷新后的状态也返回 `work_still_running` 与“取消已提出，当前操作尚未结束”的说明；原迟到结果与阶段发布保护保留。
- 同步模型或解析报超时，只有调用真的返回／抛错、工作退出后才释放；不做按墙钟强行释放名额。浏览器离开、断开或退出不证明线程已经停止。已完整接收、正在解析的上传即使 HTTP 请求取消，解析线程仍保留自己的重任务名额，直至实际结束；它可能完成有效入库，应查询原回执，不能把未收到响应当作失败后自动重发。
- 服务进程退出后内存名额消失，OS 锁由系统释放。running 和旧进程的 queued 按原恢复语义显示中断，只能明确重试；不自动重放模型／来源调用。已保存的会话、归属、回执、材料、版本和审计保留。不能证明外部服务在断连前是否已经执行，也不承诺外部调用 exactly-once。
- 旧 pipeline 在人工确认等待期间，仍有活着的工作线程，因此保守地保留名额；用户继续或中止后等线程真正结束。卡住的第三方调用会占住有限容量，不能靠“取消已提出”无限叠加新任务；本轮未新增强制杀线程或新任务平台。

## 上传接收、内存与临时文件

接收路径为：身份与归属检查 → 非阻塞接收准入 → Content-Length 快速检查和实际流字节计数 → 限量 multipart callbacks → 64 KiB 内存 spool／匿名临时文件 → 64 KiB 分块读取（实际累计不得超过文件上限）→ 回执与哈希幂等检查 → 解析重任务准入 → 准备与事务发布。

不再调用无尺寸参数的 `await file.read()`，也不在完成无限 multipart 接收后才检查大小。既有业务解析仍需要一个**已检查且有上限**的 bytes 缓冲，文本解码、分块及 JSON 准备数据也会产生有界的额外内存；这不是严格的进程 RSS 上限。解析器对异常、超时、请求中断和取消都关闭本次持有的 spool 句柄；匿名临时文件随句柄或进程退出清理，无须扫描历史项目。远端 PDF 入口也按实际读取字节限制并在 finally 关闭响应，避免旧下载入口整段 `response.content` 带来同类问题。

PDF 的项目 `.part`／目标路径含服务端生成的 paper_id，不覆盖其他论文同名文件；上传子目录必须位于该项目内。准备／解析失败只清理该调用创建的、路径已核对的 PDF 和 `.part`，不扫描目录，不清理旧附件。未得到可读取内容的 PDF 解析失败返回错误、不入库、不返回成功回执；历史已有的“仅元信息”论文仍可读，未批量改写。

若准备已成功、仅发布失败，原 UploadService 的 prepared 数据及其对应文件继续作为原回执的可恢复准备数据保留，不作为有效论文或正式材料发布，明确重试可复用。硬退出恰好发生在命名 `.part`／PDF 写入后、准备或发布前时，可能留下该次孤立文件；后续使用同一回执重试只处理其专属 paper_id。**没有实现对放弃重试的孤立文件的自动回收**：管理员需根据回执先预览、核对后再单独处理，不扫描或自动清理真实历史项目。这与正常失败的临时句柄／`.part` 清理区别列出，不能声称所有异常退出都零残留。

没有累计磁盘配额、用户总存储计费或附件总数量限额。已发布资料、可恢复 prepared 文件、用户反复完成的新上传仍会累积；真实 PDF 解压／第三方解析器输出的 CPU、内存、页数、子进程及磁盘扩张也未做操作系统级隔离。后续公网内测仍需容量验证和这些边界的专项处理。

## 验证证据与计数

全部使用临时 SQLite／文件、真实 FastAPI／ASGI、离线 SDK／来源／解析替身，主要并发断言用 Event／Barrier 控制。接收超时用 1 秒小阈值；文件边界用 512／513 字节、远端 PDF 用 2048／2049 字节，不使用巨大文件或耗尽内存。启动和终态轮询不作为并发上限的证明。

| 类型 | 去重结果 | 证据 |
| --- | --- | --- |
| 新增资源专项 | **38 passed**：其中 34 个真实 FastAPI／ASGI 场景、1 个独立进程重启场景、2 个直接 Starlette 解析器场景（断连／超时）、1 个内存准入补充场景 | [主体 33 项](local-resource-protection-special-final-v2.xml)、[格式转换与取消 3 项](local-resource-protection-last-checks.xml)、[远端文件 2 项](local-resource-protection-remote-file-checks.xml) |
| 既有账号／注册隔离回归 | **16 passed** | [XML](local-resource-protection-accounts-final.xml) |
| 既有科研／会话／报告／恢复／边界回归 | **255 passed、2 failed** | [XML](local-resource-protection-regression-final.xml) |
| 既有产物与文献导入下载回归 | **7 passed** | [XML](local-resource-protection-download-regression.xml) |
| 浏览器新增端到端 | **1 条双账号／双标签页综合路线通过**：账号及全局繁忙、文字保留、刷新、原编号恢复、恰好一对消息、大小预检、B 下载已保存报告、取消仍占用；1440px／390px | [JSON](local-resource-protection/browser-results.json) |
| 浏览器既有附件回归 | **8 个变体通过**：同名不同内容、重选、移除再选、刷新恢复、相同字节新选择、缺失／错配 manifest 等；核对 A／B 实际内容与上传次数 | [JSON](local-resource-protection/attachment-regression/browser-results.json) |
| 浏览器既有笔记回归 | **1 条综合路线通过**：草稿刷新／跨项目、迟到响应、保存时新输入、失败／清空与新会话失败 | [JSON](local-resource-protection/notes-regression/browser-results.json) |

pytest 总计 **318 个不同用例，316 passed、2 failed**，详见 [逐用例汇总](local-resource-protection/test-summary.json)。浏览器路线单列，不与 pytest 子断言相加。早期诊断、重复执行、最后的局部复测均不累加；例如 [句柄真实创建并关闭](local-resource-protection-parser-close-check.xml)、[原请求审计与重启复测](local-resource-protection-final-audit-restart.xml)、[multipart OpenAPI 复测](local-resource-protection-openapi-check.xml) 都是重复核对。既有 asyncio_mode 配置与 datetime 弃用警告未伪装成测试错误。

专项不只检查状态码：用阻塞期间的真实计数、进入回调次数、8 个线程竞争时恰好 3 个获准、每用户最多 2 个、原请求重放只有一次实际执行、原拒绝请求没有处理记录和调用审计、取消后重复操作仍只有一个来源调用、真实下载字节、临时句柄已创建且全部关闭、旧附件字节不变等断言核对。上传读操作只接受配置的分块尺寸；超限没有有效论文、成功回执或新正式材料。

双进程验收实际使用 8089：仅终止本测试启动且正阻塞的子进程；同一临时数据重新启动后，会话、归属、笔记和上传保持，原检索显示中断，重任务名额 0、没有自动来源／模型调用，再明确重试原 task_id 成功。见 [重启 JSON](local-resource-protection/restart-results.json)。这不能替代真实网关断连时的外部执行核查。

### 失败及修复记录

1. 首个专项测试对 SQLite 只用了事务上下文而未关闭连接，导致 Windows 清理临时目录失败；改为 closing，并补句柄关闭验证。遗留目录处理见服务状态部分。
2. 最初准入放在会话 OS 锁前，改变同一会话忙碌时的原冲突优先级；改为锁后、processing 前申请。原互斥断言保留，独立会话测试显式在每用户容量 2 内验证原行为。
3. 旧账号正向 PDF 用例没有可读解析结果，依赖过去“解析未启用也入库”的行为。本轮要求失败不产生有效论文，因此为该正向隔离用例配置明确离线解析器，保留所有权／实际下载／路由矩阵断言；解析失败由新增反向用例覆盖。
4. 初次把账号模式与旧匿名夹具混在一个 pytest 进程，旧夹具复用已载入的 accounts 模块，产生 401 及外部 requests 替身互相影响；分成独立进程、旧夹具明确设 local 后恢复，未降低产品默认鉴权。
5. 初版浏览器脚本未点击已有项目的“添加待上传论文”中间按钮，等待 filechooser 超时；修正为实际用户路径。另一次仅等待报告终态而未等待实际名额释放，下一请求被正确拒绝；改为核对实际占用归零后建立阻塞场景，不通过缩短保留名额来迎合测试。附件旧脚本初接到缺少其专用状态端点的夹具返回 404，换回正确离线夹具后 8 个变体通过。
6. **未完成：两项旧回归仍失败。** `test_first_batch_reliability.py::test_condition_provenance_and_one_bounded_repair[invented]` 与 `[paraphrase]` 期望结构／依据校验失败后，直接 `/retry` 就生成成功。当前已审核 P1 将该失败标为 `structure_invalid`、`can_retry=false`，所以返回原失败，未新调用模型。失败信息与准入 429 无关。保留原断言和必要校验，不借本轮资源改造修改依据策略；交独立复核决定后续对齐旧用例的处理方式。不能计入通过数。

### 截图与实际文件

已实际打开检查：[账号繁忙桌面](local-resource-protection/account-busy-desktop.png)、[系统繁忙 390px](local-resource-protection/system-busy-narrow.png)、[文件超限 390px](local-resource-protection/file-too-large-narrow.png)、[取消尚未结束 390px](local-resource-protection/cancel-pending-narrow.png)。另有 [文件超限桌面](local-resource-protection/file-too-large-desktop.png) 与既有附件／笔记回归目录。繁忙／超限文字、仍保留的输入及操作按钮可见，无横向溢出。

在 A 实际占用名额时，B 实际下载了 [自己的报告 v1](local-resource-protection/b-owned-report-v1.md)，核对正文 [1]／[2] 与对应题名、作者、年份、DOI／URL、摘要阅读范围。文件是离线软件夹具，不是科学验证；没有覆盖任何真实历史导出文件。

## 可重复执行

在仓库根目录 PowerShell 中运行。所有测试仅使用临时数据。

```powershell
# 新增专项（包含新进程；非回环 socket 阻断）
.\venv\Scripts\python.exe -m pytest -p tests.resource_offline_guard tests/test_local_resource_protection.py tests/test_local_resource_restart.py -q

# 账号回归使用独立 pytest 进程
.\venv\Scripts\python.exe -m pytest -p tests.resource_offline_guard tests/test_local_account_isolation.py tests/test_local_registration.py -q

# 旧匿名夹具：只对这个进程设环境，不运行主服务
$env:SRA_AUTH_MODE='local'
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
.\venv\Scripts\python.exe -m pytest -p tests.resource_offline_guard tests/test_project_conversations.py tests/test_conversation_home.py tests/test_research_tasks.py tests/test_p1_recovery_attribution.py tests/test_research_delivery_closure.py tests/test_research_delivery_supplement.py tests/test_first_batch_reliability.py tests/test_product_plan_boundary.py tests/test_real_workflow_reliability.py -q
```

最后一条会如实报告上述两个既有冲突用例，不应删断言使其变绿。

### 隔离浏览器实例与双账号人工路线

另开 PowerShell，以仓库根目录为工作目录：

```powershell
.\venv\Scripts\python.exe -m tests.local_resource_test_server --port 8088
```

只监听 127.0.0.1:8088，默认新建 `%TEMP%\sra-resource-browser-*`。显式 `--root` 也只允许 OS 临时目录下的 `sra-resource-*` 直属目录，拒绝 8001／8002／8077。测试配置固定为重任务每用户 1／全局 1、文件 512 字节、请求 2048 字节，目的是用小数据稳定展示边界；不改变产品默认配置。该夹具的全部模型、来源与解析网络均为离线替身或禁止，控制端点不属于产品。

用户在第二个 PowerShell 获取本次目录，并创建自己知道密码的两个额外临时账号（安全交互，不把密码放命令行）：

```powershell
$sraReviewRoot = (Invoke-RestMethod 'http://127.0.0.1:8088/__resource__/state').root
.\venv\Scripts\python.exe -m tools.local_accounts --db (Join-Path $sraReviewRoot 'accounts.db') create reviewer-a@example.com
.\venv\Scripts\python.exe -m tools.local_accounts --db (Join-Path $sraReviewRoot 'accounts.db') create reviewer-b@example.com
```

1. 两个独立浏览器上下文分别登录 A、B；A 创建两个研究项目／会话，B 创建自己的项目、笔记及小文本附件。B 可以先完成一个离线检索并下载报告。
2. 在第二终端 POST `http://127.0.0.1:8088/__resource__/hold`，随后 A 在第一个会话发送普通讨论，离线模型工作会停在事件上；可 GET `__resource__/entered` 核实已进入。
3. A 在另一个标签页／项目发送文字，应看到账号繁忙、文字保留；B 发送应看到系统繁忙，但 B 仍可查看项目和下载已有资料。不要看到其他用户的项目或任务名称。
4. POST `__resource__/release`。A 的原任务完成；A 在被拒会话用原请求重新提交，应只出现一条用户消息和一条助手回复。刷新核对草稿和原身份，不能自动生成新轮次。
5. 选择 513 字节文本文件应提前显示上限，文字保留。512 字节可接收。通过 API 绕过前端、缺少 Content-Length 或伪造长度，仍由实际字节校验；失败可查询原回执，刷新后文件需重选。
6. 要展示取消，先 POST `__resource__/hold-source`，明确发起离线检索，GET `__resource__/source-entered` 确认进入；点击取消后仍显示操作尚未结束，并继续占名额。重复取消／retry 不增加执行；POST `__resource__/release-source` 后等实际退出。
7. 检查桌面和 390px，退出 A、登录 B，不能混入 A 的输入、附件或资料。夹具中两个内置测试账号仅供自动化；无需在报告或终端展示其密码。

可用现成浏览器脚本（需要 Node 环境中可用 Playwright 与 Edge）：

```powershell
node tests/local_resource_browser.cjs
```

结束时在第二终端关闭本次隔离服务，或在其前台终端 Ctrl+C：

```powershell
Invoke-RestMethod -Method Post 'http://127.0.0.1:8088/__resource__/shutdown'
```

默认 TemporaryDirectory 随正常退出清理；若显式指定持久临时目录用于重启验证，结束后先核对该目录确为本次创建、服务已退出，再由管理员处理，不把清理命令指向真实项目。本文的验收地址在交付时**已关闭**，不是仍可访问的体验地址。

## 服务、数据与剩余限制

- 本轮真实模型调用 **0**，公网文献请求 **0**，邮件／外部解析服务调用 **0**。离线夹具的模型／来源次数另外记录于 [最终状态](local-resource-protection/fixture-final-state.json)，不能把替身计数冒充真实网络调用，也不能将其科学内容当作研究结论。
- 本轮启动 8088 资源验收、8089 两进程重启验收、8099 既有附件／笔记回归；都已停止。8088 和 8099 最终正常退出码 0。初次选错的 8099 夹具也已停止。最终无 8001／8002／8077／8088／8089／8099 监听；前三者仅观察状态，没有启停或占用。
- 最终 8088 数据目录为 `C:\Users\czh05\AppData\Local\Temp\sra-resource-browser-8kppvg3g`，正常退出后确认已不存在；此前两次重建的浏览器临时目录也确认已不存在。重启专项 TemporaryDirectory 已正常清理。pytest 自身保留的临时回归数据按 pytest 留存策略存在，没有扫描或删除真实历史项目。
- **清理受阻残留**：本轮首个失败测试的 `C:\Users\czh05\AppData\Local\Temp\sra-auth-e9ipkpl_` 因测试未关闭 SQLite 连接先出现 WinError 32。修复并结束测试进程后，尝试只删除该已核对的临时目录，自动审批返回 `blocked by policy`，未提供更具体原因。本轮没有换方法删除；该目录仍在，其中临时用户已全部禁用、会话全部撤销，核对 enabled users = 0、active sessions = 0；没有服务使用它。
- 未做真实公网服务器、真实模型／文献网络／第三方解析、真实大 PDF、操作系统级 CPU／内存隔离或多人压力测试。单进程计数不能直接用于多 worker；若以后部署多个进程，需要另行设计协调，不能宣称本实现跨进程全局限流。
- 没有用户累计存储上限、累计任务次数配额或存储计费；没有自动清理硬退出的孤立命名文件。恶意文档解压扩张、解析器子进程资源、慢速连接与连接总量、远端内容可信性、磁盘耗尽等仍需公网内测前另行验证／配置。
- 当前邮箱仍未验证，没有真实邮件、账号恢复或公网部署认证。本轮没有扩大账号平台，也没有取消任何科研调用上限、采纳、引用快照、版本或归属检查。

交付后停止，等待独立只读复核。上述两项旧回归冲突、命名文件异常残留回收和真实负载能力均未伪报为已完成。

## 独立审核补修（2026-10-28 至 2026-10-29）

本节更新前文的交付状态：两个审核问题已修复，两项旧测试已按 P1 契约对齐；前文的原始失败记录保留。仍在原分支 `feat/code-update-v2`、原 HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92` 上修改，保留工作区既有内容，未切换、重置、清理、提交、推送或部署。仓库及上级目录未发现适用的 `AGENTS.md`。本轮没有修改资源默认值、账号平台、科研提示词、模型路由或正式材料发布规则。

### 复现、根因与修复

| 问题 | 复现及根因 | 最终处理 |
| --- | --- | --- |
| 繁忙拒绝后版本冲突无法恢复 | 原编号先收到 429；另一标签页 PUT brief 使 revision 增加；旧 expected_revision 再提交得到 409。原 `firstSend` 在响应前清空输入，刷新材料又不会更新 pending 的提交依据，导致文字丢失和持续冲突 | 文字仅在确认服务端已接受、且编辑版本及内容／参考／目标仍匹配时清空。429、409、响应未知均保留 pending 和原正文。新输入（包括清空后重输相同文字）不被迟到响应覆盖 |
| 后台启动与失败落库同时异常时泄漏 | `ownership.pop_all()` 将名额和会话 OS 锁移交后，启动异常分支先 `_fail`，后 `lease.close`；落失败记录再次抛错就跳过释放 | 未启动时用 finally 关闭原 lease，与失败记录能否写入无关。明确返回“后台工作未启动，失败状态未能保存”，不谎称已落库。工作已进入或 Thread 已有 ident 时，保留 worker 的 finally 释放责任，先查询原状态 |
| 两项旧测试错误允许结构失败直接 retry | invented／paraphrase 原断言在模型替身改成有效结果后，期望 retry 成功，与 `structure_invalid` 的 P1 契约不符 | 保留逐字依据、事实归属和最多一次修复断言；改为 failed、structure_invalid、can_retry=false；直接 retry 仍 422，调用数、attempt、审计、用户消息、正式材料均不变，无新草案 |

对应产品修改仅为 `web/static/conversation-home.js` 和 `core/workspace/conversations.py`。新增专项为 `tests/test_resource_review.py`、`tests/local_resource_review_browser.cjs`；对齐 `tests/test_first_batch_reliability.py`。另将既有下载回归的离线响应替身补齐分块读取／关闭协议，并增加 `stream=True` 与关闭断言，未改变产品下载实现。`tests/local_resource_browser.cjs` 只新增截图输出目录环境变量，以保留原轮证据。

### 原请求与人工核对契约

1. “发送”和“用原请求重新提交”复用 `resumePending`。先 GET 原消息请求；只有明确的“消息请求不存在”404，且已有检索任务列表也核对不到相同编号，才视为未接受。网络失败、归属错误或不完整查询都不授权重发／更改依据。已接受的请求展示原结果／状态，不重复 POST。
2. 未接受的请求如内容、参考或目标已变化，“发送”提示先核对原请求，保留原 pending 和新编辑；用户可用“用原请求重新提交”先处理原内容，再单独发送新编辑。新编辑不会偷偷塞入旧请求，也不自动新建处理轮次。
3. 收到 409 后持久化 `needs_review=true`，刷新材料仍保留原 expected_revision。用户主动继续时打开“核对更新后的研究依据”，显示原文、原参考／目标、原版本／当前版本和当前结构化条件，明确可能再次调用模型，正式问题／方案仍需采纳；也可返回材料核对，暂不提交。
4. 用户确认后再次查询原状态、读取当前 revision。仅原请求仍未接受且材料版本与刚核对的一致时，更新原 body 的 expected_revision，保留原 request_id、正文、selected_paper_ids、selected_item_id 和 plan_request。核对期间又变更则要求重新核对，服务端 CAS 检查继续生效。
5. 刷新恢复相同 pending。参考文献勾选现随现有账号隔离的草稿保存，勾选改变时立即保存；旧草稿缺该字段则按原默认空选择读取，不推断用户已接受新参考。原附件回执／selection_id、内容哈希、刷新后重新选文件和用户隔离机制继续生效。旧缓存或旧请求没有被批量改写。

HTTP、数据库表和 request_id 的服务端幂等规则没有放宽。原 body 和版本之间的手动核对只适用于**确定尚未接受**的请求，不用于修改已接受请求。调用次数上限、结构失败禁止盲目 retry、恢复轮次与明确采纳规则均保留。

### 故障注入与资源责任

新增 5 个真实 FastAPI／临时 SQLite 专项：

- context 捕获、Thread 构造、Thread.start 三个故障点，分别叠加 `_fail` 存储异常：503 的说明准确，实际模型替身调用为 0，重任务计数归零，会话 OS 锁释放。原编号／attempt／用户消息保留，读状态按原锁规则显示 interrupted。存储恢复后同账号可在另一项目操作；原请求明确 retry 后 attempt=2，只有一对消息，生成未采纳草案，正式材料不变。
- Thread 确已执行但启动确认丢失：用 Event 精确阻塞真实 worker；HTTP 返回状态未确认时名额和锁继续持有，其他项目 429，实际只执行一次；释放 Event、join 工作后名额／锁才归零。取消保护未改为提前释放。
- 429 → 条件修订 → 409 → 原状态 404 → 人工核对后的最新版本／原编号成功；旧 body 重放返回已接受结果，调用只有一次、消息只有一对。

已复查 research `_launch`、旧 run／topic-discovery／method-doc 的移交异常分支：它们在失败写入之前释放，未发现同一个“落库抛错跳过释放”缺口，未顺带重构。服务重启仍按原规则将失去工作锁的 processing 判为中断，不自动重放；本轮没有清零审计、旧尝试或预算。

### 测试结果、失败记录与证据

pytest 按 `classname::name` 去重，最后一次执行结果为 **323 passed，0 failed，0 skipped**：本轮新增 **5**，此前资源专项 **38**，既有账号／注册 **16**，既有科研／P1／采纳／报告版本／下载 **264**。两项对齐测试包含在既有 264 中，不计作新增；局部复跑和诊断不累加。[逐用例与来源汇总](local-resource-protection/review-20261029/test-summary.json)。

| 执行组 | 实际结果 | 证据 |
| --- | --- | --- |
| 本轮 5 项故障注入／API ＋既有资源 38＋账号 16 | 59 passed | [XML](local-resource-review-resources-accounts.xml) |
| 科研／会话／P1／引用版本／下载首次合并回归 | 263 passed、1 failed | [原始 XML](local-resource-review-scientific-regression.xml) |
| 上述下载失败修复替身后，下载相关完整复跑 | 7 passed（与上一行重叠，不加总） | [最终 XML](local-resource-review-download-final.xml) |
| invented／paraphrase 等 5 个原参数单独核对 | 5 passed（重复核对） | [XML](local-resource-review-legacy.xml) |

首次合并回归的唯一失败是 `test_imported_arxiv_paper_can_download_pdf`：旧 SimpleNamespace 响应只有 `.content`，没有本轮资源保护使用的 `.iter_content`／`.close`，导致 502。补齐测试替身后，仍验证相同 PDF 字节、单次目标 URL、文件路径回写，并新增分块请求和关闭断言，未删除或放松产品校验。原始失败 XML 保留，323 的汇总是逐用例采用最后结果，**不是声称最初合并执行即全绿**。已知 asyncio_mode 配置和 datetime 弃用警告仍在，未将其伪写成通过项或新增问题修复。

浏览器使用真实主应用、Cookie／CSRF、临时账号和实际 HTTP／SQLite。只对响应交付时序和离线工作做可控替身；检查 POST body、请求编号、数据库消息、进入模型回调次数、占用计数、输入／草稿／附件／目标及可见提示，不只检查 HTTP 状态或 pageerror。

- 新增版本恢复路线：发送、原请求重提、390px 刷新恢复、409 等待期间新输入、附件变更、目标变更、另一标签页先接受、原状态未知、清空后重输同文、原参考与目标随刷新保留，共 **10 个不同变体**。每条目标实际生成恰好 1 次，原消息恰好 user／assistant 各 1 条；429／409 不触发模型。[最终结果](local-resource-protection/review-20261029/final-recovery/browser-results.json)。
- 既有双账号／双标签页资源综合路线 **1 条通过**：用户／全局繁忙、原编号恢复、超限不上传、B 下载已有报告、取消后仍占名额、桌面／390px。[结果](local-resource-protection/review-20261029/resource-regression/browser-results.json)。
- 附件竞态既有 **8 个变体通过**，同名不同内容、移除／重选、刷新、相同字节新选择、缺失／错配 manifest，核对 A／B 内容和实际上传数。[最终结果](local-resource-protection/review-20261029/final-attachment-regression/browser-results.json)。
- 笔记草稿既有 **1 条综合路线通过**，覆盖刷新、跨项目、迟到响应、保存期间新输入、失败／清空和新会话失败。[结果](local-resource-protection/review-20261029/notes-regression/browser-results.json)。

诊断失败也保留：最初浏览器脚本把阻塞请求的 Promise 放在随后导航离开的页面中，断言读到 undefined；改为独立阻塞标签页，产品接口未改。随后“原参考／目标”补测发现草稿未保存 paperIds，导致刷新后“发送”误判引用已编辑；已将参考选择加入原草稿并在勾选时保存。[修复前结果](local-resource-protection/review-20261029/original-context-initial/browser-results.json)、[补修后单项](local-resource-protection/review-20261029/original-context-final/browser-results.json)。以上失败尝试和早前 9 变体通过都不额外累计。

已实际查看桌面 1440px 与窄屏 390px 截图，提示／核对按钮可读，无水平溢出。截图仅含临时账号标识和离线内容，无密码、Cookie 或令牌：

- [409 后原输入保留（桌面）](local-resource-protection/review-20261029/final-recovery/409-input-retained-desktop.png)
- [明确核对当前条件（390px）](local-resource-protection/review-20261029/final-recovery/explicit-review-narrow.png)
- [系统繁忙与输入保留（390px）](local-resource-protection/review-20261029/resource-regression/system-busy-narrow.png)
- [取消后仍占用的说明（390px）](local-resource-protection/review-20261029/resource-regression/cancel-pending-narrow.png)

### 复现命令与人工路线

仓库根目录，已安装项目虚拟环境与 Playwright／Edge。pytest 用两个进程避免账号模式夹具对旧本地模式模块的污染，所有网络由替身和非回环 socket 保护阻断：

```powershell
.\venv\Scripts\python.exe -m pytest -p tests.resource_offline_guard tests/test_resource_review.py tests/test_local_resource_protection.py tests/test_local_resource_restart.py tests/test_local_account_isolation.py tests/test_local_registration.py -q --tb=short

$env:SRA_AUTH_MODE='local'
$env:SRA_WEB_SKIP_GUARD='1'
$env:SRA_WEB_SKIP_PROJECT_SCAN='1'
.\venv\Scripts\python.exe -m pytest -p tests.resource_offline_guard tests/test_project_conversations.py tests/test_conversation_home.py tests/test_research_tasks.py tests/test_p1_recovery_attribution.py tests/test_research_delivery_closure.py tests/test_research_delivery_supplement.py tests/test_first_batch_reliability.py tests/test_product_plan_boundary.py tests/test_real_workflow_reliability.py tests/test_latest_artifact_download.py tests/test_paper_browser_import_download.py -q --tb=short
```

第一个命令在未设置上述三个环境变量的独立 PowerShell 执行。变量只对该终端及其子进程生效，不写 `.env`，不改变现有服务。浏览器实例启动／关闭继续使用前文命令，8088 自动创建 `%TEMP%\sra-resource-browser-*`，账号与项目均位于其中；只有回环监听、离线替身，拒绝保留端口。另一个终端运行：

```powershell
node tests/local_resource_review_browser.cjs
$env:SRA_RESOURCE_SHOTS='docs/local-resource-protection/review-20261029/resource-regression'
node tests/local_resource_browser.cjs
```

完整资源综合路线要求新启动的 8088 夹具（它断言从零开始的计数），不能紧接其他模型替身路线复用计数；先正常 shutdown 后重新启动。复核已有证据时应为各脚本设置新的截图目录环境变量，避免覆盖本文文件。新脚本支持 `SRA_REVIEW_SHOTS`；只重跑某变体可设 `SRA_REVIEW_VARIANT` 为脚本列出的名称。Node 需要其安装环境能解析 `playwright`；当前工作环境通过本地运行时的 `NODE_PATH` 提供，未新增生产依赖。

普通用户手动验证：按前文安全交互命令创建临时审阅账号；A 打开两个项目，用 `__resource__/hold` 暂停第一个的离线回复。第二个输入原文发送看到 429；在另一标签页打开第二项目的“项目进度”页面（`?page=progress&project=项目编号`），展开“编辑目标与资源”，修改条件并点击“保存项目约束”；release 原工作。回到原页面发送，看到 409 且原文字／参考／目标保留；刷新仍保留。点发送或“用原请求重新提交”，核对当前条件后明确继续，只有一对消息；等待期间输入的新文字或新选附件不能被清空。B 独立登录仍能查看／下载自己的已保存资料，不能看到 A 的内容。结束 POST `__resource__/shutdown`，等待前台正常退出，8088 的地址即失效。

附件／笔记夹具可在独立 PowerShell 临时设置 `SRA_AUTH_MODE=local` 后启动 `.\venv\Scripts\python.exe -m tests.real_workflow_test_server --port 8099`；自动创建 `%TEMP%\sra-step8a-*`。分别给既有浏览器脚本设置 `SRA_ATTACHMENT_URL`／`SRA_FIRST_URL` 为回环 8099，`SRA_ATTACHMENT_SHOTS`／`SRA_FIRST_SHOTS` 为新的证据目录。完成 POST `http://127.0.0.1:8099/__reliability__/shutdown`，正常退出时 TemporaryDirectory 自动清理。此为隔离的明确本地模式，无真实用户目录，不能使用 8001／8002／8077。

### 边界与服务交付状态

本轮真实模型、公网文献、真实邮件、外部解析服务调用均 **0**；替身回调数单列保存，不能等同真实调用。未访问或修改实际账号／历史项目，也未处理前文因安全策略拒绝清理的临时目录。未新增资源等待队列、预算或部署功能。

检索原编号核对使用既有分页接口，最多检查 1000 个历史任务；未能确认穷尽时按“状态未知”停止，不改依据或启动新调用。已选参考后来不可用、材料反复变更、存储持续失败等仍可能阻止交付，用户需解决原因，不能靠跳过版本／归属校验继续。新编辑与原请求并存时先处理原请求再发送新编辑，不提供自动替换原内容的入口。

本轮没有证明任意语义／科学正确性、真实模型恢复、真实公网网络或多人负载能力。前文累计存储、异常硬退出残留、单进程局限和公网内测前事项仍有效。最终服务退出、占用归零和临时目录状态见下方收尾记录；关闭本轮隔离实例后停止等待独立复核。

2026-10-29 收尾记录：本轮启动的 8088、8099 均正常退出，退出码 0；最终未发现 8001／8002／8077／8088／8089／8099 监听，前三者仅只读观察，从未启停或占用。8088 最终 heavy=0、receive=0、active_guidance=0、peak_guidance=1、public_calls=0；[夹具最终状态](local-resource-protection/review-20261029/final-fixture-state.json)。本次 8088 两个自动临时目录 `sra-resource-browser-s4kr35is`、`sra-resource-browser-b9flovtq` 正常退出后均确认不存在；8099 的 TemporaryDirectory 上下文正常结束，无清理异常。未触碰此前被安全策略拒绝删除的目录。pytest 临时数据仍按其自身留存策略保留；证据文件保存在本文引用的目录。测试地址目前均已关闭，需按说明重新启动，不能当作有效访问地址。本轮到此停止，等待独立复核。
