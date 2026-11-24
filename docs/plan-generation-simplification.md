# 方案生成与修订链路减负

2026-11-11，隔离轮次 `r4-261111`。

**生成与修订链路通过。** 网页保存了两份独立的未采纳草案，修订关联到原草案及同一个已确认研究问题。首次修订超时，核对原请求后通过网页明确重试成功。刷新、退出重登和实际下载均通过。DPR、BPR 的科研内容准确性和完整科研主线尚未通过本轮验收。

## 原始失败与调用链

复核了 r3 的两份实际入模请求、模型可见原答、原请求终态以及下载的原草案。两次均收到网关 HTTP 200，模型正常结束，网页终态为失败，草案产出数均为零。

| r3 尝试 | 原始响应及终态 |
| --- | --- |
| 13 | 合法通用 JSON 包含完整方案。七段编号证据回填进正文后超出正文限制，业务拒绝保存。已有证据附录回归继续使用同一份原始回执，验证分离附录后原文完整。 |
| 14 | 原始 `suggested_replies[0]` 内各出现两次 `label` 和 `content`。严格解析报告 `duplicate_key`，核心方案未进入业务校验。 |

[逐项核对和哈希](plan-generation-simplification/r3-failure-verification.json) · [第 13 次入模](real-user-acceptance/numbered-evidence-20261111/raw-failures/13-input.json) · [第 13 次原答](real-user-acceptance/numbered-evidence-20261111/raw-failures/13-response.json) · [第 14 次入模](real-user-acceptance/numbered-evidence-20261111/raw-failures/14-input.json) · [第 14 次原答](real-user-acceptance/numbered-evidence-20261111/raw-failures/14-response.json) · [r3 草案下载件](real-user-acceptance/numbered-evidence-20261111/target-method-draft.md)

原链路由 `ConversationService` 核对问题、方法和修订来源，再由 `generate_guidance` 调用注册的模型。模型返回的方案与通用指导使用同一个 `GuideWireDraft` 对象。提供商适配器须完整解析原始 JSON，才能执行显示归一化、依据校验和保存。因此推荐回复的重复键可以在方案校验前使整次请求失败。

## 实现与兼容

- `core/workspace/plan_generation.py` 定义方案专用输出。顶层只有标题、九个章节、条件、所选方法的细节和待确认事项。计算、理论、实验设计各自只暴露所属方法的细节。用同一序列化方法测量计算研究 schema，通用指导为 11,660 字节，专用方案为 3,540 字节，缩减约 69.6%。
- `core/workspace/guide.py`、`plans.py` 在明确确认问题和方法后使用专用输出及方案指令。修订入模提供原草案的正文陈述、条件和方法细节，旧引用编号、证据附录、来源位置和重复版本元数据从修订输入中省去。新方案依据仍从本次实际入模范围回填。
- 服务端将已核对的研究问题、方法类型、正式修订目标及原草案编号绑定到草案。它按编号回填完整用户原话、文献与位置，并由有效证据确定参考来源。材料指纹、权限、请求归属、条件和方案完整性继续走原业务校验。建议、推断、文献依据及待确认条件沿用各自标记。
- `core/workspace/guidance_policy.py` 停止为有效方案请求补齐通用推荐回复。服务端生成审阅提示，原草案卡提供修改、下载和显式采纳入口。专用方案生成只有一次核心对象响应，没有独立的模型推荐调用。若核心原始 JSON 出现重复键或夹带契约外字段，严格拒绝。
- 核心输出校验后投影到原有 `GuideDraft` 和 `ResearchPlan`，继续使用会话保存、原有读接口、版本关联、草案卡及导出。历史草案和导出无需迁移。证据附录沿用原有渲染，同一次生成的网页和下载引用来自相同的原文快照。
- `core/llm/providers/openai_provider.py` 修正了结构化输出说明。生产默认仍用 `json_object`。隔离启动工具 `tools/serve_guided_experience.py` 支持一次显式的 `json_schema` 探测，共用原审计、计费和严格解析，失败也没有自动切换或补发。变动的测试和提供商替身见下面的回归命令。

## TokenDance 实际格式验证

使用现有 TokenDance `https://tokendance.space/gateway/v1/chat/completions`。请求模型为 `deepseek-v4.1-flash-1017`，三个可取得响应均报告 `deepseek-v4-1-flash-261017`。项目 venv 可用 `pypdf 6.21.4`，`pip check` 通过。启动时核对 34 个任务路由和公开费率。

公开目录列出模型与协议，未明确承诺这个模型在该 Chat Completions 端点的严格 schema 行为。本轮第 2 次网络尝试随真实网页方案请求提交了 `response_format.type=json_schema`、`strict=true` 及完整专用方案 schema，网关返回 HTTP 200，输出经服务端校验保存。该回执确认这次组合可以提交并得到合格结果。网关是否强制所有约束及重复键，只有一个通过样本，仍待验证。修订使用生产默认的 `json_object`。

[TokenDance 公开模型目录](https://www.tokendance.space/portal/api/models/deepseek-v4.1-flash-1017) · [公开费率快照](plan-generation-simplification/public-pricing.json) · [真实入模记录](plan-generation-simplification/model-inputs.json) · [原始可见响应](plan-generation-simplification/gateway-responses.json)

## 离线验收

最终按测试节点去重共 **165 项通过**。其中 20 项新增，包括 2 项冻结 r3 回放，其余 145 项为已有回归。首轮记录 112 项通过、7 项失败，原因是测试读取了不包含草案列表的详情接口，以及 6 个旧提供商替身仍返回旧通用对象。改用真实列表接口并更新替身输出后复跑通过。首轮回执保留。

新增测试覆盖三种合法方法、原草案与修订关联、无模型推荐内容、核心重复或缺失字段、非法问题或修订身份、错误证据和上一请求证据、未知硬件与时间及预算、重复请求与失败后草案保留，以及一次严格格式探测。既有回归覆盖有界修复、跨账号和跨项目权限、实际 PDF 证据选择、长引文附录、导出和原请求超时恢复。冻结的两份旧响应原样进入新生成入口仍失败。第 13 次的附录回放单独证明旧草案兼容与长引文的分离。

[去重统计](plan-generation-simplification/offline-counts.json) · [首轮](plan-generation-simplification/offline-initial.xml) · [119 项方案回归](plan-generation-simplification/offline-core-regression.xml) · [62 项安全与恢复回归](plan-generation-simplification/offline-safety.xml) · [账号权限](plan-generation-simplification/offline-account.xml) · [两项上下文回归](plan-generation-simplification/offline-context.xml)

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_plan_generation_contract.py tests/test_numbered_research_evidence.py tests/test_stage_b_acceptance_repairs.py tests/test_research_plan_templates.py tests/test_plan_attribution_repair.py --basetemp .pytest_cache/plan-core-repeat
.\venv\Scripts\python.exe -m pytest -q tests/test_plan_generation_contract.py tests/test_guided_acceptance_guard.py tests/test_optional_reply_isolation.py tests/test_p1_recovery_attribution.py --basetemp .pytest_cache/plan-core-safety-repeat
.\venv\Scripts\python.exe -m pytest -q tests/test_local_account_isolation.py::test_real_resources_and_every_project_route tests/test_first_batch_reliability.py::test_guidance_budgets_are_task_specific_and_context_preserves_evidence tests/test_real_workflow_reliability.py::test_plan_input_projection_keeps_conditions_and_linked_evidence_only --basetemp .pytest_cache/plan-core-boundary-repeat
```

## 真实网页结果

在隔离账号上传 DPR、BPR 公开 PDF，使用网页正式保存研究问题「在相同公开段落语料和固定查询集合上，BM25、DPR 与 BPR 的按问题计 Top-k 命中率、各自索引体积和查询延迟有何差异？」这是验收用的比较问题。正文明确标记未执行，硬件、可用时间、语料授权和预算待确认。论文为公开参考。题目不以两个论文表注的措辞差异推定指标实现不同。

| 网页操作 | 结果 |
| --- | --- |
| A · 从已确认问题生成 | 首次通过。通过准备方案请求、选择计算研究、发送。保存原草案 `e67f3e864724462a83627fd61fc8c187`。 |
| B · 审阅并具体修订 | 审阅、下载原草案后，从「修改草案」提交计时边界、固定环境和 k、重复次数、中位数与 P95，以及 BPR 表示核对要求。第一次在 120 秒读取上限超时，原请求保留，未保存新草案。 |
| C · 保存修订及关联 | 网页查到原请求失败后，明确点击「重试此消息」。相同请求号的第二次尝试成功。保存修订草案 `491abbe528ef4995846b95f2c8ea51ce`，明确指向原草案。两份草案均关联问题 `7c8e9f7999f140c2bd98e448b6bb0d86`。 |
| D · 刷新与退出重登 | 通过。两份草案的正文和结构与生成后的记录完全一致，状态均为未采纳。原研究问题仍有效，正式方案数为 0。 |
| E · 实际下载并打开 | 通过。网页展开的三条证据与保存快照逐字相同。浏览器下载 Markdown 并读取文件，核对 4,538 字符正文、三条完整引文、原草案编号、问题与未采纳状态。文件 15,679 字节，SHA-256 为 `b04aa3c7564fc260c0fcb514ead665bee1d3960a6069f8b31476adc4a2d2c35f`。 |

生成和修订各计一个首次场景，首次成功率 **1/2**。一次明确重试后两个场景均通过。实际保存、刷新、重登、打开证据和下载均来自网页操作，验证时对业务记录只读核对。首份草案、超时首次终态与修订回执分别保留。

[网页操作和终态](plan-generation-simplification/browser-transcript.json) · [逐次调用](plan-generation-simplification/attempt-index.json) · [证据与版本绑定](plan-generation-simplification/source-bindings.json) · [首次修订超时](plan-generation-simplification/revision-generation.png) · [重登后的两份草案](plan-generation-simplification/drafts-after-sign-in.png) · [修订审阅截图](plan-generation-simplification/revised-draft-review.png) · [390 像素证据视图](plan-generation-simplification/revised-draft-390px.png) · [原草案下载件](plan-generation-simplification/original-draft.md) · [修订草案下载件](plan-generation-simplification/revised-draft.md)

## 科研内容仍待核对

修订草案区分了按查询统计的命中率、独立索引体积以及两种延迟口径。查询编码、候选生成和重排纳入主计时口径，重复次数与 k 保持待定，硬件、时间、语料许可及预算继续待确认。草案没有产生实验结果，状态为未采纳。

修订请求实际提供的 BPR 正文集中在第 4 页，未选到第 3 页的完整重排方法。草案将查询和段落两侧表示列为待核查。它引用 BPR 摘要中无准确率损失的报告，仍需按表 1 的具体 k 与设置解读，尤其是 Top-1 的掉点。r3 的 DPR 表 2 口径和 BPR 方法解释问题仍待专项复核。本轮只对生成、修订和持久化链路作出结论。

## 费用与关闭

新账本 `r4-261111` 依用户授权无累计金额上限。执行端采用 24 次及 90 分钟的隔离运行保护，启用时间为北京时间 2026-11-11 11:30:51，固定截止 13:00:51。累计 **4 次**网络尝试，包括建立项目的普通讨论一次、首次生成一次、修订及明确重试各一次。无自动补发。三个已知用量回执合计输入 **28,821** token，输出 **18,369** token。

已知用量按本地保护费率估算 **¥0.204594**。四次请求发出前累计保守预留 **¥0.669310**。首次修订超时的上游用量与费用未知，未计入已知用量估算。提供商实收账单未知。旧账本及冻结证据哈希一致。

已关闭本轮浏览器和隔离实例，复核 `127.0.0.1:8125` 无连接。8001、8002、8077 未操作。源码未提交、未部署，交付待独立只读复核。

[轮次参数](plan-generation-simplification/authorization.json) · [账本](plan-generation-simplification/ledger.json) · [代码及环境指纹](plan-generation-simplification/baseline.json) · [旧账本与证据哈希](plan-generation-simplification/frozen-baseline-check.json) · [关闭回执](plan-generation-simplification/shutdown.json)
