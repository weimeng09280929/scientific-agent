# 方法草案结构失败：可选推荐项隔离与定向修复

2026-11-04。用户请求“逻辑线清晰”的方案时，模型两次 HTTP 成功，但两次结构解析失败。已请求在浏览器打开原项目对话，并读取当前运行服务日志，确认第一次错误为：

```text
GuideDraft
suggested_replies.0.price
extra_forbidden
```

即模型在可选推荐回复内添加了未定义的 price 字段。这不是 token 额度不足、文献不够或本次请求未发送。原注册表修复只追加“返回实例而非 schema”通用提示，没有给出具体字段，可能浪费第二次调用。第二次失败原始返回未持久化，不能断言其具体字段仍完全相同。

## 实施

1. `GuideDraft` 增加受限的入站处理：当原推荐列表在原上限 4 项内时，发现某个推荐项有未定义字段，省略**整个该推荐项**。不承认 price 等新字段，不据此计算价格或执行任何操作。保留其他合法推荐、正文及科研草案，由原 schema 和全部依据规则继续校验。列表超限、必需正文缺失、方案字段错误、顶层额外字段及操作中的不支持字段仍严格拒绝。
2. 省略计数为 Pydantic PrivateAttr，不新增模型可填写的公共字段／长期记录。交付阶段显示省略提示并记录 reply_option_schema_omitted；不会因为可选项坏字段自动再请求一次模型。
3. `LLMStructureError` 携带最多 8 项字段路径／类型。schema 失败审计记录 schema_invalid:字段:类型；不存入字段值和原始输出。JSON 错误报告行列，不把原文混进日志／下一次提示。
4. 原有最多一次结构修复改为接收这些具体字段诊断，要求完整实例，不再对所有问题只强调 schema 定义。原 token、网络尝试、费用和 stop-on-failure 保护不变；不新增调用轮次。

修改：`core/workspace/schema.py`、`guidance_policy.py`、`core/llm/base.py`、`providers/openai_provider.py`、`registry.py`；新增 `tests/test_optional_reply_isolation.py`、`tests/optional_reply_browser.cjs`；临时 fixture 支持故障注入。

## 验证

- 新增 6 个专项：5 个使用真实 OpenAI SDK／离线 HTTP transport，覆盖本次 price、方案必需字段缺失、动作 URL、顶层额外字段、超过四项；1 个实际 FastAPI／SQLite 验证回答保存、审计提示、原请求幂等和材料不变。
- 既有说明 11、第一批可靠性 18、P1 恢复与归属 20、方案归属修复 6、SDK 尝试和预算 8、stop-on-failure 1、方案投影 1。合计 **71 个去重用例（6 新增、65 既有）最终全部通过**。
- `optional-reply-isolation-tests.xml`：55 全通过。`optional-reply-isolation-boundary.xml`：15 通过、1 旧投影测试失败；该微型样本的完整原上下文只有 789 字符，新增于之前版本的回答偏好令投影后为 971，其他实际投影断言全通过。把样本的重复旧方案改成文档长度，保持原“变短”、引用范围及去重断言全部不变；`optional-reply-projection-final.xml` 该用例复跑通过。未放宽科学字段或删除断言。
- 浏览器真实临时应用返回带 price 的推荐项，正文仍保存，只有该项消失，两个合法推荐保留；提示可见；只有一次离线提供商调用；刷新无重复。见 [结果](optional-reply-isolation/browser-results.json)、[实际查看的截图](optional-reply-isolation/desktop.png)。不把固定回答当作真实方案质量证明。

```powershell
$env:SRA_AUTH_MODE='local'; $env:SRA_DRY_RUN='true'; $env:SRA_WEB_SKIP_GUARD='1'
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_optional_reply_isolation.py tests/test_guidance_clarity.py tests/test_first_batch_reliability.py tests/test_p1_recovery_attribution.py
venv/Scripts/python.exe -m pytest -q -p tests.resource_offline_guard tests/test_real_workflow_reliability.py::test_stop_policy_blocks_registry_retry_and_subsequent_model_calls tests/test_real_workflow_reliability.py::test_actual_sdk_attempts_usage_and_preflight_caps tests/test_real_workflow_reliability.py::test_plan_input_projection_keeps_conditions_and_linked_evidence_only tests/test_plan_attribution_repair.py
```

浏览器 fixture：`tests.guidance_clarity_server --root <新的TEMP/sra-clarity-目录> --extra-option-field`，使用固定隔离 8099；另终端配置原 NODE_PATH 后执行 `node tests/optional_reply_browser.cjs`。完成后调用该 fixture 的 `/__clarity__/shutdown`。产品服务没有新增此测试接口。

## 加载及边界

读取确认两入口项目中无在途处理／检索记录后，已加载 8001 与 8077 的修复。8001 保留真实模型／来源，8077 保留原离线模式，没有改变数据根或账号模式。原失败请求、消息、正式方案保留，未重放或伪造成成功。

本轮真实模型／来源调用均为零。临时 8099 已关闭，`TEMP/sra-clarity-ae5bbced` 保留。没有提交、推送或部署。

此修复能消除本次已确认的可选 price 字段故障；不能保证模型以后不出现其他结构或依据问题。新的必需字段错误仍拒绝保存并给定向诊断。原失败完整输出未保存，不能恢复成历史草案。真实模型重试质量尚未实测，后续仅由用户明确发起正常方案请求。
