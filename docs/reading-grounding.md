# 论文阅读选段与原文核对专项

已完成本地选段补读、受限的单次调用事实核对实验和表格差值核算。隔离网页完成三篇开发论文上传，以及 BPR 固定材料首题的真实 Flash 诊断。**真实网页定向复验和保留题未完成。** 第二个阅读请求在调用前被到期保护拒绝，自动审批也拒绝启动新的收费验收窗口。网站仍使用 Flash 默认路由，方法事实核对只在隔离实例显式开启。

## 原因与实际修改

| 原因 | 生效位置与行为 |
| --- | --- |
| SimCSE H1 的自然问法没有被分到方法，旧请求正文窗口全在首页。旧选段没有针对问到的不同部分检查覆盖。 | `core/workspace/paper_reading.py` 增加自然构造、比较、定义等问法识别，结合文中标题、过程线索与双语检索线索。对明确问到的构造要素检查初选，缺项时在同项目已保存窗口中补一次。总数仍最多三段，可替换低匹配且不提供独有要素的一段。缺项仍在时说明本轮片段不足。 |
| 短追问曾沿用论文，但题意分流仍可能只读当前短句。部分明确换文献说法也未被识别。 | `core/workspace/conversations.py` 继续读取本会话已存用户问题，避免把旧助手解释当原文。识别明确的换论文指令。 `core/workspace/guide.py` 从本轮及承接问题选择阅读目标，显式切换继续走当前项目题名、DOI 和勾选校验。 |
| BPR 的完整方法段已经入模，历史模型仍把同一个符号在下一步的使用误解成新表示。 | `core/workspace/reading_analysis.py` 和 `guide.py` 增加单次调用的可选实验。输出先列必要事实，再解释。每条带本轮原文编号，并标记明示、推断、缺失或冲突。程序检查编号范围、重复定义以及能够识别的明确类型错配和前后反转。只识别有限的直接类型表述，不能取代逐条核对。 |
| BPR B2 的源数 49.7 和 53.5 都正确，旧回答差值错写为 5.8。 | 聚焦实验回答可提交同一编号表中的两行、同一数值列及运算类型。服务器只从实际送入的原文解析可唯一定位的行列，核对单位及能识别的 k、数据集条件后用 Decimal 计算。结果随回答展示并绑定原文编号。多表、含糊行、缺列、异单位或不支持的布局可拒算。 |

检索只使用项目内已有文字。补读不调用模型、外部检索或解析服务，不扩大现有上下文额度。初选、缺项、补选和最终实际提供的片段分别记录；初选落选的文本不会进入模型，编号仍在原有上下文投影和版本范围内生成。保持原方案生成、修订、长引文附录和阅读快照结构。 [原理解专项](paper-understanding-accuracy.md) · [原模型对照](paper-model-comparison.md) · [原保留题判据](paper-understanding-accuracy/holdout-criteria.md)

## 回放与离线验证

- **SimCSE H1**。对原 PDF 使用项目 pypdf 6.21.4 和原分块窗口回放，历史实际入模为 p.1、p.1、p.1。当前初选为 p.15、p.1、p.5，覆盖检查发现负样本来源缺失，补入 p.2 图 1 周边窗口并剔除 p.15。最终 p.1、p.2、p.5，p.2 明确给出了无监督设定下从批内取负例。当前仍混入旁支页面，补读没有定位 p.3 方法正文，这次结果只能确认关键依据已选到。 [离线选择快照](reading-grounding/offline-pdf-replay.json)
- **BPR B2 和 DPR D2**。用原请求中的表格片段离线核算。BPR 对应 TQA Top-1 为 49.7 对 53.5，左减右 −3.8 个百分点，另外三个比较为 −4.9、−0.5、−1.5。DPR 表 2 对应 NQ 为 +19.3 个百分点、SQuAD 为 −5.6 个百分点。保留原回答，未产生新模型回执。
- **测试去重 133 项通过**。21 项为新增，112 项为既有回归。第一次新增专项测试为 20 通过、1 失败，补上「命中具体指什么」的指标分流后复跑通过。联合回归 128 项，冻结专项复跑 26 项，其中 21 项重合。覆盖换论文、跨账号、旧证据、输入预算、取消与超时保护、方案生成和修订、长引文快照。上面三组实际材料核对单列，不计入 pytest 总数。 [联合回归](reading-grounding/offline.xml) · [冻结复跑](reading-grounding/frozen-offline.xml)

```powershell
.\venv\Scripts\python.exe -m pytest -q tests/test_reading_grounded_explanations.py tests/test_question_guided_paper_reading.py tests/test_guided_paper_reading.py tests/test_research_gap_guidance.py tests/test_plan_generation_contract.py tests/test_guided_acceptance_guard.py --basetemp .pytest_cache/reading-grounding-regression --junitxml docs/reading-grounding/offline.xml
.\venv\Scripts\python.exe -m pytest -q tests/test_reading_grounded_explanations.py tests/test_paper_understanding_accuracy.py --basetemp .pytest_cache/reading-grounding-frozen --junitxml docs/reading-grounding/frozen-offline.xml
```

## 固定材料真实对照

B1 通过 8125 正常网页登录、上传、选择与发送。诊断启动器只在本题复用先前已人工核对的相同 B 组片段。前后同为 Flash、温度 0.2、`json_object`、8192 输出 token，问题与 p.3 §3.3 的 745 个字符逐字一致。隔离项目、证据编号、请求身份及上下文元数据不同，只有一组同题样本。

| 比较 | 科学内容与可理解性 | 网络延迟 | 正文长度 | 本地估算 |
| --- | --- | ---: | ---: | ---: |
| 历史 B1 · 固定材料 | 未通过。两阶段框架清楚，但将原文明示的段落码解释为未知 | 13.822 秒 | 723 字符 | ¥0.018158 |
| 本轮 B1 · 事实核对实验 | 通过本题的冻结方法判据。指出汉明距离两侧二值、重排用连续查询与原定义下的段落二值码、返回 top-k。把代码的实现细节留作核查，正文较短 | 30.747 秒 | 348 字符 | ¥0.036938 |

新回执的八项事实均引用同一页的实际编号，重排段落表示与最终解释一致。仍可把「按字面」说明压缩为论文描述与代码细节的区别。单题不足以证明稳定改善，故未默认启用实验。Playwright 首次未捕获 POST 响应事件，随后用原请求身份读回完成状态，未再次发送。刷新后原文与引文快照逐项一致。 [同题证据核对](reading-grounding/fixed-pair.json) · [实际请求](reading-grounding/first/model-inputs.json) · [模型可见原答](reading-grounding/first/gateway-responses.json) · [网页与只读回看记录](reading-grounding/browser-transcript.json) · [已查看的网页截图](reading-grounding/fixed-B1.png) · [快照](reading-grounding/first/source-snapshots.json)

## 零收费网页补验

使用独立 8125 临时夹具和虚构 Cedar PDF，共 **5 个去重浏览器场景通过**，包含 3 次本地替身调用，外部调用为 0。先用旧措辞跑通夹具，随后换成「Cedar 怎样用表格与说明文字产生排序列表？」重新核对同一组场景。新版断言实际分类为方法，PDF 第 4 页进入替身的最终上下文。另核对键盘打开原文、桌面与 390px 刷新回看、双论文分开显示、替换资料后保留旧引文，以及跨对话迟到响应隔离。已查看两张新版截图。这组结果单独记录。 [场景回执](reading-grounding/offline-browser-natural/browser-results.json) · [替身实际输入](reading-grounding/offline-browser-natural/actual-provider-input.json) · [引文快照](reading-grounding/offline-browser-natural/browser-citation-receipts.json) · [桌面截图](reading-grounding/offline-browser-natural/source-desktop.png) · [390px 截图](reading-grounding/offline-browser-natural/evidence-390px.png)

## 真实验收状态、费用及阻塞

| 类别 | 结果 |
| --- | --- |
| 开发论文上传 | DPR、BPR、SimCSE 通过真实网页上传并在项目显示。 [截图](reading-grounding/uploads-development.png) |
| 开发题真实首轮 | 仅 B1 固定材料诊断得到有效回答。诊断不计网站通过率。正常选段的 SimCSE、BPR 方法和 DPR/BPR 表格比较待真实复验。 |
| 短追问 | B1f 的原请求被到期保护拒绝，网页保存了失败状态，网络尝试为 0，不能评分。 [到期提示](reading-grounding/fixed-B1f.png) |
| 真实复测 | 未启动。自动审批拒绝延长原 90 分钟运行窗口。收到重复任务后，审批再次明确要求单独批准新的 90 分钟窗口。旧账本和截止时间保持不变，续验未建账、未发送，拟续验配置已标记为未批准。 [拟续验记录](reading-grounding/continuation/authorization-draft-rejected.json) |
| 保留题 | 产品于 11:06:56 UTC 冻结后选择 Gururangan 等的公开论文。已下载并先定方法、实验和证据边界三题。0/3 已执行，尚不能评分。SimCSE 已转为开发材料。 [冻结指纹](reading-grounding/frozen-version.json) · [保留题判据](reading-grounding/holdout-criteria.md) |

`r6-261111` 账本记录两次实际 Flash 网络尝试，分别为建项目和 B1。均 HTTP 200，网关返回 `deepseek-v4-1-flash-261017`，两次输入共 3,717、输出 5,182 token。按目录参考价格本地估算 ¥0.048890，保守预留 ¥0.178204，实际账单未知。没有自动补发，也没有额外核查模型调用。 [独立账本](reading-grounding/first/ledger.json) · [逐次调用索引](reading-grounding/first/attempt-index.json) · [原运行窗口](reading-grounding/authorization.json)

已关闭本轮浏览器和 8125 实例，未操作 8001、8002、8077。核对了历史请求、回答、判据及原 r5 账本指纹。等待用户批准新的真实调用窗口，并等待独立只读复核。未提交、未部署，也未推进完整科研主线。
