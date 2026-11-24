# 同名附件与迟到创建响应：P1 修复

日期：2026-10-23。对象为当前未提交工作区。本轮只修复独立复核指出的附件竞态，不开始第三批。

## 原因与修复

原实现按文件名复用附件元信息，创建返回后按元信息 JSON 相等清除未建项目的草稿槽位。A、B 选了同名但内容不同的文件时，旧回调无法区分两次选择，可能清掉 B 的元信息；上传函数又把元信息缺失当成没有附件。原七条主路径和晚到文字响应测试没有覆盖这个组合。独立复核的原始证据仍保留在 `second-batch-independent-review/`，没有覆盖其脚本、截图和结果。

生产修改仅在 `web/static/conversation-home.js`：

- 每次选择均生成独立 `selection_id`，包括重选相同文件、相同内容、移除后再选。文件名不再决定所有权。
- 内存保存 File、选择身份及对应元信息快照；创建请求在发出前持有自己的附件快照。A 返回后，只转移和上传 A 的快照，只有原槽位仍属于同一选择时才清除；B 的新文件和文字不受其清理影响。目标槽位有另一份附件时明确报错而不覆盖。
- 上传完成也按选择身份清理，不按文件名或共享的上传请求编号清理后来选择。
- 元信息缺失但同一选择的 File 仍在内存时，从其缓存恢复；身份或文件名冲突时阻止发送、保留文字，提示重选或明确移除。不能静默发送纯文字。
- 选择身份、文件 SHA-256、上传 `request_id` 各司其职：身份保护草稿；哈希核验实际字节；请求编号保证原上传重试。重选同名文件可保留原上传的哈希／编号作为重试候选，但上传前仍须核对字节；不同内容不能冒充原上传。

没有改动服务端上传契约、引用／采纳校验、数据库或 DESIGN 上限。没有自动把笔记或附件内容变成正式研究条件。

## 刷新与恢复边界

浏览器刷新后不能恢复本地 File 对象。持久元信息保留选择身份和文件名，并明确提示重新选择。未重选前阻止创建／发送，不调用模型；重新选择后继续核对原上传，而不是静默漏传。显式移除清除该槽位的 File 与元信息。

## 本轮验证

使用真实 FastAPI、实际主应用、临时 SQLite 和隔离端口 8067；仅模型 SDK／外部来源边界使用离线替身。竞态脚本先让服务端真实完成 A 的创建，再暂缓把真实响应交给页面，不伪造创建成功。

| 验证 | 本轮结果与证据 |
| --- | --- |
| 同名不同内容、重新选择、移除再选择、刷新恢复、同内容再次选择、元信息缺失、身份冲突、文件名冲突 | 8 场景通过，见 [结果](attachment-selection-race-fix/browser-results.json) |
| A/B 实际上传内容 | 每个场景分别核对 A、B 各 1 篇资料；数据库返回的正文摘要与各自测试文件完整文本严格相等；每对仅 2 次解析／上传；B 用户消息和回复各 1 条 |
| 原七条主路径 | 全部通过，见 [结果](attachment-selection-race-fix/main-journeys/browser-results.json) |
| 上传丢响应、刷新、原请求核对、取消创建、迟到文字响应 | 通过；响应丢失用例只解析 1 次，见 [结果](attachment-selection-race-fix/recovery-regression/recovery-results.json) |
| 笔记快捷键、切页／刷新、跨项目迟到保存、保存时继续输入、失败及清空 | 通过，见 [结果](attachment-selection-race-fix/notes-regression/browser-results.json) |
| 工作台、上传可靠性、第一批保护回归 | 76 通过，见 [JUnit](attachment-selection-race-fix/regression-results.xml) |
| 产品边界／历史只读回归 | 48 通过，见 [JUnit](attachment-selection-race-fix/boundary-results.xml) |
| JavaScript 语法 | `node --check web/static/conversation-home.js` 通过 |

测试文件：新增 `tests/attachment_selection_race_browser.cjs`；三个原浏览器脚本只增加截图输出目录环境变量，保留原断言。已有 pytest 配置及 datetime 弃用警告未在本次顺带处理。

### 截图与人工核对

- [A 响应到达前](attachment-selection-race-fix/before-a-response-desktop.png) ／ [到达后 B 文字及附件仍在](attachment-selection-race-fix/after-a-response-desktop.png)。
- [刷新后窄屏重选提示](attachment-selection-race-fix/refresh-needs-file-mobile.png)。
- [B 发送后桌面](attachment-selection-race-fix/b-delivered-desktop.png) ／ [窄屏](attachment-selection-race-fix/b-delivered-mobile.png)。

实际查看了迟到响应后桌面截图、窄屏刷新提醒及窄屏交付截图；输入与移除／发送入口可见，没有横向溢出。正文与数量由实际项目资料接口核对，不仅依赖截图。此处不宣称解决其他布局或提示文案问题。

人工复核路线：A 选择文本文件并发送 → 在创建响应迟到期间新建 B → 选择同名不同内容文件并填写 B 文字 → 放行 A → 确认 B 文字、附件和刷新重选提醒仍在 → 重新选择 B 文件并发送 → 分别查看 A、B 的资料正文与数量。

## 执行边界及待确认

- 实际付费模型及公网来源请求均为 0；离线验收不代表真实模型质量通过。
- 不写入、删除、归档、迁移真实用户项目；不运行实验；不提交或推送。
- 未启停 8001。本轮自建 8067 隔离服务已使用自身关闭接口结束，临时目录随服务清理。代码修复不等于用户主服务已加载新版。
- 本轮覆盖上述 P1 复现和恢复变体，不据此承诺所有并发草稿情形均无缺陷。独立复核及用户签收仍待确认。
