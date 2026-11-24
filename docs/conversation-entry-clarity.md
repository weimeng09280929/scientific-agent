# 科研对话入口与正式进展

本轮只调整主对话工作台。讨论起点来自对话设置，正式状态来自当前项目的服务端工作流和记录。两行可以同时显示“正在讨论：研究方法”和“研究问题尚未确认”。页面读取期间显示“待核对”。标题里的讨论主题随显式选择更新，没有从聊天关键词推断正式进展。

## 入口盘点与点击行为

| 原入口 | 现在怎样操作 | 实际行为 |
| --- | --- | --- |
| 对话标题下的白色阶段按钮、顶栏状态徽章、研究材料按钮 | 标题下分别显示“正在讨论”和“正式材料”，使用“查看研究进展”打开材料面板 | 读取现有 `/workspace`、会话草案及相关资料。概况显示正式记录、待复核提示、需补充的依据和待确认事项。独立资料页面保留其原入口 |
| 科研流程区的当前动作、“讨论好了，进入…” | 在输入区“讨论设置”内选择讨论起点，适用时可另点“将讨论提纲填入输入框” | 选择本身只改当前视图。下次明确发送时沿用既有会话设置 `PATCH` 和消息 `POST`。填入提纲只编辑输入区，有文字时先询问是否追加 |
| 回复底部的两组固定分类 | 有讨论建议时，按钮直接展示简短标题、预期说明和要发送的原文 | 使用同一消息接口和请求恢复逻辑。另发建议时保留原文字、待上传文件、选中资料和未发送的方案设置。原请求尚未核对时先处理原请求 |
| 回复底部重复的设置、引用、打开方案、采纳操作 | 设置固定在输入区。引用在对应消息旁。正式方案在研究进展的方案页签，草案仍从对应回复打开 | 查看和展开使用现有数据，不发起研究请求。旧消息没有解释字段时仍显示标题及原文 |
| 同一回复里的候选问题与重复的采纳操作 | 各候选卡显示标题、状态和可展开正文。可填入讨论要求，或在该候选旁点“确认这个研究问题” | 点击确认使用该草案 ID 的 `/suggestions/{id}/apply` 与材料修订号。确认后原卡显示“已确认”，精确定位该正式问题。同一回复有多个可确认候选时，按钮保持同等次要样式 |
| 流程区和输入区重复的方案准备入口 | 从有效的已确认问题卡打开“准备方法草案请求”。没有对应聊天卡片的正式问题从材料面板准备 | 预选该问题，用户核对方法后填入可编辑请求。发送走原消息及方案条件校验，产生待审阅草案。当前选择失效时必须重新核对 |
| 方案审阅区的多组修订入口 | 从对应草案打开完整审阅，按需修改或采纳。采纳是该草案的主要按钮 | 修改先填入请求。采纳继续用原草案应用接口；历史或待复核版本保留阅读、讨论与导出，隐藏会引用失效对象的采纳、设为当前和方案修订按钮 |
| 流程区的检索按钮和消息里的检索卡 | 最新有效回复需要补文献时显示入口，否则可在材料面板的文献区域补充 | 首先打开已有范围确认窗口。回复内入口仍由原消息动作接口核对卡片身份。明确点“开始检索”才提交原 `/research-tasks` 接口，保留原费用提示、预算和任务约束 |

正式状态读取现有 `workflow.flow` 的当前问题、方案、可用性和复核状态。有多个已保存问题而未选定当前问题时，显示“已有正式问题，尚未选定当前问题”。候选卡与正式问题使用具体草案和记录 ID。生成其他候选不会改写现有问题；采纳条件、材料指纹、版本和权限继续由服务端校验。

## 修改文件

- `web/static/conversation-home.js`：状态、讨论设置、对象附近的操作、推荐回复独立提交、材料请求顺序与焦点恢复。
- `web/static/ui-experience.css`：对话状态、窄屏、卡片与紧凑说明。只在主对话页隐藏重复顶栏状态。
- `tests/conversation_entry_test_server.py`、`tests/conversation_entry_browser.cjs`：真实 FastAPI、临时 SQLite、两账号、离线提供商的浏览器验收。

## 验证

浏览器命令为 `node tests/conversation_entry_browser.cjs`。用 `venv/Scripts/python.exe -m tests.conversation_entry_test_server --root <新临时目录>` 启动夹具，目录名以 `sra-entry-` 开头，父目录为系统临时目录。夹具绑定 `127.0.0.1:8113`，用夹具的 `POST /__entry__/shutdown` 关闭。

场景与请求证据见 [browser-results.json](conversation-entry-clarity/browser-results.json)。浏览器通过真实接口写入、读取和检查候选 ID、正式记录、方案关联与版本状态。人工构造的回答只用于验证交互、上下文接入和校验，真实模型的回答质量未验收。

截图包括 [桌面对话](conversation-entry-clarity/desktop.png)、[390px 对话](conversation-entry-clarity/390px.png)、[桌面候选](conversation-entry-clarity/candidates-desktop.png)、[390px 候选](conversation-entry-clarity/candidates-390px.png) 和 [方案审阅](conversation-entry-clarity/plan-review-desktop.png)。

回归命令：

```powershell
$entryBase = Join-Path $env:TEMP ('sra-entry-pytest-' + [guid]::NewGuid().ToString('N'))
venv/Scripts/python.exe -m pytest -q --basetemp $entryBase tests/test_guidance_clarity.py tests/test_research_coach_delivery.py tests/test_p1_recovery_attribution.py tests/test_local_account_isolation.py tests/test_project_background.py tests/test_research_plan_templates.py --junitxml docs/conversation-entry-clarity/regression.xml
```

- 浏览器共 10 个去重场景通过。实际检查了 1440px 与 390px 截图，复核了候选卡层次、说明的可见性、方案审阅动作和键盘焦点返回。离线生成器收到 10 次请求。查看引用、选择讨论起点和取消检索均未启动任务。
- 第 1 步初次交付的 Python 回归共 105 个去重用例，合跑 104 通过、1 失败。首次发送幂等用例的旧夹具未配置账号认证，在账号模式下收到 401。该用例在全新进程中使用 `SRA_AUTH_MODE=local`、`SRA_DRY_RUN=true` 和独立临时数据根单独通过。合跑的认证环境问题已在下方补修中解决。 [初次合跑记录](conversation-entry-clarity/regression.xml) · [本地模式单跑](conversation-entry-clarity/regression-local-recheck.xml)
- 初次默认沙箱执行受到 Edge 进程和 pytest 临时目录权限限制。随后使用受审查的隔离启动命令重跑，最终浏览器验收退出成功。已确认本轮测试端口 8099 和 8113 均无监听。

## 范围

本轮使用离线模型和资料替身，真实外部调用与费用均为 0。没有使用授权的 10 元预算。研究缺口的结构化数据、论文详解、全文解析、真实设备触屏及真实付费提供商均不属于本次验证。旧操作记录仍可由原 API 读取。本次没有复跑仍通过旧阶段按钮或旧操作名称定位元素的历史浏览器脚本。相关主对话行为由本轮浏览器脚本验证。8001／8002／8077 继续由原方式管理。

## 第 1 步补修：恢复未发送的讨论起点

旧版 `chooseResearchStep` 只修改当前页面内存。继续编辑文字虽然会让 `saveDraft` 带上当时的设置快照，但初始化的 `setSettings` 会用会话接口里的旧值覆盖它。隔离浏览器实测，选择“研究方法”后直接刷新会回到“兴趣与条件”，期间没有提交设置或消息。 [修复前复现](conversation-entry-clarity/step-draft-before.json)

现在显式选择会立即存入账号、项目和对话范围内的本地待提交记录。该记录与文字草稿分开，编辑文字不会回写或清除讨论起点。恢复时先读服务端保存的设置，仅叠加仍待提交的明确选择。服务器确认相同选择后清除对应记录，已保存的旧草稿快照不会盖过服务器。设置的响应只核对发起时的那条记录，迟到响应不能清除后来的选择或更新已经离开的对话。

发送新消息仍使用原会话设置 `PATCH` 和消息接口。设置保存失败会停止本次提交，文字、附件和选择均保留。如果设置已保存但消息请求被拒绝，刷新会显示已保存的起点，并保留原文字及请求身份供明确重试。完整刷新后浏览器会提示重新选择本地附件，在同一页面内离开再返回则继续使用待上传文件。确认研究问题和采纳正式材料仍须明确操作。

旧回归夹具的认证模式由测试自身显式指定为 `local`，使用临时配置和私有的 API 模块实例。`monkeypatch` 在夹具结束后恢复环境、配置和模块登记，避免依赖账号测试的运行顺序。实际处理函数、产品鉴权和原断言保持原样。

- 原 105 项在同一进程全部通过，无失败、错误或跳过。额外运行共享会话夹具的 14 项，全部通过。逐条结果来自两份 JUnit 报告。 [逐项清单](conversation-entry-clarity/step-draft-regression-cases.md) · [105 项 JUnit](conversation-entry-clarity/step-draft-regression.xml) · [共享夹具 JUnit](conversation-entry-clarity/step-draft-home-fixture.xml)
- 新增 8 个隔离浏览器场景，核对直接刷新、离开返回、编辑后刷新、附件恢复和实际发送、设置拒绝、消息拒绝后的同请求重试、晚到的设置响应以及跨账号、项目和对话隔离。验证了服务器设置、实际请求正文和生成上下文中的讨论起点，并比对正式记录、背景与当前关联。桌面与 390px 截图已查看。 [场景结果](conversation-entry-clarity/step-draft-browser-results.json) · [桌面保存失败](conversation-entry-clarity/step-draft-save-failure-desktop.png) · [390px 恢复](conversation-entry-clarity/step-draft-restored-390px.png)
- 复跑 105 项使用本页上方命令的同一组文件，把 JUnit 路径改为 `step-draft-regression.xml`。共享夹具命令为 `venv/Scripts/python.exe -m pytest -q tests/test_conversation_home.py`，同样使用独立 `--basetemp`。浏览器沿用 8113 夹具，在全新临时根启动后运行 `node tests/conversation_step_draft_browser.cjs`。
- 浏览器实际提交了两次离线生成。真实外部调用为 0。隔离实例已关闭，主服务和真实资料未操作。本轮没有验收真实模型或实体触屏设备。
