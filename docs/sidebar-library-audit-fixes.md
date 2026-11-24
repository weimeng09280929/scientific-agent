# 侧栏、文献徽标与检索审计修复

日期：2026-10-23。基于当前未提交工作区；未提交或推送代码。

## 修改与原因

1. **“在本项目新建对话”被裁切**：该按钮直接位于无横向内边距的导航容器，沿用了历史列表内部链接的窄间距。补充独立横向边距、可换行文字和最小点击高度，桌面和 390px 侧栏均完整可见；不改变新研究／新对话的归属。
2. **上传后徽标不更新**：主页暂停旧状态轮询后，工作区刷新事件只携带研究记录数量，没有论文数量；上传与响应丢失后的核对路径也没有通知徽标。现在使用工作区完整论文列表的 `paper_count`，上传确认成功时发出项目作用域 `sra:papers-changed` 并即时读取状态。已有项目隔离检查保留；状态请求增加序号，早发晚到的状态不能覆盖新快照。不把 `counts.question` 等研究记录数当论文数。
3. **审计只显示旧流程零记录**：旧 `/evidence` 原本只读历史 `evidence_log`；新对话检索保存在 `sra_research_tasks`，因此 20 篇论文与旧记录 0 条可以同时为真。接口保留 `entries/stats`，新增只读分页 `conversation_search`，默认 10 个任务、最多 30 个，支持 offset，按任务创建时间及 ID 稳定排序，校验项目／会话归属。文献页单独展示真实对话任务、来源状态、候选／纳入／排除／新保存／关联已有及阅读范围，旧流程留在折叠区；不回填、不改写旧记录。

对话任务卡增加“查看检索审计”入口。原用户消息不在当前历史分页时，已完成任务也保留在会话任务区，不因缺少前端消息锚点而消失。查看与下载均使用原任务与报告，不重新检索。

## 涉及文件

- `web/static/ui-experience.css`：导航按钮与审计卡样式。
- `web/static/app.js`、`conversation-home.js`：计数刷新、异步隔离、审计展示和原对话跳转。
- `core/workspace/read_model.py`、`web/api.py`：已有任务的只读审计投影。
- `tests/test_library_audit_refresh.py`、`tests/library_audit_browser.cjs`：本轮回归。

没有改变上传身份、附件选择身份、请求幂等、材料版本、明确采纳或 DESIGN 上限。

## 本轮验证

临时 SQLite、8069 隔离服务、真实主应用；模型／来源只替换在外部传输边界。本轮没有真实模型或公网文献调用。

- **113 项通过**：`test_library_audit_refresh.py`、`test_navigation_counts.py`、`test_second_batch_workspace.py`、`test_real_workflow_reliability.py`、`test_product_plan_boundary.py`。包括新旧审计共存、分页／跨项目隔离、GET 不写入、缺失数据库 404、上传幂等和阶段边界。
- 首次回归发现旧测试写死 `deepseek-v4.1-flash`，但现有路由已是 1017，导致安全审计在传输前正确阻止调用，无法测试原本要测的失败后重试保护。仅把测试授权模型改为读取现有任务路由，保留“实际请求一次、后续模型及来源均被阻止”的关键断言；最终上述 113 项全通过，未修改生产模型配置或放宽限额。
- **实际浏览器专项通过**：上传后、助手尚未回复前徽标已更新；检索完成后不进入文献页即可更新；迟到旧项目事件不能污染当前项目；上传丢响应后核对不重复入库；审计来源、排除原因、报告下载及原会话跳转可用。桌面 1440×900、窄屏 390×844 无横向溢出、无未处理脚本异常。
- **8 种附件竞态通过**，逐项核对 A/B 附件内容与数量；另跑笔记快捷键、刷新／切页草稿、跨项目与延迟保存响应、失败／清空保护，均通过。
- 已实际查看桌面、窄屏和原保留项目截图。未验收真实手机软键盘；没有借本轮宣称真实科研内容质量或所有旧问题已通过。

截图与机器记录：

- [上传后侧栏](sidebar-library-audit-fixes/sidebar-upload-desktop.png)
- [文献页任务审计](sidebar-library-audit-fixes/library-audit-desktop.png)
- [对话任务审计](sidebar-library-audit-fixes/conversation-audit-desktop.png)
- [窄屏文献页](sidebar-library-audit-fixes/library-audit-mobile.png)
- [窄屏侧栏](sidebar-library-audit-fixes/sidebar-mobile.png)
- [浏览器结果](sidebar-library-audit-fixes/browser-results.json)
- [附件八场景](sidebar-library-audit-fixes/attachment-regression/browser-results.json)
- [笔记保护](sidebar-library-audit-fixes/notes-regression/browser-results.json)

## 生效与用户核对

验证后短暂重启了本聊天此前自行启动的 **8002**，使用原数据目录加载新后端；**8001 未操作**。8069 隔离服务已正常关闭。

对保留体验项目仅做 GET 及浏览器查看，没有 POST、PATCH、删除或归档：仍有 20 篇论文、1 个已完成检索任务、6 条来源调用记录，方案可审阅。新审计读取原快照：累计有效返回 57 条（含重复）、去重 45 条、候选 20、纳入 20、排除 0、新入库 20、上限外未处理 25；摘要 20，全文 0。旧 evidence_log 仍为 0，没有伪造补记录。

[原项目只读核对截图](sidebar-library-audit-fixes/retained-project-audit.png)

用户可刷新 8002 页面 → 查看左侧“在本项目新建对话” → 打开“文献与资料”的任务审计，或展开对话中的检索任务并点“查看检索审计”。这次没有重新生成方案或重跑检索。
