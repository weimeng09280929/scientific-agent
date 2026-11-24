# 主工作台体验收敛

日期：2026-10-23。基于当前未提交工作区，只整理信息层级与交互，不代表真实模型质量或用户体验已经签收。

## 本轮改变

实际入口仍为 `/`、`/workspace` → `web/static/index.html` → `app.js` 的中央对话控制器。没有修改未使用的 `workspace.html/workspace.js`，没有新增前端框架或科研数据体系。

- **新研究**：首页只保留一个主标题、简短介绍、开场选项与输入；首次发送才建项目，可先选论文。无项目时隐藏项目内操作。已有草稿使用开场选项时，须明确同意追加提示，不静默替换输入。
- **项目与对话**：已有对话以会话名称为主，项目名可回到项目对话列表。新研究清除旧归属，“在本项目新建对话”保留项目；点击项目不建会话。一次性说明可关闭，之后从菜单查看。
- **侧栏**：保留上部当前项目资料；下部最近对话、项目可独立折叠、搜索、分页。默认各五条，当前项目保留可见。长名不改写，键盘聚焦可展开，触屏可通过条目菜单查看全名；重名项目增加时间和短标识，完整编号只在详情。修复当前项目不在近期列表时，展开仍被截回五条的问题。
- **统一材料区域**：移除聊天顶部的大块研究摘要，改为右侧“研究概况／文献与报告／研究方案”。概况读取正式问题、已保存条件、workflow 下一步及阶段；资料页显示原阅读范围和报告状态；方案区区分正式记录、当前会话草案和历史／其他版本。空态不再同时堆叠所有缺项。
- **定位与阅读**：引用定位对应论文；任务卡可直接打开对应报告；方案摘要打开同一审阅组件。支持扩大阅读，窄屏全屏抽屉有返回入口、焦点约束与 Esc 关闭，保留聊天位置和草稿。草案采纳后保持对应正式方案展开，不误打开其他版本。
- **菜单、下载、输入**：三点菜单只保留低频操作；外部点击、Esc、执行操作或切换上下文后关闭，不与下载同时叠加。下载继续读取真实产物目录，保留正式／草案／历史与待复核标记。设置按需展开；无有效问题时，方案请求按钮禁用并说明原因，服务端证据校验不变。
- **教学兼容**：两个案例使用相同材料分类与下载位置。完成后返回修改时，按服务端当前步骤和合法选项展示修订任务，不被历史完成标记遮住；未改案例包、来源、讲解或版本契约。

主要生产修改：`web/static/index.html`、`conversation-home.js`、`ui-experience.css`；`teaching-cases.js` 仅修正上述返回修改的状态展示。没有更改后端、模型路由、检索范围、采纳规则或 DESIGN 上限。

## 新用户的实际路线

1. 点“新研究”，直接说兴趣或先添加论文；只有明确发送并确认领域后才建立项目。刷新后本地文件须重选的提醒继续保留。
2. 收到回复后继续讨论；需要看依据时从引用／任务卡进入材料面板，关闭后回到原位置继续输入。
3. 明确采纳研究问题，再提出方案需求。打开草案审阅、讨论修改，明确采纳后才成为正式方案。
4. 顶部“下载当前产物”选择实际版本。方案可审阅不表示实验完成或结论得到验证。
5. 同一研究的另一段讨论用“在本项目新建对话”；另一个主题用“新研究”。侧栏最近对话返回准确的项目与会话。

## 截图：前后同类状态

截图使用隔离数据，历史条目数量随临时验收变化；比较的是同一入口状态，不是对用户历史数据做改写。

| 状态 | 修改前 | 修改后 |
| --- | --- | --- |
| 无当前项目／桌面 | [前](workbench-experience-convergence/before-home-desktop.png) | [后](workbench-experience-convergence/after-home-desktop.png) |
| 无当前项目／390px | [前](workbench-experience-convergence/before-home-mobile.png) | [后](workbench-experience-convergence/after-home-mobile.png) |
| 长名对话／桌面 | [前](workbench-experience-convergence/before-conversation-desktop.png) | [后](workbench-experience-convergence/after-conversation-desktop.png) |
| 材料空态／390px | [前](workbench-experience-convergence/before-materials-mobile.png) | [后](workbench-experience-convergence/after-materials-mobile.png) |

其他关键画面：[报告直接阅读](workbench-experience-convergence/report-reader-desktop.png)、[正式与历史方案](workbench-experience-convergence/current-and-history-desktop.png)、[未采纳草案及待复核方案下载](workbench-experience-convergence/downloads-draft-desktop.png)、[窄屏下载](workbench-experience-convergence/downloads-mobile.png)、[窄屏方案](workbench-experience-convergence/after-fl-heterogeneity-plan-mobile.png)、[缩放等效空间](workbench-experience-convergence/after-zoom-200-equivalent.png)。上述首页、抽屉、长阅读、版本及下载截图均实际打开查看，不只检查脚本异常。

从首次使用者角度：新研究归属和首次创建时机已在输入前说明；材料从同一个入口按类查看；下一步取 workflow，而不是把进入阶段当完成。是否真正容易理解，仍需要用户亲自体验确认。

## 本轮验证（不是历史通过数）

使用 8069 隔离 FastAPI、临时 SQLite、实际主应用及 Edge；主要业务接口真实运行，仅 SDK／来源网络边界使用离线替身。没有真实模型或公网文献请求，不评价夹具回复的科学质量。

| 验证 | 结果及证据 |
| --- | --- |
| 无项目输入、带论文开始、同项目多会话、教学转普通、报告一致、方案采纳与复核、旧链接／刷新／前后退 | 原七条路线通过，[最终结果](workbench-experience-convergence/main-journeys-final/browser-results.json) |
| 菜单外部关闭／Esc／焦点、材料页签键盘操作、长对话阅读与草稿、搜索分页、重名、当前旧项目展开、两个案例完整采纳和下载 | 通过，[结果](workbench-experience-convergence/browser-results.json)；浏览材料不调用模型，标准案例路径模型／来源调用均为 0 |
| 从任务打开精确报告与论文，方案修订、新草案、明确采纳、旧版本保留，实际下载报告／当前与历史方案 | 通过，[结果](workbench-experience-convergence/material-navigation-results.json)，3 份实际下载文件内容已核对 |
| 原八种附件竞态：同名不同内容、重选、移除再选、刷新、同内容新选择、元信息缺失／身份及名称冲突 | 通过，[最终结果](workbench-experience-convergence/attachment-final/browser-results.json)，A/B 各一份且全文与各自测试文件严格一致 |
| 上传响应丢失／刷新／核对原请求，不重复解析 | 通过，[结果](workbench-experience-convergence/upload-regression/recovery-results.json)，仅一次解析 |
| 笔记快捷键、切页／刷新、跨项目迟到保存、保存期间输入、失败／清空、创建失败保留输入 | 通过，[结果](workbench-experience-convergence/notes-regression/browser-results.json) |
| 工作台、第一批可靠性、精确产物下载与 DESIGN／历史只读边界 | 80 通过，[JUnit](workbench-experience-convergence/regression-results.xml) |
| 会话创建／恢复、项目导航、会话隔离／采纳及兼容 | 72 通过，[JUnit](workbench-experience-convergence/conversation-navigation-results.xml) |

浏览器覆盖 1440×900、390×844、720×450 的紧凑布局空间；无横向溢出、未处理脚本异常或浏览器外部请求。缩小可用空间后导航自动改抽屉，输入与发送仍可见。JavaScript 语法与相关 tracked 文件 diff 空白检查通过。既有 pytest asyncio_mode 配置和 datetime 弃用警告仍存在。

过程问题也保留：早期分页脚本未等搜索请求结束即点旧列表，已改为等待真实搜索响应，未放宽分页断言；随后确实发现当前项目钉选会截断展开列表并修复。另发现资料分类后通用文献入口未展开首篇、采纳后方案折叠、教学完成后返回修改不显示合法选项，均定位并修复后重新验收。报告卡默认折叠，脚本改为先真实展开再点操作，没有强制点击隐藏按钮。

## 限制与后续

- 这不是科学内容批次：引用分类、证据解释、教学内容质量及真实模型交付仍留待专门批次；离线通过不能替代真实端到端或用户签收。
- 实际检查了 Markdown 下载；Word/PDF 的复杂公式与排版没有重新验收。分页外旧报告提供已有任务接口的定点读取，但本轮没有另外构造超过 20 份报告的压力样例。
- 720×450 验证的是约等于 1440×900 放大 200% 后的 CSS 可用空间，不宣称验证了真实手机软键盘、所有浏览器缩放快捷键或屏幕阅读器。
- 历史菜单能查看完整名称，简短标识只辅助区分重名；不批量改名。大规模单项目产物及长回复性能未另做压力测试。
- 不修改真实项目、不删除归档或迁移数据、不运行实验、不提交推送。未启停 8001；磁盘新代码不等于主服务已加载新版。本轮自建隔离服务验收后关闭。

本轮到此停止。用户体验反馈待确认，下一批不自动开始。
