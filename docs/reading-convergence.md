# 论文阅读统一候选验收

## 范围与冻结

使用 TokenDance `deepseek-v4.1-flash-1017`、独立 `r8-261111` 账本和 `127.0.0.1:8125`。先完成零收费登录、四篇开发论文复用、保留材料上传、判据冻结和受影响回归。新窗口从 2026-11-11 15:48:04.208923 UTC 到 17:18:04.208923 UTC，最多 24 次，金额无累计硬上限。旧授权、旧账本及真实数据不参与写入。候选按进程固定为 `coverage-v1`，既有业务默认保持。 [新授权](reading-convergence/authorization.json) · [第一轮冻结](reading-convergence/freeze-1.json) · [开发判据](reading-convergence/development-criteria.md) · [保留判据](reading-convergence/holdout-1-criteria.md) · [首轮题目与材料指纹](reading-convergence/round1-manifest.json)

## 根因、实现与预检

- 旧 N1 实际入模没有正文，原因是问题分类漏掉口语化的分阶段训练及其数据、标签条件，随即落到通用词检索。 `core/workspace/reading_coverage.py` 识别问到的顺序、输入、标签、表示、输出、正负例和动机，使用有限的中英检索线索。 `core/workspace/paper_reading.py` 按覆盖增益选择正文，对明确未覆盖要点进行一次本地补读。复杂方法最多五段，仍受原总输入预算约束，保存初选与补入位置。线索只能说明候选位置，科学判定对照实际原文。
- 上轮普通网页没有发送事实表契约。 `core/workspace/guide.py` 在候选单篇复杂方法问答中启用事实核对，把当前所问与实际证据编号一起送入，沿用严格引文校验。 `core/workspace/reading_analysis.py` 可记录事实覆盖的要点及检查结果。普通讨论和方案生成继续走原路径。没有逐请求切换实验环境变量。
- 指标本身的比例或百分数与两率差值的百分点分开。对已知含两种分母的候选阅读材料增加明确单位反例检查。表格数值继续由程序从定位成功且条件一致的原表计算。旧坏回执保留，D1 历史单位混淆在回放时被拒绝。
- 零收费回放对照旧实际材料。BPR 追问选到存储动机与重排正文，SimCSE 选到图 1 正负例，原 N1 问法改为方法且取到两批语料及组合顺序。离线联合回归 **84 项通过**，含本轮 8 项新测试。未将旧轮次测试重复计入。 [离线回放](reading-convergence/offline-replay.json) · [离线 JUnit](reading-convergence/offline.xml)

## 首轮真实网页验收

由浏览器在隔离项目中依次发送固定 12 题。冻结指纹为 `80d57203fab7dc0411c85c12413e161032feab44756c632d5947768c222eef1f`，开发材料 DPR、BPR、SimCSE、Don’t Stop Pretraining 沿用隔离副本。保留材料 BERT 在冻结后选定，通过网页实际上传。每题只发送一次，浏览器观察超时先按原请求编号查状态。网络成功 **12/12**，业务保存 **11/12**，11 条已保存回复的资料和引文经刷新回看一致。严格内容 **9/12**，其中开发 **7/9**、保留 **2/3**。首段清楚 **9/12**。 [逐次索引](reading-convergence/attempt-index.json) · [浏览器回执](reading-convergence/browser-transcript.json)

| 题 | 事实核对契约实际入模 | 首轮必答要点、引用与问题 | 严格通过 | 首段清楚 |
| --- | --- | --- | --- | --- |
| B1 | 是 | p.3 已有两个阶段的表示、运算与返回范围。原答正确说出筛选和范围，随后把原文明确定义的段落码 `hp` 写成重排时的未知项。校验拒绝，网页请求终态失败。 | 否 | 否 |
| B1f | 否 | 首题失败后承接了旧表格回答，选到 p.4 的表和摘要，遗漏 p.3 重排原文。讲到了存储动机，却把重排概括成连续向量重排，并断言 `k≥20` 才追平或略超，和同表 Top-20 两列均低于 DPR 冲突。 | 否 | 否 |
| S1 | 是 | 引用 p.2 图 1。无监督同句两次前向、不同 dropout 和批内负例；有监督蕴含正例、矛盾难负例及批内负例。 | 是 | 是 |
| S2 | 是 | 引用 p.2 及 p.8，直接说明两遍产生不同表示、同批句子作负例，以及 NLI 标签如何选正例和难负例。 | 是 | 是 |
| T1 | 是 | p.2、5、6。先领域未标注语料，后目标任务未标注训练文本，正确交代组合顺序和标签条件。 | 是 | 是 |
| T2 | 是 | 另一种口语问法同样覆盖两阶段，并区分继续预训练与下游监督分类。 | 是 | 是 |
| D1 | 否 | p.2 §2 与 p.5 表注。以问题为单位，给出正确分子、分母和比例／百分数单位，指出表注歧义和核对路径。 | 是 | 是 |
| D2 | 否 | p.5 表 2。NQ 为 78.4 对 59.1，SQuAD 为 63.2 对 68.8，方向相反，网页程序计算 +19.3、−5.6 个百分点。 | 是 | 是 |
| B2 | 否 | p.4 表 1。四个差值为 −4.9、−3.8、−0.5、−1.5 个百分点，正文和程序核算一致。区分小 k 的实际重要性与落后成因。引用 BPR 论文对 recall 的字面措辞，不能凭该措辞推断评估实现。 | 是 | 是 |
| H1 | 否 | 只选到 p.8 消融和正文开头。回答明确承认证据不足，但上传的 p.4 实有 MLM 和 NSP 的定义及答案构造。遗漏关键原文和 NSP 正负例，按固定判据失败。 | 否 | 否 |
| H2 | 否 | p.8 表 5。QNLI 为 88.4 对 84.9，SST-2 为 92.7 对 92.6，均为开发集且方向同为下降。可选算术未定位，网页明确拒算。 | 是 | 是 |
| H3 | 否 | 明确说明 GLUE 无法证明中文医疗问答引用准确，指出语言、领域、任务和证据评估需要另行验证。 | 是 | 是 |

关键科学错误计 **3 处，分布于 2 题**。B1 一处被校验拦截。B1f 的两处出现在用户可见回复。H1 是选段与答题覆盖失败，单独统计。首轮网关耗时中位数 30.55 秒，范围 15.07–48.63 秒。已保存回复长度中位数 538 字符。未进行同配置收费复抽。 [实际输入](reading-convergence/model-inputs.json) · [原始可见回执与结束原因](reading-convergence/gateway-responses.json) · [事实表、引文、补读轨迹与保存状态](reading-convergence/source-snapshots.json)

桌面截图已查看 BPR 的表格核算和原请求在途状态，390px 截图已查看两阶段继续训练答复的资料范围。B1 截图记录的是浏览器观察超时后的在途画面，最终拒绝以原请求终态为准。 [桌面表格](reading-convergence/r1-B2-desktop.png) · [桌面在途](reading-convergence/r1-B1-desktop.png) · [390px 资料范围](reading-convergence/r1-T1-390px.png)

## 首轮后的有依据补修

- `core/workspace/conversations.py` 的候选配置从最近一次失败或中断的方法请求恢复追问范围。原问题重新使用项目内论文选择规则，支持当时明确点名覆盖旧勾选。当前消息若明确切换，使用本轮目标。历史回答仍可回看；它不替代本轮论文证据。
- `core/workspace/guide.py` 和 `core/workspace/reading_analysis.py` 将实际片段中可定位、重复出现的明确符号类型连同证据号交给模型。BPR p.3 的段落二值码可直接核查，缺失的实现细节单独保留。仍须人工核对科学解释。回复不会被程序替换为标准答案。
- `core/workspace/reading_coverage.py` 和 `core/workspace/paper_reading.py` 识别自然问法中的预测目标和并列任务名称。在各名称附近找定义证据，避免只匹配消融标题，避免拿上一任务的定义给下一任务。覆盖后停止追加低相关片段。实际 PDF 离线回放选到 BERT p.4 的 MLM 目标与 NSP 正负例两个窗口。BPR 追问离线选到 p.2 存储动机与 p.3 重排。BERT 已成为开发回归材料。
- 本次补修是第二轮的**离线候选**。独立保存代码和规则指纹，首轮冻结文件及实际原答未改。第二轮的冻结、独立保留题和真实模型结果均尚无记录。 [离线候选指纹](reading-convergence/offline-candidate-manifest.json) · [选段与符号回放](reading-convergence/offline-candidate-replay.json)

早期离线补修已定位到 BERT p.4，但仍遗漏随机负例。后续修正任务名邻域选择后复核通过。两次普通沙箱运行停在 `TestClient` 环节，已结束进程。最终复跑在本机事件循环执行，使用临时资料和离线提供商。 [中间选段回放](reading-convergence/offline-round2-replay.json)

```powershell
.\venv\Scripts\python.exe -m pytest -q `
    tests/test_reading_candidate.py tests/test_reading_grounded_explanations.py `
    tests/test_paper_understanding_accuracy.py tests/test_plan_generation_contract.py `
    tests/test_guided_acceptance_guard.py tests/test_question_guided_paper_reading.py `
    --basetemp .pytest_cache/reading-convergence-delivery-verified `
    --junitxml docs/reading-convergence/offline-delivery.xml
```

最终合跑 **124 项通过，0 失败，0 跳过**。其中本轮专项 13 项，既有相关回归 111 项。既有回归包含方法核对与历史回执、按问题阅读与项目账号范围、方案生成修订与下载证据、调用护栏。首轮 84 项及中间 71 项均为重复覆盖，未累计。现有 pytest 配置和弃用警告仍在。 [最终 JUnit](reading-convergence/offline-delivery.xml)

## 账本与停止条件

- 新授权有效期按上海时间为 **2026-11-11 23:48:04 至 2026-11-12 01:18:04**。累计 **12/24 次**，没有生成额外重试。已知输入 47,888 token，输出 48,825 token。按事先核对的 TokenDance 峰值费率本地估算 **¥0.486376**，保守预留合计 **¥1.238902**。提供商实收未取得。 [独立账本](reading-convergence/ledger.json)
- **达到停止条件，尚未达到完成标准。** 第一轮阅读未达门槛。第二轮因本窗口到期而未执行，剩余次数随窗口失效。修复后的 BPR 解释准确性和全新保留材料仍待真实网页验证。完整科研主线未启动，未创建、采纳或下载本轮正式方案。补修的计划生成、修订与下载仅有相关离线回归。
- 交付时核对 `8125` 无监听。保留隔离数据和证据，未启停主服务，未操作真实项目，未提交、推送或部署。日常默认配置维持原设置。
