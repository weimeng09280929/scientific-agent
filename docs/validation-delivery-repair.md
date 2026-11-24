# 生成完成后校验失败的交付修复

2026-11-04。只读核对“联邦学习攻防”项目，找到同会话两条失败请求：00:34 UTC 为推荐说明校验失败；00:37 UTC 为方案用户事实归属失败，与用户粘贴日志一致。实际数据在 8001 自用入口的项目库，8077 地址只是用户当时可见的另一个页签。未修改这些请求、聊天、正式材料或采纳记录，也没有重跑它们。

原失败记录只保存错误与调用审计，没有失败模型的原始结构化输出。因此无法事后还原具体哪句 explanation、哪条 statement／quote 触发检查，不能声称已证明那句是误判。以下修复针对确认存在的通用链路问题，真实案例仍需用户下一次正常请求核验。

## 问题及处理

1. 可选的推荐说明触发有限词形规则时，原先会抛 WorkspaceError，阻止整条回答交付。现在仅移除该可选 explanation，保留回答正文及原 label/content，仍完整检查引用、操作归属、方案和用户条件。网页使用固定行为说明与实际发送文字，不把说明当执行授权；显示“回答已保存，部分推荐说明已省略”。记录 reply_explanation_omitted 审计，刷新不重复调用。没有用规则回复替换失败模型内容。
2. 原方案修复只覆盖 conditions 中已知引文的摘录／限定词格式错误。sections 中真实用户引文对应的正文扩写会直接失败。新增 UserStatementEvidenceError，将这种特定归属错误接入**原有同一个最多一次**修复分支，不另建新轮次或重置预算。修复上下文带上原引文，要求逐字保留为独立 user 事实，其他建议单独列出。

修复结果必须保留原 quote 和同义规范化后相同的 user 正文，不能删除原事实或换成 proposal 绕过；所有校验重新执行。原有无 GPU 冲突、未知用户引文、外来引用等在这之前仍直接拒绝。若修复仍错误则保持 structure_invalid、can_retry=false；直接 retry 不新增调用。修复成功仅产生未采纳草案，正式 v1 不被替换。

PLAN_PROMPT 同时强调 user 正文优先逐字使用引文，减少不必要的概括性扩写。没有把依据检查改成“引文存在就通过”，没有降低事实校验。

## 修改位置

- `core/workspace/guidance_policy.py`：可选说明省略、可见警告及审计。
- `core/workspace/plans.py`：明确用户正文归属错误类型、逐字事实提示；原检查仍拒绝不受支持的陈述。
- `core/workspace/conversations.py`：原一次修复分支兼容此错误；保留原事实检查；不覆盖已有显示警告。
- `core/workspace/guide.py`：修复信息也进入结构化上下文，实际提供商可捕获和核对。
- `web/static/conversation-home.js`：区分省略说明与拒绝操作的提示。
- `tests/test_plan_attribution_repair.py`：六项真实 API／离线提供商专项。
- `tests/test_guidance_clarity.py`：将“坏的可选说明导致整条失败”的旧期望改为安全省略，并验证原回答、发送内容、审计和无额外调用。
- `tests/guidance_clarity_server.py`、`tests/validation_delivery_browser.cjs`：临时样本、失败说明注入及网页实际验收。

## 验证

去重 55 个 pytest 用例：新增方案专项 6、原说明专项 11、第一批可靠性 18、P1 恢复／依据归属 20。最终对应用例全部通过，重跑不叠加计数。涵盖成功修复只保存草案、持续错误最多一次修复、不得降为建议绕过、虚构引文／GPU 冲突／外来引用拒绝、v1 保留、失败后盲目 retry 零调用。原调用审计与预算保护继续适用，不额外放宽累计次数。

保留失败记录：首批 `validation-delivery-repair-tests.xml` 为 54 通过、1 失败，离线提供商只读取结构化上下文，未看到原先只在 system 中的修复信息；补入结构化上下文后，`plan-attribution-repair-final.xml` 为 5 通过、1 测试错误（测试把 Suggestion.supersedes_id 错放到 plan 内读取）；修正字段层级，保留修订身份断言，`plan-attribution-repair-fixed.xml` 为该用例通过。不是将失败计为通过或删除断言。

浏览器 [结果](validation-delivery-repair/browser-results.json)：真实 FastAPI／临时 SQLite／离线提供商实际返回带违规可选说明的回复；正文保持，说明省略，警告与发送内容可见，只有 1 次离线提供商调用，刷新后不重复。桌面与 390px 截图位于同目录。固定样例只证明交付链路，不证明真实模型的修复质量。

复现：

```powershell
$env:SRA_AUTH_MODE='local'; $env:SRA_DRY_RUN='true'; $env:SRA_WEB_SKIP_GUARD='1'
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_plan_attribution_repair.py tests/test_guidance_clarity.py tests/test_first_batch_reliability.py tests/test_p1_recovery_attribution.py
$repairRoot=Join-Path $env:TEMP ('sra-clarity-'+[guid]::NewGuid().ToString('N').Substring(0,8))
venv/Scripts/python.exe -m tests.guidance_clarity_server --root $repairRoot --invalid-explanation
# 另一终端
$env:NODE_PATH='C:\Users\czh05\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
node tests/validation_delivery_browser.cjs
```

## 加载与使用

只读检查没有 processing 会话或 running／queued 检索任务后，重新加载 8001、8077；保留 8001 真实模型／来源模式，8077 原离线模式。启动不发出模型请求。临时 8099 已关闭，根目录 `%TEMP%/sra-clarity-713b4f6d` 保留。

用户刷新后可正常继续讨论；方案请求请核对当前问题和条件后从原方案入口明确提交新请求。原结构失败消息不会变为成功，不能通过盲目 retry 重执行。符合修复条件的新请求可能多发生一次模型调用，计入原审计／预算，可能产生额外费用；不能保证所有模型输出均可修好。失败原输出没有被补造为历史草案。

本轮真实模型、文献、邮件与外部解析调用均为零；仅只读查找用户所述项目与失败记录。没有提交、推送或部署，没有全系统重验。实际真实输出的误判比例及科研质量尚未测量。
