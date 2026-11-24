# 对话移动独立复核补修（2026-10-30）

本轮只修复目标核对竞态和明确拒绝后的退出恢复，不改跨库移动事务，不新增复杂对话迁移等能力。原交付与历史结果见 [conversation-moves.md](conversation-moves.md)，本轮统计另列，不与上一轮累计。

## 开始状态与原问题复现

分支仍为 `feat/code-update-v2`，HEAD 为 `903d1fc0a5d543abd307d65774bbaece6a025f92`。开始时已有 370 条修改／未跟踪记录，全部保留。检查仓库及上级目录，未找到适用的 AGENTS.md。未切换、重置、清理、提交、推送或部署。

通过当前原版页面、真实 FastAPI、临时账号和 SQLite 复现了两项问题（端口 8098，临时根 `sra-move-504327a1`）：

1. 用事件控制 A 的真实预检查响应，响应已取得但暂不交给页面；下拉框改成 B，再释放 A。页面显示通过并允许确认，实际请求是 **B 的目标 ID + A 的 expected_token**，后端返回 409。
2. 该 409 后取消、重开及刷新，目标仍被持久化 pending 锁定，没有重新选择入口。

诊断脚本：`tests/conversation_move_review_reproduce.cjs`。证据：`conversation-move-review/reproduction/diagnostic.json`、`wrong-target-authorized.png`、`rejected-target-locked.png`。这是缺陷复现证据，不作为修复通过用例累计。复现阶段没有移动或科研业务写入，没有模型调用。

## 原因与修复位置

### 目标与窗口绑定

`web/static/conversation-home.js::openMove` 原先在 await 之后才读取 select.value；临时变量 target 没有参与提交校验。目标 onchange 虽清了 token，A 的迟到结果仍可把它重新填入。关闭窗口也没有统一的检查代次与有效窗口约束，提交结果回调可直接导航。

现在每次检查捕获发起时的目标与 serial，检查结果保存为 `{target, token, serial}`。目标改变立即清除通过状态、禁用确认、递增 serial 并中止旧读取；即使旧读取仍返回，也必须同时通过目标、代次、窗口存活检查。提交前再次检查三者一致，再从该检查记录构造请求，不能从变化后的选择拼接旧 token。

每次窗口都有独立身份；新开窗口会关闭旧窗口。关闭后只允许已提交操作的结果按原请求身份写回该账号的 pending，不修改新窗口、不启用按钮、不导航。再次打开后，由用户查询原请求；关闭不表示撤销数据库操作。

### 可核对的拒绝结果

原实现把 409／422 都视作可重新预检查，其余都视作未知，但两类都锁住 pending，既没有安全退出，也无法区分“已移动”的 409。

`web/conversation_move_api.py` 现在返回明确的 `move_state`：

- `completed`：实际完成回执；包括“原 request_id 已完成但本次提交了不同目标”的 409。
- `not_executed`：该次精确请求被拒绝且不可再执行，包含原 request_id、目标、expected_token、会话、具体原因与 status_code。
- 404 无最终回执、存储失败、网络失败、无相应字段的错误：不能证明未执行，继续按未知处理。
- `code=conversation_moved` 的 409：表示归属已变，不能因此解锁；可以由用户查看经权限核对的现归属。

为使 `not_executed` 对迟到提交也成立，`core/workspace/conversation_moves.py` 在现有来源项目库增加 `sra_conversation_move_refusals`，主键是会话、request_id、expected_token。原移动函数已退出／回滚后，在同一个 API 移动互斥范围内核对“没有该请求的成功回执、来源实际会话仍存在”，再保存拒绝记录。记录写入失败或成功回执无法解析时返回未知，绝不承诺未执行。

旧 token 的重复／迟到提交命中该拒绝记录，不能后来执行。用户核对新材料后，同一目标、同一 request_id 可以带新的检查 token 明确提交；旧 token 的拒绝回执不能授权清除新 token 的未知请求。拒绝元数据不计入研究材料指纹，不随对话迁移；保留在原项目，保护旧地址。GET 只读，不创建表或拒绝记录。

现有成功回执、来源追踪、会话锁、两库事务、权限、CSRF、幂等、调用审计和预算逻辑保持。拒绝不消耗模型额度，不改写消息、正式材料或科研历史。冷备份继续复制完整项目库；专项验证拒绝记录在恢复副本中仍有效，无须新备份格式或另建数据系统。

## 状态与用户恢复行为

| 状态 | 页面行为 | 后续操作 |
|---|---|---|
| 尚未提交 | 目标可选；没有通过当前检查就不能确认 | 选择、预检查、明确确认 |
| 正在提交／结果未知 | 保留原编号、目标和 token；锁定目标 | 仅查询原移动结果，不自动提交、不新换编号 |
| 明确拒绝且未执行 | 显示具体原因；显示“重新选择目标” | 明确点击后才移除这份已拒绝 pending、解除选择锁并清除通过状态；重新选择后再检查、再确认 |
| 已完成 | 使用原成功回执展示实际归属 | 活跃窗口正常进入目标；已关闭窗口不自动导航，重开后核对原结果进入 |

“重新选择目标”必须匹配当前持久化请求及其精确拒绝证明；未知状态没有清空 pending、换目标或新编号的入口。一次查询 404、旧 token 的拒绝、任意 409 都不足以解锁。同一目标的版本冲突保留原编号，用户重新核对后才用新 token 提交。

旧格式 pending 缺少结果字段时按未知读取，不从历史 HTTP 状态猜测未执行。文字、待上传文件、材料关联和科研请求未确认状态仍使用现有草稿阻止逻辑；不自动发送或转移。

## 本轮文件

- `web/static/conversation-home.js`：检查与窗口身份、精确提交绑定、四种状态、重新选择、原请求查询及关闭保护。
- `web/conversation_move_api.py`：结构化结果／拒绝契约、在现有互斥范围内保存可证明的拒绝结果。
- `core/workspace/conversation_moves.py`：精确拒绝记录、旧 token 禁止执行、只读结果查询及拒绝前安全核对。
- `tests/test_conversation_move_review.py`：5 项新增接口／持久化测试。
- `tests/test_conversation_moves.py`：原拒绝验证改为严格比较除拒绝表外所有原业务表内容哈希，同时保留预检查时的整个数据库字节不变断言；不是仅比较条数或放松科研校验。
- `tests/conversation_move_review_browser.cjs`、`conversation_move_review_reproduce.cjs`：本轮浏览器验收及修改前诊断。
- `tests/conversation_move_test_server.py`：增加临时场景样本；`tests/conversation_moves_browser.cjs`：原响应丢失恢复步骤改用明确的“核对原移动结果”按钮，原数据及调用断言保留。

## 本轮验证结果（去重）

上一轮报告是 **131 项 pytest、9 条浏览器流程**，是历史结果，不并入本轮数量。

本轮 **54 项 pytest 全部通过：5 项新增 + 49 项既有回归**。另有 8 项原用例在加强业务表内容哈希断言后复跑，属于前述 54 项，不再相加。

| 类别 | 本轮结果 | 证据 |
|---|---:|---|
| 新接口契约、负回执持久化与恢复 | 5/5 | `conversation-move-review-api-final.xml` 中 test_conversation_move_review |
| 原对话移动专项 | 21/21 | 同上 test_conversation_moves |
| P1 恢复／依据与文件夹导航 | 25/25 | `conversation-move-review-p1-navigation.xml` |
| 账号、真实项目全路由及会话 | 3/3 | `conversation-move-review-isolation.xml`，覆盖表 `conversation-move-review/route-coverage.json` |
| 拒绝后原业务内容完整性加强复跑 | 8/8，已包含 | `conversation-move-review-business-unchanged.xml` |

新增 5 项分别核对：拒绝绑定及旧 token 禁止执行、无回执只读与已完成 409、拒绝存储失败不承诺未执行、完成后读取失败不误报拒绝、恢复副本中的拒绝保护与手动新目标。

本轮浏览器专项 **11 条流程通过**，实际 SQLite／FastAPI，真实响应由事件闸门控制先后顺序或在送达浏览器前丢弃，不使用睡眠猜测时序：

1. A 检查中改 B，A 迟到不能授权 B。
2. A、B 乱序返回，只用 B 的真实 token 提交，实际移动一次。
3. 检查中关闭重开，旧窗口响应不能授权、改目标或导航。
4. 明确拒绝后由用户重新选择合法目标并成功移动。
5. 拒绝后关闭／重开、刷新，仍可重新选择；390px、键盘可操作。
6. 完成响应丢失，刷新后查询原结果，无第二次提交。
7. POST 尚未到达服务端时查询实际得到 404，仍保持编号和目标；释放后实际提交成功，关闭窗口的回调不导航。
8. 同一目标版本重核对保留编号；新 token 提交尚未到达时，旧 token 拒绝回执不能解锁。
9. 另一显式请求已经移动产生的 409 不被视为未执行。
10. 拒绝响应丢失后，由精确负回执恢复，再明确改选成功。
11. B 账号不能查询 A 的移动结果。

结果与最终样本：`conversation-move-review/review-final/browser-results.json`；最终隔离根 `%TEMP%/sra-move-8453dc7c`。原有移动浏览器 **9 条流程在本轮复跑通过**，证据 `conversation-move-review/move-regression/browser-results.json`，覆盖草稿／附件、只读预检查、旧链接、前后退、复杂关联、原编号恢复与双账号。两个脚本有重叠，不宣称为 20 条互不重复的业务场景。

已实际查看的最终截图（无密码／Cookie／会话令牌）：

- [当前目标的有效预检查，桌面](conversation-move-review/review-final/current-target-desktop.png)
- [明确拒绝后的重新选择](conversation-move-review/review-final/rejected-reselect-desktop.png)
- [390px 拒绝与退出操作](conversation-move-review/review-final/rejected-reselect-390px.png)
- [404 仍不能解锁未知请求](conversation-move-review/review-final/unknown-404-kept-desktop.png)

没有新增删除断言、skip、xfail；新增测试及最终复跑无失败。早期 4 项新接口测试结果保留于 `conversation-move-review-api-initial.xml`，随后补了第 5 项“完成回执读取失败”并最终复跑；不重复累计。`asyncio_mode` 配置及 UTC API 弃用告警仍为既有告警。Python 编译、JS 语法与相关 diff 空白检查通过，不作为业务测试计数。

## 复现命令与运行边界

在仓库根 PowerShell 中创建全新测试根，使用 8098；夹具会拒绝 8001／8002／8077、非临时路径和既有根目录：

```powershell
$sraMoveReviewRoot=Join-Path $env:TEMP ('sra-move-'+[guid]::NewGuid().ToString('N').Substring(0,8))
venv/Scripts/python.exe -m tests.conversation_move_test_server --root $sraMoveReviewRoot --port 8098
```

另一个终端运行新浏览器专项；原流程复跑应使用另一个全新根，不能在已经移动过的样本上冒充首跑：

```powershell
$env:NODE_PATH='C:\Users\czh05\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:SRA_MOVE_REVIEW_OUTPUT='docs/conversation-move-review/manual-rerun'
node tests/conversation_move_review_browser.cjs
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8098/__move__/shutdown
```

```powershell
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_conversation_move_review.py tests/test_conversation_moves.py
$env:SRA_AUTH_MODE='local'; $env:SRA_WEB_SKIP_GUARD='1'; $env:SRA_DRY_RUN='true'
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_p1_recovery_attribution.py tests/test_project_organization.py
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_local_account_isolation.py::test_accounts_and_routes tests/test_local_account_isolation.py::test_real_resources_and_every_project_route tests/test_local_account_isolation.py::test_csrf_cookie_session_rotation_and_restart
```

真实模型、文献源、邮件和外部解析调用 **0**。浏览器只使用回环请求，所有科研边缘为离线替身；fixture 中的 network/model 审计是离线计数，不是付费调用。未访问／修改真实账号及项目，未启停或占用 8001／8002／8077。

本轮四个隔离根（`sra-move-504327a1`、`sra-move-1acc5430`、`sra-move-aaeb93d3`、`sra-move-8453dc7c`）均正常关闭，实例标记为 stopped，8098 已无监听。8077 仍为原 PID 28836；8001／8002 无监听，均未操作。8098 地址仅在手动启动夹具期间有效，交付后不可直接访问。测试目录保留，不额外清理，更未处理此前安全策略拒绝的目录。既有 TemporaryDirectory 回归自行回收其本次创建的目录。去重统计与停止状态见 `conversation-move-review/validation-summary.json`。

## 未验证与保留限制

- 未验证真实提供商、真实网络、服务器负载、Linux、跨进程多 worker 或硬件断电；本轮继续是单进程本地能力。
- 仍未知且没有精确最终回执的请求，保持原编号和目标，不提供强制清空、自动重发或人工猜测解锁。源／目标不可访问、认证失效或旧服务没有新结果字段，也不当成未执行证明。
- 主服务没有重启或部署；验收对象是使用当前前后端启动的隔离实例，没有以运行中旧主服务的混合版本作为通过证据。
- 原普通文字对话限制、复杂关联阻止、跨请求预算组限制及备份冷停机前提保持，不扩大支持范围。
- 拒绝记录属于保护旧提交的持久化元数据，随原项目库备份保留；本轮不提供回执清理或存储配额功能。

完成后停止，等待独立只读复核。
