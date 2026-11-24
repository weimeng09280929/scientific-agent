# 第 3 步：按问题阅读论文

在“查看研究进展 → 文献与报告”勾选论文，再直接问方法、实验、局限或具体术语。系统按本轮问题选取可读内容。用户明确只看一篇时，使用已选的一篇或唯一匹配的题名、DOI。多选而无法唯一识别时，请用户先核对选择。回答里的“本轮论文依据”可展开查看当时提供的范围、已校验引文和送入的片段。点“查看来源”在现有资料面板定位该论文下的引文快照。已知 PDF 物理页码会一并显示，面板不会假装跳到了 PDF 原页。

## 链路核对

| 原环节 | 实际缺口 | 本轮处理 |
| --- | --- | --- |
| 上传 → PDF 文字提取 | 已有 pypdf 回退代码，上传在 MinerU 不可用时没有调用。扫描件可能根本没有文字层。 | 复用本地回退入口提取文字层。已有配置的 MinerU 分支保持原用途。全空、提取失败和超限走原上传拒绝及清理。部分页没有文字会标注。 |
| 提取 → 分块入库 | 旧 PDF 路径每节只留前 1500 字符，最多留 30 节。旧通用文本分块依赖空格，超长无空格段落可能没有被分开。 | 本地上传用有重叠的定长窗口，按页或文本段落切分，覆盖已提取的首尾。复用 SQLite 的片段 ID、顺序和页码列。 |
| 预览来源 | 正文开头或导入片段可能写入 `abstract`。旧库的该字段不能统一当作作者摘要。 | 保留兼容字段，新增元数据来源标记。上传标正文开头，手动补录标导入摘录，检索的摘要字段标来源提供的摘要。已明确标记的作者摘要、模型摘要分别展示。无法核实的历史数据标为旧文本摘录。模型摘要不作为原文传给指导模型。 |
| 论文 → 片段 | 明确选中的论文固定读取前两段，未选的通常只供摘要。方法、实验和局限常在后段。 | 先按用户选中项、唯一题名或 DOI 确定论文。其余按问题和项目主题对题名及预览排序。需要阅读论文时才在候选论文的已有片段中按问题排序。 |
| 上下文 → 证据 | 仅有逐字段截断。生成方案时的纸面上下文和服务端验证范围可能不同。 | 先完成片段筛选和证据分配，再对系统规则、完整用户请求、背景、历史、方案及序列化后的输入设总上限。验证复用实际送入的证据投影。 |
| 回答 → 来源 | 普通阅读只有论文级引用。对话重开后无法知道当时拿到了哪些原文。 | 可选引文、实际输入范围和片段随消息保存。读取时复用材料指纹，历史快照保留。资料删除后只保留快照，材料更新后区分当时原文和当前资料。 |

## 片段选择和预算

- 轻量本地词法评分，使用英文词和简单复数、中文双字片段、带引号的短语以及问题目的。方法、实验、局限和概览各有中英文线索。提取文本本身的首行像标题时可加权；片段顺序不能推成章节或页码。按分数排序，以存储顺序和片段 ID 处理并列。最相关命中之后，若紧邻窗口有否定或适用限定，可留一段作为补充。对标准化后相同的窗口去重。
- 普通请求最多 6 篇，网站显式勾选仍至多 3 篇。方案沿原正式问题的关联来源，最多 12 篇。每篇最多 3 段，每段至多 1050 字符，预览最多 1200 字符且可选相关窗口。证据正文总量最多 12000 字符。先按论文分配，再轮流补充剩余空间。无法放入的窗口整体省略，范围说明标明省略。
- 指导输入的系统规则和序列化提示合计最多 42000 个 Unicode 字符。优先缩减目录、历史与附带记录，保留原问题、必要项目背景、方案目标和已经确定的证据窗口。必要输入仍超限时明确提示缩小请求。字符预算是本地限额，没有当作供应商 tokenizer 的精确计量。沿用原回答预算 4096／6144 token、方案 12000 token，已有调用、重试和上层预算保护继续生效。
- 没有命中时不硬塞开头片段，保留真实预览类型和未命中提示。对明确限定单篇的阅读轮次，其他论文目录、历史回答和项目材料摘要不进入该轮生成上下文。题名、未核实摘录和模型摘要的区别在界面与生成规则中都有说明。

## 证据与兼容

普通阅读可返回 `reading_citations`。引文需匹配本轮提供的连续原文。正文引文同时指定 `excerpt_id`，由存储片段 ID 和切片边界组成。标题、页码和位置由服务端从同一份投影取得。重复引文去重，同一句话若出现在多个已提供片段且旧式缺口引文没有指定 ID，只保留引文，位置标为未知。未选入的句子、片段、论文、项目或账号不能通过校验。

第 2 步研究缺口也复用相同的原文匹配，继续区分作者局限、比较推断和待验证设想。多篇比较必须有至少两篇实际提供的来源。来源未核实的摘录可以作为待核对文本，不能标成作者明确指出的局限。已确认问题、方案请求、审阅和采纳沿用原入口。历史消息与旧库缺字段时正常读取，无批量重解析或数据库迁移。

修改文件主要为 `core/workspace/paper_reading.py`、`guide.py`、`conversations.py`、`schema.py`、`research_coach.py`、`guidance_policy.py`、`plans.py`、`conversation_moves.py`、`service.py`，`core/knowledge/paper_text.py`，`core/tools/mineru_parse.py`，`web/api.py`、`web/static/conversation-home.js`、`app.js`、`ui-experience.css`，以及检索入库时的来源标记和相应依赖清单。

## 本地解析验收范围

使用自建六页 PDF 走真实 FastAPI 上传、上传回执事务、pypdf 文字层提取、SQLite 片段读取和实际指导请求。方法、实验和局限分别位于第 4、5、6 页。空文字 PDF 验证拒绝入库及文件清理。上传 PDF 的本地回退上限为 400 页和 200 万个提取字符。pypdf 实测版本为 6.21.4，验收从已有捆绑运行时复制到一次性的临时目录，仅对测试进程设置 `PYTHONPATH`。系统依赖清单记录了该本地提取依赖。

MinerU 的已有入口只读核对。本轮没有安装、升级、配置或调用真实 MinerU。扫描件 OCR、复杂表格、双栏顺序、公式或图像的语义提取均未验收。页码仅在真实提取器或已有记录明确提供时显示。不存在页码时，界面只说明存储片段位置或位置未知。

## 验证命令与证据

```powershell
$readingBase = Join-Path $env:TEMP ('sra-paper-pytest-' + [guid]::NewGuid().ToString('N'))
$env:SRA_READING_EVIDENCE = (Resolve-Path docs/question-guided-paper-reading).Path
venv/Scripts/python.exe -m pytest -q --tb=short --basetemp $readingBase tests/test_question_guided_paper_reading.py --junitxml docs/question-guided-paper-reading/special.xml
$readingBase = Join-Path $env:TEMP ('sra-paper-regression-' + [guid]::NewGuid().ToString('N'))
venv/Scripts/python.exe -m pytest -q --tb=short --basetemp $readingBase tests/test_research_gap_guidance.py tests/test_research_partner.py tests/test_real_workflow_reliability.py::test_plan_input_projection_keeps_conditions_and_linked_evidence_only tests/test_research_plan_templates.py tests/test_local_resource_protection.py --junitxml docs/question-guided-paper-reading/regression.xml
```

浏览器使用新临时目录，父目录为系统临时目录且名字以 `sra-entry-` 开头。以 `venv/Scripts/python.exe -m tests.conversation_entry_test_server --root <新临时目录> --paper-reading` 启动 8113，运行 `node tests/question_guided_paper_reading_browser.cjs`。夹具的 `POST /__entry__/shutdown` 用于关停。第 1、2 步浏览器回归分别启动新的夹具根目录，第 2 步用 `--research-gap`。

| 验收项 | 结果及证据 |
| --- | --- |
| 上传、提取、按题选片段 | 同一自建 PDF 分别询问方法、实验和局限。指导模型实际收到相应的第 4、5、6 页，并返回可校验引文。空文字 PDF 失败后未留下资料记录或上传文件。[API 输入与引文回执](question-guided-paper-reading/pdf-reading-receipts.json) |
| 选择和预算 | 验证长文本分窗、重复片段、相邻否定限定、只看单篇、多篇比较的证据分配，以及对长历史和整体输入设上限。[专项 JUnit](question-guided-paper-reading/special.xml) |
| 来源和越权拒绝 | 验证六种预览状态、无正文命中、未知位置、重复引文。未送入片段、其他项目或账号、伪造片段 ID、错引文均无法通过。[专项 JUnit](question-guided-paper-reading/special.xml) |
| 页面与历史 | 5 个场景通过，覆盖真实上传、来源定位、刷新、390px、键盘、多篇对照、材料更新和迟到响应。桌面及窄屏截图已实际查看。[场景](question-guided-paper-reading/browser-results.json) · [生成器实际收到的证据](question-guided-paper-reading/actual-provider-input.json) · [消息保存的引文回执](question-guided-paper-reading/browser-citation-receipts.json) |
| 第 1 步页面回归 | 入口 10 个、设置及草稿 8 个场景通过。覆盖推荐发送、讨论设置恢复、保存失败中止发送、附件、明确确认和采纳。[入口](question-guided-paper-reading/step1-entry-browser.json) · [设置与草稿](question-guided-paper-reading/step1-draft-browser.json) |
| 第 2 步页面回归 | 6 个场景通过。涵盖仅题名、多篇比较、确认候选、原文更新、通俗解释和切换对话。[场景](question-guided-paper-reading/step2-browser.json) |
| 文献列表 | 独立浏览器脚本 1 次通过，含 6 项检查。25 篇实际记录，验证作者摘要和旧摘录标签、收放及键盘。该夹具单独使用 8099。[检查](question-guided-paper-reading/library-browser.json) |

专项最终 18 项通过。首轮专项 16 项中有 2 项失败，实际后段 PDF 和单篇／多篇测试暴露了英文复数与章节排序不足。修复后复跑并补充预算与溯源场景。首轮相关后端回归 123 项中有 14 项失败，涉及研究缺口问句未触发正文选取，以及旧测试对范围文案、作者摘要计数及固定前两段的预期。本轮证据目录保留两份首轮结果。[首轮专项](question-guided-paper-reading/initial-special.xml) · [首轮回归](question-guided-paper-reading/initial-regression.xml)

修复后相关后端回归 123 项全部通过，最终复跑耗时 238 秒。按文件分别为研究缺口 17、科研指导 31、方案输入投影 1、方案模板 37、资源保护 37。专项与这 123 项按用例分别计数，浏览器场景另计。回归仍提示已有的 `asyncio_mode` 配置和 `datetime.utcnow()` 弃用警告。[最终回归 JUnit](question-guided-paper-reading/regression.xml)

桌面样本“只看这篇论文，Cedar 方法的输入和输出是什么？”的序列化输入为 7118 字符，回答额度为 4096 token。输入包含 Cedar 一篇，已校验引文定位到第 4 页。随后比较问句实际收到 Cedar 和 Birch 两篇，Birch 的片段无可靠页码。更新资料后新请求不再收到旧引文，原回答仍显示旧快照和依据变更状态。

[桌面：在资料面板定位第 4 页引文快照](question-guided-paper-reading/source-desktop.png) · [390px：展开原文和来源链接](question-guided-paper-reading/evidence-390px.png)

测试使用隔离账号、自建 PDF、临时 SQLite 和离线生成器。真实模型、文献、邮件、外部解析调用均为 0。验收结束后确认 8113、8099 已停止监听，8001／8002／8077 未启停，未读取或修改真实项目资料。

离线固定回答用于证明实际输入、约束、存储和 UI。真实模型能否把论文解释准确，能否稳定区分原文、类比和推断，均未验收。逐字和归属校验也不能证明模型解释一定由引文支持。

## 第 3 步复核补修：本轮指定论文优先

原规则把旧勾选排在本轮题名或 DOI 前面。真实会话接口复现了三种后果：明确指定 Birch 却向生成器提供已勾选的 Cedar；用「只读《题名》」或 DOI 时没有进入单篇投影；多选不含 Birch 时拒绝本可唯一确定的 Birch。补修前四个专项断言均失败。[复现 JUnit](question-guided-paper-reading/target-before.xml)

- `core/workspace/paper_reading.py` 先按本轮指定的完整题名或 DOI 匹配当前项目可用论文。唯一匹配时只读该篇，本轮解析结果不会改写用户原勾选。同名且无法以 DOI 区分、题名和 DOI 不一致、未匹配时均给出核对提示。「只看这篇」仅在没有明确标识且勾选唯一时使用勾选项。无法辨认的自由措辞可改写成「只读《完整题名》」或「只看 DOI …」。
- `web/conversation_home_api.py` 提供附件上传前的只读核对，沿用项目访问校验。只有一份待上传附件且没有已有论文勾选时，可以用「只看这篇」指向该附件。旧勾选与待上传附件同时存在且「这篇」指代不清时，先让用户核对。`web/conversation_api.py` 的消息接口仍会重新判定目标，拒绝时返回结构化错误，在进入生成和创建请求记录前终止。包含 “Literature Review” 等检索词的单篇阅读请求也先服从本轮限制，避免误开检索任务。
- `web/static/conversation-home.js` 在无法确定目标时保留输入和本地附件。直接提交消息收到目标拒绝后，移除未提交的临时显示和请求占位，用户可以改正后再发。上传前的核对期间切换会话或更改输入、资料选择时，不继续使用旧核对结果。

浏览器验收使用离线生成器，记录了实际输入、服务端引文、页面显示及拒绝时的网络请求。四类成功输入包括旧单选覆盖、DOI 覆盖、旧多选覆盖和「这篇」沿用单选。拒绝场景覆盖多选指代不清、同名、未匹配和其他账号 DOI。带附件的同名拒绝只发起只读核对，文字和文件名仍可见。后端另验证同名且旧勾选恰好命中其中一篇、其他项目来源、DOI 前缀误匹配和冲突标识不会进入生成。[本轮浏览器回执](question-guided-paper-reading/target-browser-receipts.json) · [桌面：实际定位到 Birch](question-guided-paper-reading/target-source-desktop.png) · [390px：同名提示与保留的文字和附件](question-guided-paper-reading/target-draft-390px.png)

### pypdf 环境与复现

第 3 步补修验收时，未注入临时 `PYTHONPATH` 的项目 `venv` 找不到 `pypdf`。当时把本机已有的 pypdf 6.21.4 复制到临时目录，只给验收进程设置 `PYTHONPATH`。项目 `venv` 随后完成了正式依赖安装，见[项目 venv 安装与上传复验](#项目-venv-安装与上传复验)。

从仓库根目录执行以下命令可创建独立 Python 环境并复验。安装命令只写新建的临时环境。离线安装时可在安装命令加 `--no-index --find-links <本地 wheel 目录>`，其中应备好依赖清单和测试依赖的 wheel。

```powershell
$isolated = Join-Path $env:TEMP ('sra-reading-venv-' + [guid]::NewGuid().ToString('N'))
& .\venv\Scripts\python.exe -m venv $isolated
$python = Join-Path $isolated 'Scripts\python.exe'
& $python -m pip install -r requirements.txt pytest httpx
& $python -c "import sys, pypdf; print(sys.executable, pypdf.__version__)"
$testRoot = Join-Path $env:TEMP ('sra-reading-test-' + [guid]::NewGuid().ToString('N'))
& $python -m pytest -q --basetemp $testRoot tests/test_question_guided_paper_reading.py
```

浏览器使用上述隔离 Python，沿前文方式启动 `--paper-reading` 夹具并运行 `node tests/paper_reading_target_browser.cjs`。测试应使用 Playwright 和本机 Edge。浏览器脚本只连接回环夹具。补修同样未调用真实模型或外部解析。

本轮相关后端回归命令如下。验收使用新的临时测试目录。

```powershell
$testRoot = Join-Path $env:TEMP ('sra-reading-regression-' + [guid]::NewGuid().ToString('N'))
& $python -m pytest -q --basetemp $testRoot tests/test_research_gap_guidance.py tests/test_research_partner.py tests/test_real_workflow_reliability.py::test_plan_input_projection_keeps_conditions_and_linked_evidence_only tests/test_research_plan_templates.py tests/test_local_resource_protection.py tests/test_research_tasks.py::test_explicit_request_starts_but_normal_chat_does_not tests/test_research_tasks.py::test_ambiguous_request_clarifies_without_task
```

首轮浏览器回归发现，只读核对早于讨论设置保存，使设置保存失败时也出现一次核对 POST。调整后，已有对话先保存讨论设置，成功才发起上传前核对。该断言保持原样。

浏览器最终通过 9 个补修场景及 19 个相关回归场景，桌面和 390px 截图均已查看。[原论文阅读 5 项](question-guided-paper-reading/target-original-reading-browser.json) · [讨论设置、附件和草稿 8 项](question-guided-paper-reading/target-step1-draft-browser.json) · [研究缺口与明确确认 6 项](question-guided-paper-reading/target-step2-browser.json)

后端 35 项论文阅读专项通过，其中 18 项为原专项，17 项为本次新增。原有相关回归 125 项通过，包含独立回归集 123 项和与专项同跑的检索入口 2 项，无失败或跳过。真实模型和外部解析调用均为 0。验收结束后隔离端口 8113 已停止监听，主服务和真实项目未操作。[专项及检索入口 JUnit](question-guided-paper-reading/target-special.xml) · [相关回归 JUnit](question-guided-paper-reading/target-regression.xml)

## 项目 venv 安装与上传复验

- **项目 venv 已安装。** 按现有依赖清单执行 `.\venv\Scripts\python.exe -m pip install "pypdf>=6.21.4,<7"`，安装 **pypdf 6.21.4**。实际解释器为项目的 `venv/Scripts/python.exe`，Python 为 3.13.14。隔离模式导入落在 `venv/Lib/site-packages/pypdf/__init__.py`，Python 版本满足包声明的 `>=3.9`，`pip check` 返回 `No broken requirements found.`。安装未整体升级其他依赖，导入和上传复验均未注入临时 `PYTHONPATH`。[安装与依赖核对](question-guided-paper-reading/venv-pdf-validation/installed-package.json)
- **隔离上传链路验证通过。** 同一解释器在全新临时目录用 FastAPI 的真实上传路由上传自建六页 PDF，运行本地 pypdf 文字层提取并保存第 1–6 页正文。针对方法、实验和局限提问后，核对离线生成器实际收到的选段，以及保存的逐字引文、物理页码和片段身份。真实模型、外部解析调用均为零。[最终测试结果](question-guided-paper-reading/venv-pdf-validation/upload-chain.xml) · [模型输入与引文位置回执](question-guided-paper-reading/venv-pdf-validation/pdf-reading-receipts.json)
- **主服务运行状态待验证。** `start_research_system.bat` 和 `tools/start-local-demo.ps1` 的 8001 启动入口、`tools/start-local-accounts.ps1` 的 8077 启动入口均显式使用项目 `venv/Scripts/python.exe`。在本次核对的启动脚本中未找到 8002 的入口。核对时三个端口均未监听，未启停这些端口，因此没有在运行中的主服务进程内验证解释器或解析能力。

| 物理页码 | 验证的逐字引文 |
| --- | --- |
| 4 | The Cedar encoder takes a table and a caption as separate inputs. |
| 5 | The experiment compared Cedar against a keyword baseline. |
| 6 | The Cedar encoder was not evaluated on scanned tables. |

首轮安装受沙箱网络限制，随后按同一命令完成安装。首轮测试在 pytest 创建沙箱临时子目录时停止，尚未进入上传；以全新系统临时目录复跑后 1 项通过。[首轮测试环境记录](question-guided-paper-reading/venv-pdf-validation/sandbox-initial.xml)

从仓库根目录使用项目解释器复跑。测试不需要打开监听端口。

```powershell
[Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'Process')
$testRoot = Join-Path $env:TEMP ('sra-venv-pdf-' + [guid]::NewGuid().ToString('N'))
& .\venv\Scripts\python.exe -m pip check
& .\venv\Scripts\python.exe -m pytest -q --basetemp $testRoot tests/test_question_guided_paper_reading.py::test_actual_pdf_upload_and_topic_specific_late_pages
```
