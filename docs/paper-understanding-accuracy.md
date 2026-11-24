# 论文理解准确性专项

2026-11-11，隔离轮次 `r5-261111`。**本轮尚不进入完整科研主线验收。** 通用选段、短追问和表格引文保存已修复。DPR 指标、DPR 表 2、BPR 表 1 通过本轮网页复测。BPR 方法在充分证据下仍有关键解释错误，保留论文还有一项方法选段不足。

## 判据和原文核对

模型调用前独立写定六道开发题的原文位置、必须保留的条件、不可推出的结论和材料不足条件。核对了 r3 和 r4 的实际输入与原答，以及三份实际下载草案。r3 的 BPR 短追问没有选到方法正文，但另有回执在第 3 页已入模时仍误解重排。r4 修订草案只拿到 BPR 第 4 页的正文，保留了对重排表示的待核查标记。两种情况分别归为材料不足与理解偏差。 [预设判据](paper-understanding-accuracy/criteria.md) · [历史回执核对](paper-understanding-accuracy/prior-evidence-review.json) · [r3 原验收](real-user-acceptance/numbered-evidence-20261111/run.md) · [r4 原验收](plan-generation-simplification.md)

- DPR PDF 第 2 页 §2 定义的计量单位是题。分子是在前 k 候选中至少含一个答案跨度的题数，分母是评测题数。第 5 页表 2 的表注字面上像按段落计。作者代码的固定版本按题第一次命中累加，并除以题数。表 2 的 NQ 与 SQuAD 必须按相同 k 和正确训练变体核对。 [DPR 原文](https://aclanthology.org/2020.emnlp-main.550.pdf) · [作者代码及位置](paper-understanding-accuracy/criteria.md#作者代码核对)
- BPR PDF 第 3 页 §3.3 先用两侧二值码与汉明距离取 l 个候选，再用连续查询向量和段落二值码打分取前 k。固定作者代码先解包段落位向量，映射到 ±1 后与连续查询向量计算内积。浮点存储类型不改变其二值取值。作者评估器也按每题第一次命中计数。第 4 页关于读者通常使用更多段落的说明只解释作者为何认为小 k 差距相对次要。 [BPR 原文](https://aclanthology.org/2021.acl-short.123.pdf) · [作者代码及位置](paper-understanding-accuracy/criteria.md#作者代码核对)

只读核对作者仓库固定提交，未执行作者脚本。源码哈希与核对位置留存。公开实现可与定义交叉核验，未在本轮重新跑论文实验。作者代码与人工判据均未进入产品提示词和被测模型上下文。 [作者源码快照索引](paper-understanding-accuracy/author-source-versions.json)

## 代码变更与离线证据

| 文件 | 变更 |
| --- | --- |
| `core/workspace/paper_reading.py` | 实验题保留有界的原始表格块，优先保住表头、完整行、正文指标定义和同页的限定说明。扩展通用指标和局限用语。所有引文位置仍绑定真实上传片段。 |
| `core/workspace/guide.py`、`schema.py` | 聚焦阅读只向模型提供当前证据和用户的阅读问题，要求答复与引文。说明两阶段的对象、表示和返回范围，区分观察、重要性与因果。正文直接回答当前问题，程序已有的分母算例继续独立展示。 |
| `core/workspace/conversations.py`、`research_coach.py` | 短追问沿用本会话最近一篇论文和已保存的用户问题；历史助手结论不作为当前论文证据，移入的旧项目历史不能指定本轮论文。明确更换的论文、显式目标、缺口讨论与权限检查仍按原入口处理。否定式的候选请求不会反向触发缺口生成。 |
| `core/workspace/schema.py` | 首轮三个表格请求收到完整模型原答，但引用的完整表格超过旧的 1,200 字符快照上限。回放原始编号确认 `quote.string_too_long` 后，将服务端编号快照上限与 1,600 字符表格块对齐，保留模型原始引文输入限制。 |
| `web/project_exports_api.py` | 导出分别显示「替代的正式方案」和「修订来源草案」。修改限于文案，原关系和历史记录可继续读取。 |

没有按论文名称、特定实验数值或答案写产品规则。编号与文字校验只核对原文和范围，不能为解释的科学正确性背书。

离线最终去重 **153 项通过**，其中新增 5 项、既有回归 148 项。扩大首跑 147 通过、1 失败，失效缺口的解释被过早识别为直接论文短追问；修复分流后复跑 152 项通过。移动历史边界增加一项独立回归。另有表格长快照、续问、旧草案修订及导出专项回归，重复节点没有加算。 [首跑](paper-understanding-accuracy/offline-first.xml) · [扩大复跑](paper-understanding-accuracy/offline-final.xml) · [移动历史回归](paper-understanding-accuracy/offline-moved.xml) · [导出及快照](paper-understanding-accuracy/offline-final-focus.xml) · [去重统计](paper-understanding-accuracy/offline-counts.json) · [原始快照错误回放](paper-understanding-accuracy/initial-snapshot-length-errors.json)

## 开发样本网页结果与 A/B 诊断

A 为开发题首轮网页实际选段，A2 为修复后同题实际选段，B 为人工核对后从上传 PDF 选取足够回答同题的原文。B 的选段按已上传物理页和实际 chunk 校验，有些段使用含必要内容的整块以保留排版。B 的选段替换仅在隔离诊断启动器生效，产品逻辑不含该试题或其选段指令。三组均从真实网页上传后选择论文、提问，A 与 A2 刷新回看并展开依据。B 的结果不计入网站通过率。

| 用例 | A 首次 | A2 网页复测 | B 人工选段同题诊断 |
| --- | --- | --- | --- |
| D1 · 指标定义 | 原答题级分子分母正确，表格快照保存失败 | **通过**。第 2 页定义与第 5 页表注同时在场，明确区分字面与实际实现，并给核查路径 | **通过**。只给定义及表注也能正确解释 |
| D2 · 表 2 | 四个数值对，称两行训练设置相同，且快照保存失败 | **通过**。同为 Top-20，正确区分 Single DPR 和 BM25。NQ 高、SQuAD 低 | **通过**。还明确指出 BM25 为无训练行 |
| B1 · 两阶段表示 | 已给 p.3，误判段落侧不明确 | **未通过**。答复读到段落码的定义和重排用的相同符号，仍误判为待核 | **未通过**。第 3 页 §3.3 单独送入，仍误判重排段落侧 |
| B1f · 短追问 | **通过**。原文、两侧表示和存储动机都正确 | **未通过**。保持同论文并带回 §3.3，但把重排段落码再次误判为不确定 | **未通过**。第 2 页动机加第 3 页方法也未消除错误 |
| B2 · 表 1 和小 k | 数值与原因区分基本正确，表格快照保存失败 | **通过**。四列数值及差值正确，区分影响重要性和差距原因 | **未通过**。TQA Top-1 的两个源数正确，将 53.5 − 49.7 误算为 5.8 个百分点，正确为 3.8 |
| G1 · 是否没有研究 | 范围判断正确，但附加「每题多个正例会改变分母」的未经核对说法 | **部分通过**。保留判断且没有候选卡，但只从指标名称补写了按题定义 | **部分通过**。正确拒绝否定性结论，但把两篇论文的 Top-k 口径都表述为已从当前片段核对，超出 BPR 本次证据 |

按功能与内容同时满足的严格标准，A **1/6**、A2 **3/6**、B **2/6**。网页业务保存分别为 **3/6、6/6、6/6**。这两个数字分别衡量科学回答与保存状态。A2 的 B1 与 B1f 同类错误连续出现后停止网页原样重试。B 使用不同的人工证据范围完成诊断，B1 和 B1f 再次出错后没有续发方法重试。

A2 首题的浏览器驱动未捕获 POST 响应事件，独立的网络回执已有结果。后续按原请求号读回保存状态，没有重发该题。证据链区分了自动化观察超时、实际模型耗时和真实业务终态。

可理解性人工核对中，D2 与 B2 的通过回答直接给出所问比较，分别约 340 和 540 字符。BPR 方法回答虽然解释了汉明距离和内积，但错误的不确定性提示会误导读者。缺口回答的首段清楚，后续无关的指标解读仍需减少。

[实际入模材料](paper-understanding-accuracy/model-inputs.json) · [原始可见答复](paper-understanding-accuracy/gateway-responses.json) · [逐题状态和耗时](paper-understanding-accuracy/attempt-index.json) · [送入与保存的快照](paper-understanding-accuracy/source-snapshots.json) · [网页记录](paper-understanding-accuracy/browser-transcript.json) · [DPR 表 2 复测](paper-understanding-accuracy/A2-D2.png) · [BPR 方法误判](paper-understanding-accuracy/A2-B1.png) · [BPR 表 1 复测](paper-understanding-accuracy/A2-B2.png)

## 冻结版本保留测试

在首次代码冻结后才按 SimCSE 原文编制保留问题。正式发问前仅补了旧项目历史隔离检查，并再次冻结和验证哈希。该改动独立于 SimCSE 的题目和结果。三题均通过正式网页上传、选论文、发问、展开依据和刷新回看。保留题出结果后代码没有继续调整。

| 用例 | 首次判定 |
| --- | --- |
| H1 · 正负样本构造 | **部分通过**。同一句前向两次和独立 dropout 解释正确。只送入首页，没有方法节中负样本构造所需文字。答复将批内负样本列为待核对，未把常识冒充原文。 |
| H2 · 表 3 消融 | **通过**。默认 82.5、无 dropout 71.1、固定同一 mask 43.6，对齐 STS-B 开发集、BERT-base 和 Spearman。保留任务及外推边界。 |
| H3 · 中文判例检索 | **通过证据边界题**。明确表示 STS 数据及相关系数不能证明目标检索效果，给出了标注、对照与检索指标需求。首段可独立阅读，但约 2,069 字符的全文较长。 |

保留样本 **2/3** 通过预设判据，方法题未完整通过。可核查的剩余选段问题是没有显式「方法」「步骤」字样的正负样本问法只命中首页。若依据此结果修改选段，SimCSE 应转为开发样本，新的保留验证需用另一篇论文。

[保留题判据与 PDF](paper-understanding-accuracy/holdout-criteria.md) · [首次冻结](paper-understanding-accuracy/frozen-version.json) · [保留执行版本](paper-understanding-accuracy/frozen-holdout-version.json) · [H1 方法页不足](paper-understanding-accuracy/H-H1.png) · [H2 表 3 结果](paper-understanding-accuracy/H-H2.png) · [H3 证据边界](paper-understanding-accuracy/H-H3.png)

## 对照、费用与关闭

本轮模型为 TokenDance 的 `deepseek-v4.1-flash-1017`，22 份响应均报告 `deepseek-v4-1-flash-261017`。新账本记录 22 次网络尝试，包括 1 次建项目讨论、18 次开发及选段诊断、3 次保留题。全部返回 HTTP 200，网页三次保存失败见 A。无额外自动重试。运行时采用 24 次、90 分钟的隔离保护，无累计金额上限。

网络延迟中位数为 A 18.690 秒、A2 21.859 秒、B 14.232 秒、保留样本 13.578 秒。组间证据量、缓存及内容不同，延迟只作实测记录。已知输入 **78,551**、输出 **55,479** token。本地保护费率估算 **¥0.600934**，保守预留 **¥2.203680**，提供商实收未知。 [轮次参数](paper-understanding-accuracy/authorization.json) · [独立账本](paper-understanding-accuracy/ledger.json) · [逐次指标](paper-understanding-accuracy/attempt-index.json)

只读核对了候选 `deepseek-v4-pro` 的同一平台公开目录，已准备固定 B 证据、6 道同题和匿名评阅方案。尚未对候选发起模型调用。候选接入、真实输出能力、延迟和结算仍待新增授权及验证。 [待授权对照方案](paper-understanding-accuracy/model-comparison-plan.md)

本轮浏览器和三次 8125 实例进程均已关闭，旧账本及旧期限文件已复核哈希。8001、8002、8077 未操作。未运行完整科研主线、未采纳方案、未提交或部署。按用户要求交付独立只读复核。 [关闭回执](paper-understanding-accuracy/shutdown.json)
