# 第 2 步：从文献依据到候选研究问题

原先的 `synthesis` 讨论起点已经要求区分作者局限、比较推断与待验证设想。实际生成上下文也已经标明本轮送入的题名、摘要与片段，并把已保存报告作为阅读索引。此前缺少可验证、可保存的缺口和候选关系。用户通常只能从回答正文和一组通用引用里自己推断某条候选为何出现。

现在可以直接问“请整理研究现状与研究缺口，并给出一个候选研究问题”。回答按需整理少量发现。候选卡直接说明要弄清什么和问题从哪里来。展开后可以核对已有研究、值得核查的原因、待确认条件、可能价值、所需资料，以及各条引文的原文与本轮范围。点“查看来源”定位项目文献。没有内容依据时可以提出待验证设想，并指出需要补什么。

“确认这个研究问题”仍绑定具体候选，沿用现有应用接口、材料指纹和修订号。确认后从该问题准备方法草案请求，由用户发送。草案仍须审阅和明确采纳。没有缺口字段的历史回答、候选和正式问题继续使用原规则。

## 生成、数据与证据边界

| 内容 | 来源与检查 |
| --- | --- |
| `GuideDraft.research_gaps` | 可选，最多 3 条。每条含通俗结论、已有研究（可省略）、为什么值得核查、不确定性及最多 4 段引文。仅在相关讨论提示生成，普通问答保留原方式。首次用到时解释“研究缺口是已有研究中仍未充分回答、值得继续核查的问题”。 |
| `stated_limitation` | 显示“文献明确提到的局限”。须有实际摘要或片段原文，提示词要求原文明确提到局限。 |
| `comparison` | 显示“比较材料得到的可能不足”。至少两篇实际提供内容的论文，每篇都有匹配的引文。比较结论由模型推断。 |
| `hypothesis` | 显示“尚待验证的研究设想”。允许没有引文，此时不能填写已有研究发现。待核查事项说明需要补充的材料。只有题名或没有文献时，卡片还显示由服务端生成的范围提示 `reading_note`。 |
| `sources` | 只能用本轮 `papers` 中的 ID，`part` 指摘要或片段，`quote` 必须是该部分的连续原文。题名、项目目录、报告索引和历史讨论不能充当原文。题名来自服务端，页码只在匹配片段明确且无歧义时附上。 |
| `gap_link` | 可选，仅用于候选研究问题。用本条回答中从 0 开始的索引定位缺口，并记录联系、价值、条件和不确定性。服务端把通过校验的来源绑定进候选 `paper_ids`，确认后沿用原有正式问题论文关联。 |
| 历史说明 | 生成时的范围和原文与消息一起保存，关联缺口的快照也保存在原候选。正式问题继续使用原数据结构。在原对话能回看解释。材料或记录变化时，读取投影标记为生成时的说明，原文继续可读，过时草案仍走原有待复核与拒绝采纳机制。 |

本链路只传入有界摘要与片段。上传过、解析过或库内标为全文都不表示本轮拿到了完整全文。没有摘要与片段时，结构化局限或比较会被拒绝。服务端还拦截部分明确的越界断言。逐字匹配、数量和归属校验无法证明引文支持模型的解释，无法覆盖所有自然语言措辞。已保存报告只帮助定位来源。历史说明进入同一对话的后续上下文时附历史标记，不作为当前文献事实。用户说“没看懂”或要求暂停时，沿用原有简化规则，去掉本轮额外卡片和推进动作。

## 代码与运行

- `core/workspace/research_coach.py`、`guide.py`、`guidance_policy.py` 接入真实指导提示词，检查范围及原文，并保留解释和暂停约束。
- `core/workspace/schema.py`、`conversations.py` 保存有界可选字段与服务端快照。数据仍写入原消息和草案 JSON，无迁移或新增审批记录。校验失败沿用原失败状态和调用限制。
- `web/conversation_api.py` 在已有指导动作读取接口上附加按消息的缺口依据变化状态。界面刷新时复用原账号、会话及响应顺序保护。
- `web/static/conversation-home.js`、`ui-experience.css` 把证据与操作放在原卡片上。独立缺口显示在原回复下；已关联的缺口显示在候选旁。
- `tests/research_gap_fixture.py`、`tests/test_research_gap_guidance.py`、`tests/conversation_entry_test_server.py`、`tests/research_gap_guidance_browser.cjs` 用临时 SQLite、真实应用接口与离线提供商替身验收。

```powershell
$gapBase = Join-Path $env:TEMP ('sra-gap-pytest-' + [guid]::NewGuid().ToString('N'))
venv/Scripts/python.exe -m pytest -q --basetemp $gapBase tests/test_research_gap_guidance.py --junitxml docs/research-gap-guidance/special.xml
$gapBase = Join-Path $env:TEMP ('sra-gap-regression-' + [guid]::NewGuid().ToString('N'))
venv/Scripts/python.exe -m pytest -q --basetemp $gapBase tests/test_research_coach_delivery.py tests/test_guidance_clarity.py tests/test_p1_recovery_attribution.py tests/test_research_plan_templates.py tests/test_conversation_moves.py --junitxml docs/research-gap-guidance/regression.xml
```

浏览器使用临时目录 `$env:TEMP/sra-entry-<唯一后缀>`。用 `venv/Scripts/python.exe -m tests.conversation_entry_test_server --root <新临时目录> --research-gap` 启动回环 8113，运行 `node tests/research_gap_guidance_browser.cjs`。用夹具的 `POST /__entry__/shutdown` 关闭。第 1 步两套浏览器回归分别使用新临时根，不加 `--research-gap`，执行原 `conversation_entry_browser.cjs` 和 `conversation_step_draft_browser.cjs`。

## 证据与待验收

- 专项 17 项、既有 Python 回归 107 项通过。另将旧格式用例改为从测试库物理移除新字段后复跑通过，同一用例只计一次。 [逐项结果](research-gap-guidance/test-cases.md) · [专项 JUnit](research-gap-guidance/special.xml) · [回归 JUnit](research-gap-guidance/regression.xml) · [旧格式复跑](research-gap-guidance/legacy-recheck.xml)
- 第 2 步的 6 个浏览器场景通过。第 1 步的入口 10 个场景和起点恢复 8 个场景也通过。覆盖推荐发送、文字与附件保留、设置失败中止、历史和迟到响应。第 2 步浏览器有 4 次离线生成。 [第 2 步场景](research-gap-guidance/browser-results.json) · [入口回归](research-gap-guidance/step1-entry-browser.json) · [起点回归](research-gap-guidance/step1-draft-browser.json) · [实际离线提供商输入](research-gap-guidance/request-evidence.json)
- 已实际查看桌面和 390px 截图，检查长中文换行、展开、来源按钮与候选确认入口。确认 8113 已关闭。 [桌面](research-gap-guidance/comparison-desktop.png) · [390px 候选](research-gap-guidance/candidate-390px.png) · [390px 展开证据](research-gap-guidance/comparison-390px.png) · [390px 历史提示](research-gap-guidance/historical-390px.png)

离线替身验证真实生成接口、证据校验、保存、刷新、确认和展示。真实模型对局限的归纳、跨文献比较的准确性、中文解释是否稳定易懂，均未验收。后续真实体验可从少量具有可核对摘要及正文片段的熟悉课题开始，分别检查能否复述候选从哪里来、实际读到了什么、缺哪些依据。真实模型、文献源和邮件调用均为零。
