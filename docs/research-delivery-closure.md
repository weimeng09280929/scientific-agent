# 第一步：研究现状报告与对话交付修复

日期：2026-10-25（Asia/Shanghai）。本轮仅处理发布复核中的两项 P2；没有开始账号、邮件、预算或部署工作。

## 1. 开始状态、问题与范围

- 工作目录保持 `scientificResearchAgentSys/scientificResearchAgentSys`，分支 `feat/code-update-v2`，HEAD `903d1fc0a5d543abd307d65774bbaece6a025f92`。没有切换、重置、暂存、提交或推送；所有原有修改与未跟踪文件保留。
- 已阅读 `p1-recovery-20261024.md`、`release-review-20261024.md`，未发现适用的 AGENTS.md。开始时尝试读取主服务状态，受沙箱可见性限制未将空输出当作可靠结论；随后授权只读检查确认 8001/8002 无监听。本轮未操作这两个主服务。
- 沿 `synthesis.report_markdown` → `ResearchTaskService._deliver/_delivery_text` → `research_task_api.export` → 主应用材料面板核对，确认：
  1. 正文直接截短 `paper_id[:12]`，末尾没有题名、作者、年份及 DOI/URL 对照；相同前缀可能无法分辨。
  2. 交付正文主要拼接查询、计数与覆盖说明；`cited_paper_ids` 取全部保存／关联论文，而非实际用于这条回复的来源。
  3. 直接相关的版本问题：历史下载从 `view.report_versions` 的元信息列表取 `report`，该列表并不包含报告正文；查询及状态又取当前任务，可能交付空正文或错用后来的上下文。

[修复前报告](research-delivery-closure/before-report.md) · [修复前桌面](research-delivery-closure/before-dialogue-desktop.png) · [修复前 390px](research-delivery-closure/before-dialogue-narrow.png)

修复前截图使用同一类临时离线材料，在独立进程内加载发布提交 `d92b37f` 的原报告渲染与交付方法；没有恢复或覆盖开发目录文件，不代表重跑了真实用户项目。修复后验收不替换主要业务接口。

## 2. 报告现在如何独立核对

- 新报告交付时，将该次实际综合候选的题名、作者、年份、DOI、URL、阅读范围保存为 `report.source_snapshot`，与原报告一起存入现有 `report_versions`。另存本轮实际保存／关联的目录快照，包含纳入／排除标记。
- 参考文献按该版本正文首次引用顺序编为 `[1]`、`[2]`。重复引用复用编号；按完整 ID 精确识别，不截断、不按相似题名合并，不全局替换数据库或机器 JSON。
- 原始 ID、研究要点及其 `paper_id/paper_ids` 关联保持不变。内部 ID 仅在报告末尾技术核对位置保留；缺失的作者、年份、DOI、链接明确标缺失。
- 当前综合接口只向模型提供元信息和最多 1200 字符的摘要节选，因此新来源快照不会因为文献库有全文而升级阅读范围。渲染器兼容元信息、摘要、摘要节选、正文节选、明确记录的全文及未知范围；这不表示本轮新增了全文阅读能力。
- 每个新版本另存 `render_context`：主题、年份范围、查询、来源执行记录、上限及整理失败说明。旧版本下载直接读取原完整版本条目，使用原状态与原上下文；补跑与后来修改论文元信息不会改变旧版编号、来源及导出字节。
- 状态 API 的 `report_markdown`、材料面板预览、产物目录下载、任务卡下载及指定版本下载均使用同一投影和渲染。目录下载复用原 MD 导出接口的 `catalog=true` 参数，没有新增格式、方案库或科研流水线。

接口沿用：

```text
GET /api/projects/{project_id}/research-tasks/{task_id}/export
GET /api/projects/{project_id}/research-tasks/{task_id}/export?version=1
GET /api/projects/{project_id}/research-tasks/{task_id}/export?version=1&catalog=true
```

### 历史兼容

历史报告不迁移、不补写、不重新生成。仅使用报告自身已有的来源快照或明确嵌入该报告的书目信息；任务当前候选和文献库可能来自后续补跑，不能据此猜补旧版。

缺少可信快照时，编号仍按原引用关系稳定生成，但参考条目明确显示“该版本无法可靠还原”，作者／年份／DOI 等标缺失。没有版本内查询记录时也明确说明。旧记录及其科学表述不被改写，无法核验的原整理文字标为待核对。

历史聊天不会批量重发或重写，仍可能保留原来的交付文案。本轮新生成的报告与交付采用新格式。旧流水线的其他类型文档不在本轮伪装成已有版本快照。

## 3. 对话现在先交付什么

新交付依次提供：

1. 简短完成／部分完成说明和证据边界。
2. 已校验报告中的 1—3 条有可读来源支撑的研究要点。
3. 报告已有、可解析引用支持的方法差异及一条主要局限；没有逐项依据则说明待核查，不凑研究结论。
4. 两个依据现有 workflow 选择的讨论选项，例如比较方法、讨论候选问题，或对照当前问题／方案核对依据。
5. 明确分开的“本条回复引用”和“本轮保存／关联目录”，以及完整报告、目录保存和任务详情入口。

来源部分失败在首句提示；完整查询、计数、上限与错误仍在任务详情。仅元信息时跳过内容综合，不编造方法／结论；模型输出截断等整理失败时明确交付规则目录，`content_status=synthesis_failed_catalog_only`，不套正常研究总结。既有任务生命周期 status 不被伪装为科学成功，显示标签和版本导出保留内容失败说明。

新增的消息字段为服务端生成的 `research_task_id`、`report_version`、`report_citations`；不向模型 GuideDraft 增加任意操作能力。讨论继续使用 ReplyOption；明确动作仍仅为已有 `view_references` 白名单，绑定持久消息及真实项目论文 ID，由原接口核验。问题与方案没有自动采纳。

后续同会话讨论的 history 附该条报告的版本内编号对照，避免模型只看到 `[1]`；它明确只是历史编号解释，不替代当前材料有效性和引用检查，不混入其他会话。

新报告到达且用户原本在底部时，从交付开头呈现；阅读历史时不拉到底部。未重排首页、侧栏、输入区或材料布局。

## 4. 主要文件

| 文件 | 修改职责 |
| --- | --- |
| `core/research_tasks/report_sources.py` | 版本内快照、精确引用识别、来源表、目录及有依据片段的纯展示帮助函数 |
| `core/research_tasks/synthesis.py` | 可读编号／参考来源渲染、未知引用拒绝、元信息不做内容推断 |
| `core/research_tasks/service.py` | 原子保存新版本快照；统一报告投影；复用报告交付要点与选项，区分引用与目录 |
| `web/research_task_api.py` | 原导出入口读取完整版本；同为 MD 的目录下载 |
| `core/workspace/schema.py`、`conversations.py` | 兼容消息字段与后续讨论的历史编号对照；不修改 P1 恢复与采纳规则 |
| `web/static/conversation-home.js` | 编号来源、原版本打开、报告／目录／详情入口及仅新报告的阅读定位 |
| `tests/test_research_delivery_closure.py` | 本轮专项；另补临时服务与实际主应用浏览器脚本 |

没有新增数据库表或迁移，没有修改模型路由、SDK 重试、调用上限、取消保护、材料指纹或采纳事务。旧版本 JSON 不在 GET/下载中改写。唯一既有测试契约调整是将 P1 已存在的 `sra_chat_failures` 加入会话表白名单，仍断言不创建流水线状态或实验／写作记录。

## 5. 验证结果与过程失败

全部使用临时 SQLite 与离线边界。最终命令在仓库根目录、虚拟环境运行；设置 `SRA_WEB_SKIP_GUARD=1`、`SRA_WEB_SKIP_PROJECT_SCAN=1` 仅影响测试进程，未修改 .env 或主服务。

```powershell
venv/Scripts/python.exe -m pytest tests/test_research_delivery_closure.py tests/test_research_tasks.py tests/test_real_workflow_reliability.py tests/test_p1_recovery_attribution.py tests/test_second_batch_workspace.py tests/test_product_plan_boundary.py tests/test_project_conversations.py tests/test_research_guidance.py -q --disable-warnings --basetemp=.pytest_cache/research-delivery-final-combined --junitxml=docs/research-delivery-closure/final-combined-results.xml

venv/Scripts/python.exe -m pytest tests/test_research_delivery_closure.py tests/test_p1_recovery_attribution.py -q --disable-warnings --basetemp=.pytest_cache/research-delivery-final-delta --junitxml=docs/research-delivery-closure/final-delta-results.xml
```

- [最终组合](research-delivery-closure/final-combined-results.xml)：**255 passed**。
- [最终增量](research-delivery-closure/final-delta-results.xml)：**39 passed**，包含后来补充的后续讨论编号对照与 P1 回归。
- 两组交集 **38 项**，去重共 **256 项通过**，不是 294 项。早期 178、93 等分组结果有重叠，不另行累计。
- 覆盖完整／重复／相同前缀引用、缺失字段、未知引用、三种降级状态、版本补跑、论文元信息变更、旧版读取不写入、无额外生成／检索／重复消息、会话隔离、操作绑定，以及 P1 和 DESIGN 边界。
- `test_resume_failure_keeps_old_report` 在本轮组合中通过，未复现此前偶发问题；这不证明偶发性已彻底排除。
- Python 编译与前端／浏览器脚本语法检查通过。既有 asyncio_mode 配置及 datetime 弃用警告没有隐瞒或通过跳过测试消除。

过程中的失败如实保留：

- 沙箱内临时目录权限错误、Edge 启动失败；首次沙箱 pytest 未产出终态，主动中断，未将其算通过。改用授权普通测试进程和独立临时目录。没有以此启停主服务。
- 原公共测试服务夹具对“创建项目立即建库”的旧假设导致缺失数据库删除报错。本轮新隔离夹具明确为自己的临时项目初始化资料，不改变产品延迟建库／GET 不建库行为。
- 本轮新专项最初将 start 返回的 queued 当作执行终态、并试图用只读 store 修改夹具。修正测试为读取实际终态、使用临时库的明确写入 store，保留原保护断言。
- 一项既有表白名单测试尚未包含 P1 的持久失败表；按明确 P1 契约补齐，未删除关键流水线断言。前期失败不被后续通过掩盖成“从未失败”。
- 最后新增的滚动用例最初使用“请再检索……”措辞，被现有意图规则当作讨论，没有创建预期任务；离线夹具也没有普通指导能力，因此该次脚本失败。改用明确的“请检索……”授权后，在全新临时实例完整复核通过，没有改动产品识别规则。“再检索”的意图覆盖留待专门批次，不把它写成已修复。

## 6. 实际主应用与交付样例

```powershell
venv/Scripts/python.exe tests/research_delivery_test_server.py --port 8068
node tests/research_delivery_browser.cjs
```

浏览器需仓库现有 Playwright/Edge 环境；本机通过 NODE_PATH 定位已有 Playwright，不新增前端框架。真实 FastAPI、会话／研究任务接口、SQLite、index.html 与实际主应用；模型 Registry 与来源适配边界为离线替身，不是 SDK／真实模型质量验收。外部 HTTP 在夹具中禁止。

主路径：发起任务 → 研究要点与部分失败提示 → 打开引用材料并关闭返回 → 打开原版本完整报告 → 保存目录 → 刷新无重复消息／模型调用 → 补跑得到 v2 → v1 下载字节不变 → 查看历史版本。另检查整理失败只提供目录、元信息样例及阅读历史时迟到交付不强拉滚动。

最终浏览器运行通过，记录 13 次本地模型替身逻辑调用；不代表实际 HTTP 请求。真实模型请求、公网来源请求均为 0，浏览器外部请求和未处理脚本异常均为空。主路径没有用固定回复替代真实模型质量验收；本轮只验证离线交付与引用行为。

- [浏览器记录](research-delivery-closure/browser-results.json)
- [修复后桌面](research-delivery-closure/after-dialogue-desktop.png)
- [修复后 390px](research-delivery-closure/after-dialogue-narrow.png)
- [窄屏报告与返回入口](research-delivery-closure/after-report-narrow.png)
- [历史版本入口](research-delivery-closure/after-history-desktop.png)
- [整理失败提示](research-delivery-closure/after-synthesis-failure-desktop.png)

已实际查看关键截图。正文编号和来源入口可见；390px 可滚动阅读、关闭抽屉并返回；无横向溢出、无未处理脚本异常。未验收真实手机软键盘，也不代表用户已签收体验。

可独立打开的文件：

- [正常流程离线报告 v1](research-delivery-closure/offline-report-v1.md)：明确为软件夹具，完整展示编号与来源结构。
- [相同版本资料目录](research-delivery-closure/offline-catalog-v1.md)：与正文引用分开。
- [可核对公开书目的离线样例](research-delivery-closure/offline-public-source-report.md)：FedAvg／FedProx 的已有公开题名、作者、年份、DOI／原文链接；仅提供元信息，明确不作方法或结论推断。书目信息复用既有预置案例，本轮未联网复核或重新阅读论文；没有用虚构链接填来源。

## 7. 调用、数据与复查边界

- 本轮真实模型请求 **0**，公网文献请求 **0**。不把离线模型／来源替身调用计作真实调用，不宣称真实模型质量已验收。
- 未打开真实用户项目做写入测试，未删除／归档／迁移项目，也未修改真实方案或重新发送原对话。原有未提交修改全部保留。
- 8001／8002 没有启停或加载本轮新代码。本轮自己启动的 8068 隔离进程已正常退出（退出码 0），临时目录随进程清理。结束时只读核对 8001／8002／8068 均无监听。
- 不新增或调整账号、邮件、预算、部署，不执行实验、训练或论文生成；没有提交、推送或部署。

规划验收端复查路线：启动上述临时服务 → 执行浏览器脚本 → 查看首条“研究要点”及引用 → 打开完整报告并核对末尾来源表 → 保存目录 → 补跑后按版本下载 v1/v2 → 核对 browser-results 与报告字节稳定性。真实模型及未经辅助的新手体验另行验收。

到此停止，仅交付第一步。是否进入第二步由复核决定。

## 8. 审核补修（2026-10-26）：仅处理两项 P2

本节追加保留前一轮过程记录。开始时仍为 `feat/code-update-v2` / `903d1fc0a5d543abd307d65774bbaece6a025f92`，已有修改和未跟踪文件全部保留；未发现适用的 AGENTS.md。只读检查 8001、8002 无监听，没有操作这两个主服务。

### 8.1 正文交叉引用

**复现与原因**：A 的 `representative.paper_id` 指向 A，但 `point` 写“与 paper:B 的条件不同”；旧索引只收集 A。`limitations.point` 同样未扫描，正文可能显示未解析，消息和后续讨论缺 B。旧交付仅核查主 ID 的阅读范围；此外，即使没有展示研究要点，未展示的方法差异、局限仍可能被算入消息引用。

**修复**：

- `report_sources.entry_reference_ids` 合并结构化 ID 与正文明确标记。研究要点、方法差异、局限说明共用完整 ID 解析，不截断、不按题名匹配、不查询当前论文库。
- `research_points` 与 `supported_limitations` 检查一个条目涉及的**全部**来源。任何来源仅元信息、缺失或范围未知，该条不作为已支持的对话研究内容交付；报告保留原文并标待核查，不将其升级为内容依据。
- `delivery_reference_ids` 与实际展示范围一致：最多三条有效要点、实际显示的方法差异及第一条有效局限；没有有效要点时不交付这些段落的引用。未展示的第二条局限等不计入本条回复引用。
- 正文编号、来源表、消息 `report_citations`、`cited_paper_ids`、查看来源操作及已有后续讨论 `report_reference_map` 使用同一版本对照。不存在或截断的 ID 仍由综合校验拒绝，未放宽证据及操作校验。

**版本兼容**：

- 新交付的 `source_snapshot.schema_version=2`，新增 `reference_order` 固定该版本编号；报告、快照与消息仍在原事务交付，不新增数据库或迁移。
- 旧 schema 1 或无顺序字段的报告，先保留旧索引已分配的编号，再在末尾追加本次识别出的遗漏正文引用。例如旧 A=1、C=2，遗漏的 B 追加为 3，不把 C 改为 3。只能使用原报告版本快照；缺失来源照旧明确提示。
- GET 仅生成只读展示，不写回历史报告、消息或版本。已有历史消息保留原交付内容和原引用对照；不会伪造一条“当时已完整引用”的消息。旧消息曾显示未解析的标记不会被批量修订；需要完整核对时打开其绑定版本报告。新交付及其后续讨论具备完整一致映射。
- 新版本、补跑和论文库元信息更新均不改变旧版编号与依据。补修使旧版中原来遗漏的交叉引用得到可追溯展示，因此该类旧文件相较补修前可能新增来源项；这属于读取器纠错，不声称旧文件导出字节在本次读取器升级前后完全相同。已有编号不变；补修之后的旧版导出在补跑和论文库变化后保持字节稳定。

### 8.2 “整理失败，仅交付目录”的版本状态

**复现与原因**：任务执行结束为 `completed`，但报告 `mode=rules` 且保存 `synthesis_note`。导出已说明内容失败，`read_model.reports` 却仅按生命周期显示“已完成”；材料面板跨页补读版本的分支还会直接显示原始 `status`。

**修复**：新增纯函数 `report_status`，把任务生命周期与内容状态分开，保留 `status` 和既有状态机。材料目录、下载目录、任务卡、材料面板标题、预览及 MD 导出复用同一版本状态：

| content_status | 用户说明 |
|---|---|
| synthesized | 研究内容已整理（基于所提供材料） |
| rules_catalog_only | 规则资料目录，未做内容综合 |
| metadata_catalog_only | 仅元信息目录，未做内容综合 |
| synthesis_failed_catalog_only | 资料目录已保存，模型内容整理未成功 |
| unknown | 历史报告，内容状态未记录 |

来源部分失败继续添加“部分完成（有来源失败）”，取消／失败仍保留任务原状态。新规则报告保存轻量 `catalog_kind` 区分正常规则目录与仅元信息；正常 rules 不标失败。旧规则报告缺少该字段时保守显示规则目录，不猜测曾经读过哪些内容。

历史版本优先取该版本报告的实际 `mode`，再用该版本的 `mode` 兼容字段；只读该版本的 `synthesis_note`，不能套用当前任务。兼容旧数据中“模型已配置但实际输出规则报告”的 mode 不一致。版本信息不足时显示未知，不用新版本的成功或失败补齐。没有版本条目的旧任务只使用自身已保存字段。

`read_model.reports` 使用 SQL `json_extract` 读取必要标量，不在 Python 加载所有版本正文、来源、调用日志或研究材料，不建表、不改库。原待复核标记、版本链接、分页和格式契约保留；列表返回兼容增加 `content_status/content_label`。原有后续讨论编号映射无需改造聊天数据库。

### 8.3 修改文件

- `core/research_tasks/report_sources.py`：统一交叉引用、所有依赖的阅读范围检查、实际交付引用集合、版本编号顺序。
- `core/research_tasks/synthesis.py`：报告中同一条目的全部引用与限制说明，规则目录类别标记。
- `core/research_tasks/report_status.py`：版本内容状态的唯一标量映射。
- `core/research_tasks/service.py`：交付引用、当前／历史版本内容标签、只读渲染投影。
- `core/workspace/read_model.py`：轻量、版本局部的目录状态。
- `web/static/conversation-home.js`：材料目录之外按版本补读时，也使用版本的 `status_label`。
- 新增 `tests/test_research_delivery_supplement.py`、`tests/research_delivery_supplement_browser.cjs`；扩充原临时测试服务的一种交叉引用离线响应。未修改意图识别、“再检索”规则、模型路由、调用限额、采纳或 P1 恢复实现。

### 8.4 本轮验证（独立于前轮计数）

命令（仓库根目录；PowerShell；先设置 `SRA_WEB_SKIP_GUARD=1` 与 `SRA_WEB_SKIP_PROJECT_SCAN=1`）：

```text
venv/Scripts/python.exe -m pytest tests/test_research_delivery_supplement.py tests/test_research_delivery_closure.py tests/test_research_tasks.py tests/test_real_workflow_reliability.py tests/test_p1_recovery_attribution.py tests/test_second_batch_workspace.py tests/test_product_plan_boundary.py tests/test_project_conversations.py tests/test_research_guidance.py -q --disable-warnings --basetemp=.pytest_cache/delivery-supplement-final --junitxml=docs/research-delivery-closure/supplement-combined-results.xml

venv/Scripts/python.exe -m pytest tests/test_research_delivery_supplement.py tests/test_research_delivery_closure.py tests/test_second_batch_workspace.py tests/test_p1_recovery_attribution.py -q --disable-warnings --basetemp=.pytest_cache/delivery-supplement-delta --junitxml=docs/research-delivery-closure/supplement-delta-results.xml

venv/Scripts/python.exe tests/research_delivery_test_server.py --port 8068
node tests/research_delivery_supplement_browser.cjs
node tests/research_delivery_browser.cjs
```

Node 需要本机 Playwright 的 `NODE_PATH`；两个浏览器脚本须**串行**运行（测试服务的离线响应模式是全局夹具状态），不能用于真实用户服务。新脚本产物存到独立 `supplement-20261026` 目录；原脚本复跑会更新前节相同文件名的后截图／浏览器记录，前节文字过程记录未覆盖。

- [组合 XML](research-delivery-closure/supplement-combined-results.xml)：**269 passed**，1267 warnings，318.86 秒。
- 在补充“历史配置 mode 与实际报告 mode 不一致”用例及最终优先级兼容调整后，[增量 XML](research-delivery-closure/supplement-delta-results.xml)：**65 passed**，120 warnings，76.79 秒。
- 两组交集 64 项，去重 **270 项通过**；不是 334 项，也不与前轮 256 相加。补充专项最终 14 项（首次 13 项通过后新增一项历史兼容）。没有通过删除断言、跳过或扩大超时掩盖失败。
- 覆盖要点／局限正文引用 B、重复引用／相同前缀、无效 ID 拒绝、元信息交叉依赖不入回复、未展示局限不入引用、消息与后续编号一致、补跑／论文库变更后旧版稳定，以及五类内容状态、旧失败版在新版成功后仍显示原提示、读取无写入。包含原 P1、幂等、归属、取消、版本与 DESIGN 保护。
- 实际 FastAPI、临时 SQLite、实际主应用完成：发起交叉引用任务 → 对话引用 [2] → 打开实际 B 材料 → 完整报告／来源表 → 下载 → 刷新不新增消息。另检查整理失败时任务卡、材料目录、下载菜单、预览标题和实际下载文件说明一致。
- 实际查看桌面和 390px 截图；无横向溢出、无未处理脚本异常、无浏览器外部请求，材料抽屉可返回。原浏览器全链路亦复跑通过。首个补充浏览器尝试在抽屉刚显示但资料尚未读取完时提前断言失败；改为等待实际资料标题，未改产品行为或使用强制点击绕过遮挡。
- Python 编译与主应用 JS 语法检查通过。未进行真实模型、真实来源内容质量、新手教学效果或真实手机键盘验收；未扩大到账号、邮件、预算或部署。

### 8.5 证据与复查路线

- [本轮浏览器结果](research-delivery-closure/supplement-20261026/browser-results.json)
- [交叉引用对话：桌面](research-delivery-closure/supplement-20261026/cross-dialogue-desktop.png) · [390px](research-delivery-closure/supplement-20261026/cross-dialogue-narrow.png)
- [来源表：桌面](research-delivery-closure/supplement-20261026/cross-bibliography-desktop.png) · [390px](research-delivery-closure/supplement-20261026/cross-bibliography-narrow.png)
- [失败状态：材料预览桌面](research-delivery-closure/supplement-20261026/failed-material-preview-desktop.png) · [窄屏](research-delivery-closure/supplement-20261026/failed-material-preview-narrow.png)
- [失败状态：下载目录桌面](research-delivery-closure/supplement-20261026/failed-download-menu-desktop.png) · [窄屏](research-delivery-closure/supplement-20261026/failed-download-menu-narrow.png)
- [交叉引用离线报告](research-delivery-closure/supplement-20261026/cross-report-v1.md) · [整理失败的目录报告](research-delivery-closure/supplement-20261026/failed-report-v1.md)

上述软件夹具含 `fixture.invalid` 与 `10.0000` 测试来源，**不是可用于科研的真实文献或本轮公网核验**。前节公开书目样例继续保留。复查时使用临时服务，运行新脚本，再核对报告 [1]/[2] 与消息来源；将失败版本在材料目录、预览与下载中对照即可。之后关闭该临时服务，不操作 8001／8002。

本次真实模型调用 **0**、公网文献调用 **0**；未写入真实用户项目，未提交、推送或部署。最终在全新临时实例单独复核补充浏览器路径通过，记录 6 次本地模型替身逻辑调用，不是网络尝试。自己启动的 8068 隔离服务均已正常退出（退出码 0），临时目录随进程清理；结束时只读核对 8001／8002／8068 均无监听。仅完成第一步补修，到此等待审核端复核，不开始第二步。
