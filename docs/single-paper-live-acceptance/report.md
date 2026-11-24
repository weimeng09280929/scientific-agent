# 单篇公开论文真实网页验收

**单篇论文基础科研流程可用，复杂精读仍有限制。**

验收日期为 2026-11-12（北京时间）。在隔离账号版 `127.0.0.1:8125` 使用 TokenDance 的 `deepseek-v4.1-flash-1017`。冻结案例为 [SimCSE](https://aclanthology.org/2021.emnlp-main.552/) 单篇论文和英文句对相似度的小规模比较设计。研究背景明确标为隔离测试，设定普通 CPU、Python 和一周设计期，数据与模型权重可用性待核实。使用稳定阅读和现有计算研究方案入口，实验阅读开关关闭。见 [冻结案例](case.json) 和 [代码与材料指纹](freeze.json)。

## 已核对的网页步骤

1. 通过「新研究」提交兴趣和条件，创建隔离项目与会话。通过编辑项目背景对话框保存四项背景，读回与输入一致，背景修订号为 1。[背景截图](resume-01-background.png)
2. 通过网页上传已有公开 PDF 的原始字节，成功生成 55 个正文片段。通过论文库下载 PDF 并打开检查，17 页，下载和输入 SHA-256 一致。[论文库截图](resume-02-library.png) · [PDF](SimCSE-downloaded-resume.pdf) · [打开核验](pdf-open-check-resume.json)
3. 两道基础阅读题均通过。第一题准确说明通用句子表示和对比学习目标，证据位于 PDF 第 1 页。第二题准确说明同句经同一编码器两次前向、使用不同 dropout 掩码形成正对，批内其他句子充当负例，证据位于第 1 页及第 2 页图 1。分别从网页打开引用，关键引文与实际发送给模型的选段一致。回答附加了用户未要求的背景说明与待核查项。[第一题来源](resume-04-purpose-source.png) · [第二题图 1](06-method-figure1.png) · [引文核对](citation-verification.json)
4. 网页生成了一条候选问题，比较冻结的无监督 SimCSE 和词面重叠基线在同一批英文句对上与人工评分排序的一致性。候选没有声称发现新研究缺口，数据和权重可用性保留待确认。点击「确认这个研究问题」后成为正式问题。[候选回执](05-question-terminal.json) · [确认截图](07-question-confirmed.png)
5. 通过正式问题卡准备计算研究方法草案。首次生成被依据校验拦截，用户输入仍在会话中。提交一次明确的澄清请求后保存了完整草案，样本上限 100，评价使用 Spearman，冻结无监督 SimCSE 和词元 Jaccard 共用同一批句对。[首份失败](07-draft-terminal.json) · [成功草案回执](07-draft-clarified-terminal.json) · [原草案下载件](original-draft.md)

6. 通过原草案的「修改草案」保存一次修订，缩至最多 60 对，低、中、高三个评分区间各计划 20 对，区间不足报告实际数量。比较在相同句对上进行，Spearman 对并列采用平均秩，恒定相似度报告为无法计算。审阅确认文献事实有原文支持，研究设想和可用条件保留各自状态。评分区间切点、采样与不确定性方案仍待确定。之后从网页明确采纳，形成第一份正式方案 v1。原 100 对讨论稿保留，修订指回原草案。[修订回执](08-revision-terminal.json) · [审阅截图](09-revision-review.png) · [方案引文核对](plan-source-verification.json)
7. 刷新后核对相同项目、背景、正式问题和方案。退出后确认已无登录会话，再使用同一隔离账号登录。读回同一批 13 条消息，含 7 条用户消息、6 条助手消息，其中 1 条用户消息保留失败状态。两个草案仍分别存在，原草案可打开，刷新和重登没有新增生成。浏览器自动化的等待曾被退出后的重新载入打断，已按退出状态继续重登。[恢复后的消息](restored-messages.json) · [重登后原草案](11-original-draft-after-relogin.png)
8. 从网页「下载当前产物」下载正式方案 Markdown 并打开检查。13,681 字节，正文与正式记录完整匹配，包含题目、方案 v1、修订来源草案、采纳状态、论文标题及第 1 页两段原文快照。文件状态为「当前正式方案，可审阅，尚未验证」。导入记录未自动带入 DOI，引用通过论文标题、项目论文编号、页码和快照定位。[正式方案](official-plan.md) · [下载截图](10-official-after-relogin.png) · [正式记录](adopted-workspace.json)

## 请求、费用与范围

共 **9 次真实模型网络尝试**。网关 9 次均成功返回，网页 8 次完成、1 次因上述草案依据校验失败。共 2 次属于中断前的项目，7 次属于完成的项目。无网络超时、无自动重试。账本按核验后的公开未折扣费率估算 **¥0.420594**，逐次发送前累计预留 **¥1.244710**，实际结算金额未核实。启动时固定窗口的时间比首次模型请求早约三分钟，重启沿用原截止时间。[账本](ledger.json) · [价格快照](pricing.json) · [网页操作记录](browser-transcript.json) · [输入与回答目录](provider/)

结果仅覆盖这篇公开 PDF 的基础阅读与研究设计。数据及权重尚未核实，实验未执行。复杂精读能力沿用既有证据边界。产品代码未改动，隔离实例关闭，数据和证据已保留。
## 首次失败与处置

- 第一次 PDF 上传返回 HTTP 503。原编号查得 `failed` 和 `FileNotFoundError`。原测试文件名使深层隔离目录中的临时写入路径达到 263 字符。改用文件内容和哈希完全相同的 `SimCSE.pdf` 后成功，原上传记录保留。一次浏览器自动化因未确认网页对话框超时，该次未发上传请求。未修改上传代码。
- 上一次操作中断后，网页执行进程和仅在其内存中的合成账号凭据均已丢失。保留第一次项目与两次模型回执，重启时沿用同一账本和截止时间，在新隔离项目复走背景、论文和第一题。浏览器自动化最初也曾使用不适用账号版的创建项目选择器，该次未发模型请求。
- 首份方法草案的模型 HTTP 返回成功，业务返回 422。模型把附录中不同 STS 评估口径的陈述和本项目的建议写进同一条文献事实。系统正确拦截且无自动重试，原请求不支持原编号重试。随后由网页提交一次澄清请求，将论文的直接陈述与拟议做法分开。没有采用程序降级回答，没有手工改写模型输出，没有改动产品代码或重跑既有测试集。

## 启动同一配置

在仓库根目录打开 PowerShell。原窗口在北京时间 **2026-11-12 19:40:18** 截止，截止前可以使用原账本的剩余额度启动同一隔离环境。在浏览器打开 [隔离账号版](http://127.0.0.1:8125)，注册单独账号后选择「新研究」。本轮实例交付后关闭。

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\venv\Scripts\python.exe -u .pytest_cache/real-user-acceptance-stage5/rounds/r12-single/run.py
```

窗口到期后，可用下列方式在相同端口查看本地账号界面和已保存数据。此入口禁用模型调用，真实生成须使用新的隔离预算窗口。

```powershell
.\venv\Scripts\python.exe -m tools.serve_local --mode accounts --port 8125 --data-root .pytest_cache/real-user-acceptance-stage5/rounds/r12-single/data --allow-registration
```

如需复核同一个隔离项目，可在本机同一 Windows 用户下从下列文件读取合成账号凭据。凭据只保存在本地隔离目录，勿粘贴到共享聊天。登录后打开项目「这是隔离测试」。

```powershell
$testAccount = Import-Clixml -LiteralPath '.pytest_cache/real-user-acceptance-stage5/rounds/r12-single/isolated-test-account.clixml'
$testAccount.UserName
$testAccount.GetNetworkCredential().Password
```