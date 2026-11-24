# 第 4 步 · 新手论文伴读与选段翻译

## 在网站上使用

1. 进入已有研究对话，打开「查看研究进展 → 文献与报告」，展开目标论文，点击「带我读这篇」。窗口先显示论文题名与四个可选目标。打开或关闭窗口都不会调用模型。
2. 选择「研究目的」「方法步骤」「实验与证据」或「局限与我的课题」。只提交选中的一个阅读目标。伴读回复和「本轮论文依据」留在当前对话。也可以回到输入框自由提问、说「没看懂」或「暂停一下」。原草稿、待上传附件和讨论设置留在原处。
3. 在资料预览、回复中的原文片段或引文快照里，选中文字后点击「处理选中的文字」，再选「翻译成中文」或「用通俗的话解释」。键盘用户可以直接使用同一段旁的「翻译这段」「解释这段」。这两个快捷操作处理显示的整段，超过上限时提示缩小范围。
4. 窗口分别显示选中的原文、来源以及「中文翻译」或「通俗解释」，必要时另列「术语说明」。可关闭窗口再通过「查看上次选段」查回最新请求。历史来源标为「历史原文快照」。选段结果保存在对话请求的局部交付记录中，不进入聊天消息列表或后续提示词，也不写入论文原文、正式笔记、研究问题、研究缺口或方案。

入口使用网站已显示的英文内容。PDF 原页划词、整篇翻译及双语排版均不在本版范围内。教学案例沿用原有预置讲解。

## 方法借鉴和实际生成规则

- 只读参考 `nature-reader` 的来源定位、段落对齐和术语处理文档。采用先锁定真实来源、按选段生成、翻译与解释分栏的原则。没有运行技能脚本、安装技能依赖或要求 Codex 环境参与网站请求。
- 只读参考 `nature-paper-card` 的方法拆解、证据与局限。把输入／步骤／输出、实验比较与证据边界、与课题的可能关联缩为四个独立目标。模型提示要求先处理本轮目标、用通俗语言解释、区分论文事实和类比，并指出仍待核对的内容。没有固定精读报告、自动问卷或连续调用。
- 伴读卡片直接使用当前项目的论文 ID，再进入第 3 步单篇阅读投影。它会覆盖旧勾选且不修改旧勾选。方法、实验与局限沿用按问题选择后段片段、阅读范围和逐字引文校验。承接最近伴读的短句「没看懂」或术语追问复用同篇论文。上一条讲解只用于识别难点，不能充当文献证据。明确暂停由本地规则确认。伴读不会提供研究候选、检索和采纳动作。
- 翻译和解释使用不同输出规则。提供商实际收到所选文本和同一原文块内两侧各至多 160 个字符供消歧。规则要求保留数字、单位、公式符号、否定、条件和不确定语气。相关性不能写成因果。不确定术语保留英文并说明可能含义。解释允许标明的类比。两种操作都只处理选段，不补造未提供的全文背景。

## 来源绑定与调用保护

浏览器把 DOM 的 UTF-16 位置转为 Unicode 码点位置，使用零基索引和左闭右开区间。服务端按完全相同的区间取原文，核对提交的选段原文和版本。跨块、空白、超过 1000 字符或切断字符时会提示重选，浏览器不提交。当前预览使用服务端提供的版本指纹，涵盖题名、来源类型与实际显示内容。已保存的正文片段、摘要或引用使用对话消息 ID、片段身份或引文索引及原文摘要。当前预览改变后需重新选择，历史快照由原消息读取，不从当前论文回填。论文已删时仍能在原对话使用保存的快照。

选段通过现有已认证的会话消息入口，使用同一资源锁、提供商注册表、调用审计、额度和冷却机制。一次显式提交至多发出一次模型网络尝试，结构错误或连接失败不自动补发。操作类型、来源、版本和位置组成稳定请求 ID。账号、项目、对话权限先于来源解析。同一请求重复点击或刷新只核对原请求。状态未知时按原 ID 查询，失败只有在原预算与状态允许时才可显式重试。试用上层预算仍生效，选段不开放绕过额度的新处理轮次。

「取消本次处理」取消结果交付。已经发出的网络请求仍可能计费，资源锁要等执行结束才释放。取消后的迟到结果不进入窗口或正式材料。切换会话或账号时，旧窗口及响应失效。「查看上次选段」的本地定位按账号与会话隔离，原文快照与结果由服务端保留。

## 修改位置

| 位置 | 改动 |
| --- | --- |
| `core/workspace/reading_session.py`、`conversation_schema.py`、`guide.py` | 伴读目标、选段来源协议、生成规则与结构校验 |
| `core/workspace/conversations.py`、`schema.py`、`conversation_moves.py` | 使用原请求执行及审计链路，隔离局部结果，保持历史来源归属 |
| `core/workspace/service.py`、`web/conversation_api.py`、`web/workspace_api.py` | 展示预览版本，复用会话请求、状态和权限，提供取消交付 |
| `core/llm/base.py`、`registry.py`、`providers/openai_provider.py` | 选段关闭隐式结构修复和传输重试，原调用默认行为保留 |
| `web/static/conversation-home.js`、`ui-experience.css` | 在论文资料和引文旁提供伴读、选段、结果及恢复入口 |
| `tests/test_guided_paper_reading.py`、`guided_paper_reading_browser.cjs`、`guided_paper_reading_fixture.py`、`conversation_entry_test_server.py` | 隔离账号、合成资料、真实网页和离线调用验收 |

## 验证与环境

第 4 步验收使用隔离账号、临时项目、合成 PDF、离线生成器或 SDK MockTransport。当时项目 `venv` 尚未安装 `pypdf`，测试进程使用了临时依赖路径。随后项目 `venv` 已安装 pypdf 6.21.4，并用该解释器复验隔离上传链路。主服务运行中的解释器和解析能力仍待验证，详见[项目 venv 安装与上传复验](question-guided-paper-reading.md#项目-venv-安装与上传复验)。

使用隔离 Python 环境，从仓库根目录执行以下命令。浏览器需要 Playwright 与本机 Edge。

```powershell
$testRoot = Join-Path $env:TEMP ('sra-guided-api-' + [guid]::NewGuid().ToString('N'))
& $python -m pytest -q --tb=short --basetemp $testRoot tests/test_guided_paper_reading.py
$fixtureRoot = Join-Path $env:TEMP ('sra-entry-guided-' + [guid]::NewGuid().ToString('N'))
& $python -m tests.conversation_entry_test_server --root $fixtureRoot --guided-reading
# 在另一终端从仓库根目录运行
node tests/guided_paper_reading_browser.cjs
# 验收后仅停止隔离夹具
Invoke-WebRequest http://127.0.0.1:8113/__entry__/shutdown -Method Post
```

相关后端回归合跑命令如下。浏览器回归分别使用新的临时夹具目录，以默认模式、`--research-gap` 和 `--paper-reading` 启动，依次执行 `tests/conversation_step_draft_browser.cjs`、`tests/research_gap_guidance_browser.cjs` 和 `tests/question_guided_paper_reading_browser.cjs`。

```powershell
$testRoot = Join-Path $env:TEMP ('sra-guided-regression-' + [guid]::NewGuid().ToString('N'))
& $python -m pytest -q --tb=short --basetemp $testRoot tests/test_guided_paper_reading.py tests/test_question_guided_paper_reading.py tests/test_research_gap_guidance.py tests/test_research_partner.py tests/test_optional_reply_isolation.py tests/test_p1_recovery_attribution.py
```

首轮接口专项 17 项通过。首轮浏览器在最后的 390px 账号切换时因测试脚本的旧侧栏定位器超时，未将这一轮计入通过。已改用现有侧栏入口并重新从临时项目验收。

最终后端合跑 128 项通过，无失败或跳过。包含本轮 19 项专项及 109 项相关回归，按用例去重。回归为选项安全 6 项、恢复与依据 20 项、按问题阅读 35 项、研究缺口 17 项和科研指导 31 项。浏览器本轮 6 个场景通过，第 1–3 步分别复跑 8、6、5 个场景并通过。原有测试配置和时间弃用警告仍出现。实际查看了桌面和 390px 截图。

[最终后端 JUnit](guided-paper-reading/backend.xml) · [第 1 步浏览器回执](guided-paper-reading/step1-browser.json) · [第 2 步浏览器回执](guided-paper-reading/step2-browser.json) · [第 3 步浏览器回执](guided-paper-reading/step3-browser.json)

真实模型、文献源、邮件及外部解析调用均为 0。验收结束后确认隔离端口 8113 已停止监听。

| 验收项 | 检查结果 |
| --- | --- |
| 1. 伴读覆盖旧勾选 | 页面勾选 Cedar 后打开 Birch 的伴读。实际生成器只收到 Birch，保存的引文也属于 Birch。 |
| 2. 方法、实验、局限和材料缺失 | 使用合成 PDF 走本地上传，检查生成器在对应目标下收到第 4、5、6 页原文。仅题名样本的页面范围明确为仅元信息，无引文。 |
| 3. 没看懂、术语与暂停 | “没看懂”和术语追问收到同篇论文及前一条讲解的有限上下文，无候选或采纳动作。暂停确认由本地规则完成，调用数不增加。 |
| 4. 选段与位置 | 选中含中文、表情、换行与公式的预览中第二个重复句。浏览器请求、服务端快照及生成器文本逐字对应。 |
| 5. 翻译与解释 | 生成器收到两种不同输出规则，分别生成翻译或解释字段，结果保留原文与范围。 |
| 6. 翻译规则确实进入生成器 | 实际提供商输入保留选段中的 `x ≥ 2`、`3 mg`、`may` 和否定表述。规则同时约束条件、语气及因果误读。 |
| 7. 无效或越权选段 | 浏览器拦截空选、跨块和超长。接口拒绝错误偏移、文本或版本不符、伪造请求 ID、跨项目、跨账号。 |
| 8. 历史与材料变化 | 更新后删除测试论文。读取历史引文和片段时仍保留原题名、内容与定位，其他对话不能使用该快照。 |
| 9. 请求状态和实际次数 | 划选和展开无调用，双击同一操作仍只有一条调用。刷新和丢失提交响应后只读取原状态。接口覆盖失败显式重试、取消不交付，以及 SDK 连接失败和无效 JSON 都只有一次网络尝试。 |
| 10. 迟到结果及隔离 | 拦住原请求状态响应后切换对话，旧响应未呈现。换账号后直接读取原请求返回 404。 |
| 11. 草稿、附件和正式材料 | 浏览器核对主输入、待上传文件和讨论设置。接口核对选段未添加聊天记录、草案或正式材料。 |
| 12. 桌面、390px 与键盘 | 键盘提交伴读目标和历史片段解释，390px 无横向溢出，关闭入口可达。 |

[真实请求、页面和恢复回执](guided-paper-reading/browser-results.json) · [生成器实际输入、选段和结构化结果](guided-paper-reading/provider-receipts.json) · [桌面伴读入口](guided-paper-reading/companion-desktop.png) · [桌面选段翻译](guided-paper-reading/translation-desktop.png) · [390px 历史原文解释](guided-paper-reading/historical-explain-390px.png)

真实模型翻译准确性、教学质量、实体触屏手势和主服务解析能力尚未验收。390px 测试使用浏览器窄视口，不能代替手机实机操作。
