# 可保存、离线可复用的科研教学案例（第六步 B）

> 2026-10-21 体验返工：新体验目录使用 v1.1.0；v1.0.0 与已绑定体验保持不变。四阶段流程、正常操作对应表、验收与最新主服务状态见 [教学体验返工记录](research-teaching-experience.md)。下文第 10 节是返工前的交付记录，不代表本轮部署状态。

本步只做教学内容和体验接入，复用[方案阶段上限](product-plan-boundary.md)、[多会话](project-conversations.md)、[中央对话](conversation-home.md)、[科研引导](research-guidance.md)、[检索任务](conversation-research-tasks.md)和[理工科方案模板](research-plan-templates.md)。不进入第七步整体验收。

> 教学案例，使用预先整理的资料；不是实时检索或实验结果。

## 1. 两个小案例

| 案例 | 教学目标 | 真正改变后续内容的选择 |
| --- | --- | --- |
| `fl-heterogeneity` v1.0.0：联邦学习的数据差异与公平比较 | 兴趣缩小为问题；区分论文所述与待验证问题；理解基线、预算可比性及重复性 | FedAvg/FedProx 方法比较，或 FedAvg 对数据差异的敏感性；单任务小范围，或多种划分与重复。得到四种计算研究方案 |
| `sulfide-design` v1.0.0：硫化物固态电解质的对象与验证设计 | 区分相近术语、具体化学组成、材料级与器件级证据；提出具体问题及对照需求 | 传导性能可比性，或体相与界面证据区分；单体系，或跨对象分组审查。得到四种实验研究设计讨论稿 |

不要求某条路径是唯一正确答案。“目标过宽”“还不理解”提供解释但不采纳、不进入下一研究阶段。没有科研能力评分、实测数据、创新性证明或成功保证。材料案例不含合成、装配、危险操作参数及执行规程。

## 2. 来源核验表（2026-10-21）

采用多源检索技能的出处与阅读范围原则；本环境没有学术 MCP 搜索接口，改用出版方、arXiv 与机构公开页面。仅保存自行编写的中文概述、元信息和事实位置，不打包论文 PDF、原图或付费正文。未抄录大段原文。下表每项均核对了实际页面，不把摘要概述当作全文阅读。

| 本地来源 ID | 公开来源、作者与年份 | 实际阅读范围 / 事实位置 | 案例使用的事实与限制 |
| --- | --- | --- | --- |
| fedavg | [McMahan、Moore、Ramage、Hampson、Agüera y Arcas，2017](https://proceedings.mlr.press/v54/mcmahan17a.html) | PMLR 书目、Abstract 第 2—3 段 | 本地保留数据、聚合更新与迭代模型平均；不承诺任意划分上的效果 |
| fedprox | [Li、Sahu、Zaheer、Sanjabi、Talwalkar、Smith，MLSys 2020](https://arxiv.org/abs/1812.06127v5)；DOI 10.48550/arXiv.1812.06127 | arXiv v5 书目和摘要前 3 句；初稿提交于 2018 年，包中年份注明取会议版 2020 | 区分系统异质性与统计异质性，引入 FedProx；没有搬用论文准确率作为案例结果 |
| scaffold | [Karimireddy、Kale、Mohri、Reddi、Stich、Suresh，2020](https://proceedings.mlr.press/v119/karimireddy20a.html) | PMLR 书目和 Abstract 第 2 段 | 在论文理论设定下分析客户端漂移、介绍控制变量；不泛化成所有现实条件的保证 |
| lgps | [Kamaya 等，2011，A lithium superionic conductor](https://www.nature.com/articles/nmat3066)；DOI 10.1038/nmat3066 | 出版方作者、年份、Abstract 第 2 段；未读订阅正文 | 指定 LGPS 组成的结构及室温传导报道；不把材料指标当作电池总体表现 |
| review | [Famprikis、Canepa、Dawson、Islam、Masquelier，2019](https://www.nature.com/articles/s41563-019-0431-3)；DOI 10.1038/s41563-019-0431-3 | 出版方作者、年份和 Abstract 前两句；未读订阅正文 | 传导、电化学/力学、界面稳定和接触等多方面挑战；不添加正文方法细节 |
| cell | [Kato 等，2016，High-power all-solid-state batteries using sulfide superionic conductors](https://www.nature.com/articles/nenergy201630)；DOI 10.1038/nenergy.2016.30 | 论文直开受到出版方身份跳转限制；使用可读取的[出版方 1(4) 目录简介](https://www.nature.com/nenergy/volumes/1/issues/4)、出版方作者/引用信息及 [KEK 机构 Reference](https://www2.kek.jp/en/newsroom/2016/03/23/0920/)交叉核对。未取得论文全文，未声称阅读完整摘要 | 目录介绍指定组成及其固态电池；仅用于区分材料与器件对象，不引用测量数值或声称核验实验细节 |

每份包保存完整作者列表、`verified_on`、`reading_scope`、`facts[].claim/location`、`verification_urls` 和使用范围。未确认 PDF 页码，因此不填页码。PMLR 两篇以原始出版链接作稳定标识，不编造 DOI。SoLid 只出现在术语歧义讲解中，不计作材料来源，也不提供未经核对的粒子探测结论。

以上来源支持教学概念，不证明案例问题具有创新性。两个案例的基线安排、控制条件、误差检查与研究问题是明确标记的教学建议；其科学可行性仍需导师与专业人员审阅。

## 3. 七步与分支

```text
认识兴趣与教学条件
  → 查看已保存来源（可打开概念解释，解释不前进）
  → 比较两个研究问题 ── 过宽目标 → 解释并停留
  → 明确采用问题（首次写入正式 question）
  → 选择范围条件 A / B（保存教学条件笔记）
  → 查看对应草案（question × scope，共四种）
  → 审阅、明确采纳、下载（正式 plan，止于 DESIGN）
       ├─ 返回问题 → 新问题草案 → 采用后替换主线；旧记录保留
       └─ 修改范围 → 旧方案待复核 → 新草案 → 采纳为下一版本
```

体验完成是独立的 `completed`；科研状态始终来自现有 workflow。它不表示实验完成、科学结论验证或论文完成。未采纳问题/方案时，仅有聊天草案，不冒充正式材料。

## 4. 教学包与发布规则

源码数据位于 `content/teaching-cases/{case_id}/{version}.json`，`manifest.json` 保存规范 JSON 的 SHA-256（复用材料 `fingerprint`）。字段包括案例身份、版本、目标、适合人群、教学条件、前置概念、来源与核验、七步及选项、问题、范围、每个组合的方案草案、待确认事项、导出说明。

方案不是散落在页面条件分支中的文字。`plan_for` 按包内分支绑定项目自己的问题与论文 ID，构建现有 `ResearchPlan`（stem-plan-v1 九节与方法专属字段），然后通过现有 `validate_plan` 检查并用现有公式/Markdown 展示。

已发布版本禁止原地改写。内容变更新增版本文件和对应指纹，保留旧文件与旧 manifest 条目。目录会明确列出可用版本；新体验绑定用户实际选择的版本，不猜测“最新研究”。已有体验只读取绑定版本，校验不符、包缺失或结构损坏时返回不可用，不能悄悄切换版本继续。

校验工具：

```powershell
venv/Scripts/python.exe tools/validate_teaching_cases.py
```

这是只读校验，不自动更新指纹掩盖内容变动。校验来源字段、来源数量、步骤、分支引用、每种组合的不同方案与结构。机器校验不代替学术事实审核，也不在用户体验时联网检查外部链接；外部页面今后可能失效，需维护者在新版本制作时重新核验。

## 5. 保存、原子性与接口

不新增聊天库或科研主线。开始体验复用 `ConversationStarter` 的持久请求预留、局部恢复和现有 `ConversationService`。显式创建后，在项目 SQLite 同一事务内保存来源、案例绑定、初始工作台和预置开场；创建失败可用原 request_id 完成，不能通过删除项目掩盖失败。

`kv_store` 内：

- `teaching.case.v1`：case_id、版本、内容指纹、所属会话、步骤、state_version、选择、来源到项目材料的映射、问题/方案草案及已采纳记录 ID、completed。
- `teaching.receipt:{request_id 指纹}`：选择内容签名与已提交结果，用于重放核对。
- 普通项目转入时的 `teaching.reference.v1`：只说明教学参考出处，不标记该普通项目为教学模式。

预置消息进入原 `sra_chat_messages`；兼容新增 `Message.content_source=teaching_case`，用户为 user，旧消息缺字段按原 mode 展示。预置讲解虽然使用兼容 rules 模式保存，但页面明确写“预置案例讲解”，不冒充模型或临时规则生成。聊天 seq、分页、会话隔离与原草案存储机制保持不变。

| 接口 | 说明 |
| --- | --- |
| `GET /api/teaching-cases` | 只读目录、可用性和资料版本，不建项目 |
| `POST /api/teaching-cases/start` | `{request_id,case_id,version}`；返回 project_id、conversation_id、teaching |
| `GET /api/projects/{pid}/conversations/{cid}/teaching` | 当前步骤、版本、选项、禁用理由、概念和本地来源 |
| `POST …/teaching/choices` | `{request_id,expected_version,option_id}`；只允许当前步骤的真实选项，不接受任意 next |
| 原 `POST …/messages` / `GET …/requests/{rid}` | 自由输入先保存；教学项目只返回“未覆盖、未调用模型/检索”的说明，刷新和重试不变成生成 |
| `POST …/teaching/fork` | `{request_id,title,resources?,time_budget?}`；独立普通项目，自己的条件由用户填写 |
| `GET …/teaching/export` | Markdown：教学标记、版本、明确采纳的记录、来源范围及待讨论事项 |
| 原 workspace、suggestions/apply、exports/items、草案 export | 复用正式材料、原子采纳与准确版本导出；旧接口不绕开校验 |

相同 request_id/内容重发返回已有结果；不同内容 409。旧 state_version 409。消息 ID 与选择请求 ID 不得用于不同操作。选择推进、消息写入、必要的正式记录/采纳标记在一个 SQLite 写事务内完成；事务失败全部回滚。共享材料在采纳时仍检查原材料指纹、真实引用、正文片段与归属，缺快照或过期返回 409。

修改范围保存为带教学标签的条件笔记，递增材料版本，并标记有效旧方案需复核。问题修改通过已有 supersedes 依赖传播；方案修订通过原版本链保留旧版本。重复或并发采纳仍返回原 item_id，不另建记录。只读浏览不建库、建表、修复或迁移。

## 6. 前端和离线边界

仍是 `/` 和 `/workspace` 加载的 `index.html` 主应用。新增小型 `teaching-cases.js/css` 适配现有中央对话，不另建教学站点，不改项目排序/侧栏体系。首页“案例体验”打开目录；浏览不建项目；明确“开始体验”才创建。案例绑定显示在工作台共享响应的 `teaching` 字段，能力响应为 `case_content=true, teaching_cases=true`。

顶部显示“第 N 步 / 7”、资料版本和教学标记；步骤区可滚动，消息区独立滚动，输入不盖住最后的按钮。概念及来源摘要保存在本地；点击可选的外部原文才需要网络。材料面板、公式净化、当前产物下载沿用原实现。

创建、选择及转为普通研究各用独立稳定 request_id。网络响应不明时保留原 ID，显式“核对原选择”；不能换 ID 自动重做。已完成但响应丢失时，即使当前步骤前进，也可核对旧回执。切换会话后晚到响应只提交原会话，不跳回或污染新页面。localStorage 仅保存待核对请求，正式进度在 SQLite。

未知自由问题保留输入和消息，诚实说明未覆盖，不套用预置答案。旧 run/resume、方法生成、导入/下载外部文献、人工续接与检索任务写入入口在教学项目受服务端限制；正常教学选择不经过第五步意图识别。检索任务的只读恢复也改为延迟初始化模型注册表，浏览不需要模型密钥。历史未绑定教学包的 case 模式设置仍按原兼容规则处理，不凭空绑定本案例。

转为自己的研究先展示会带入的公开来源与参考草案，用户填写主题和自己的条件。新项目仅保存公开来源和带教学来源标记的参考 note，不复制正式 question/plan/focus，不把案例设定写进用户 brief，不自动检索或调用模型。原教学项目原样保留。

## 7. 导出

“下载当前产物”目录增加真实的教学来源/已采纳记录汇总入口；方案与问题仍复用原研究记录和版本导出。草案保留未采纳标签，正式方案保留版本与待复核状态，不能把最近生成的预置方案当作已采纳产物。

教学标记、案例版本、真实来源、教学设定和未验证事项随汇总、正式记录及草案导出保留。Markdown 已做内容验收。Word/PDF 继续走原转换，复杂公式、表格排版仍可能受限，本步未重构或宣称全部修复。

## 8. 验收及已知限制

使用真实 FastAPI、临时 SQLite、已发布案例包。专项夹具禁止模型注册表、指导模型及外部 HTTP；任何调用都会使测试失败。浏览器只允许本地服务请求，未 mock 主要教学/会话/材料接口。

覆盖：两个案例八个有效分支，解释不推进研究状态、明确采纳前无正式记录、稳定创建/重发/并发、请求冲突、跨项目 ID、事务注入失败回滚、旧草案无快照、正文改变失效、改条件与问题保留旧版本、重复采纳、读取不写、缺库不补、新版本不替换绑定、注册表重建恢复、未知自由问题、独立普通研究和来源映射、下载标记及 DESIGN 边界。测试数量与最终状态记录见末尾交付记录。

真实主应用 Playwright/Edge：首页目录不创建，两个案例走到采纳/下载、刷新恢复、自由问题、材料面板、转为自己的研究；1440×900 与 390×844 无横向溢出，pageerror 为空；标准路径网络和模型初始化审计均为空。截图已人工查看：

- [案例目录（桌面）](research-teaching-cases/catalog-desktop.png)
- [计算方案审阅](research-teaching-cases/plan-computational-desktop.png)
- [材料方案审阅](research-teaching-cases/plan-materials-desktop.png)
- [窄屏教学体验](research-teaching-cases/completed-mobile.png)
- [窄屏材料面板](research-teaching-cases/materials-mobile.png)
- [窄屏转为普通研究前的参考预览](research-teaching-cases/fork-mobile.png)

复测命令：

```powershell
venv/Scripts/python.exe -m pytest tests/test_research_teaching_cases.py -q
# 独立临时目录服务，使用完关闭；不能用于生产项目
venv/Scripts/python.exe tests/research_teaching_cases_test_server.py --port 8022
# 另一个终端，已有 Playwright/Edge 时
node tests/research_teaching_cases_browser.cjs
```

未验证：真实模型自由回答质量（教学不使用）、真实训练/实验、方案创新性/正确性、完整论文内容、导师教学效果与长期学习效果、真实移动设备软键盘、多机部署、高并发压力、复杂 Word/PDF 格式。公开原文未来的可访问性不作保证。本轮没有真实用户数据写入、付费模型、实验、提交或推送。

## 9. 后续案例维护

先核验 3—5 个实际相关公开来源，记录阅读范围与事实位置；自行编写中文讲解，把教学条件与文献事实分开。复用七步结构，为至少两个问题和两种范围提供确实不同的组合方案，不填精确假设备或结果。新版本保留旧包，不批量改用户进度。

增加 JSON 与新 manifest 指纹后运行只读校验、离线分支测试和浏览器测试，再人工复核内容。当前发布内容只有计算联邦学习和材料实验设计两类；其他案例需要明确审核领域/方法契约，不能仅换名称冒充。页面读取数据包，新增同契约案例不必重写页面。

第七步可依赖：版本绑定的教学包、稳定案例目录/开始/选择/进度/参考转入接口、现有消息来源标记、真实材料映射与采纳、同一产品 DESIGN 上限。本步不开始第七步。

## 10. 本轮交付记录（2026-10-21）

| 实际执行 | 结果 |
| --- | --- |
| 教学专项、会话、方案模板、产品边界、主页、引导、任务、项目导航、下载、工作台共十组 | **274 passed，0 failed / error / skipped**；报告 `.pytest_cache/step6b-final-v2.xml` |
| 领域、可恢复归档、数学文档、流程恢复四组补充回归 | **82 passed，0 failed / error / skipped**；报告 `.pytest_cache/step6b-extra.xml` |
| 最后补充转入参考预览后，教学专项再次完整运行 | **31 passed，0 failed / error / skipped**；报告 `.pytest_cache/step6b-teaching-final.xml`；这 31 项已包含在上述 274 项中，不重复计算 |
| 三组前端状态测试 | **44 项通过**（25 + 12 + 7），无模型调用和持久项目写入 |
| 两个已发布数据包只读校验、JS 语法和修改涉及的 Python 格式检查 | 通过；不修改数据包指纹 |
| 真实主应用浏览器，最后使用临时服务 8027 | 两案例、导出、刷新、自由问题、普通研究转入、桌面及窄屏通过；pageerror、外部网络请求、模型初始化/外部操作审计均为空 |
| 故障与异步交互 | 真实服务先提交再丢弃响应，原 ID 核对不重复消息；草稿保留；键盘 Enter 可操作；A 响应延后至 B 页面后不会串话，A 的正常保存也不会被错误取消 |

综合 Python 回归共 **356 项不同用例**，随后再跑了其中 31 项教学专项。测试仍产生已有依赖/兼容警告，本轮没有通过删除断言或跳过用例消除失败。`test_resume_failure_keeps_old_report` 包含在整组任务回归中，本次通过（未只挑该用例单独复跑来掩盖整组失败）；既有偶发性仍应在后续观察，不能据此认定已根治。

早期验收确实发现并修复：只读任务列表提前初始化模型注册表、兼容项目对象可缺少 project_id 的读取假设，以及资料转入时新引用检查没有使用同一事务。对应整组随后复跑通过，早期失败报告未覆盖删除。转入时现在原子保存公开来源与参考笔记，并校验新项目材料 ID；界面提供 `reference_preview`，展示实际将带入的问题及方案参考，而不是只给泛泛承诺。

来源核验遵循实际取得的元信息、摘要和机构介绍范围；没有声称核验全文或实验结论。离线软件用例通过不等于案例学术创新性、教学效果或方案可执行性已被证明。

主服务 8001 本轮检查未运行，未启动、停止或重启，因而没有主服务已加载新代码的承诺。隔离验收实例仅使用临时目录，验收后关闭。没有对真实项目写入、执行实验、调用付费模型、提交或推送；到第六步 B 为止。
