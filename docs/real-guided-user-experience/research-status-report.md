# 研究现状整理：federated learning non-IID FedAvg FedProx data heterogeneity

- 任务：`task-97a744294e274243a6c2bf30d37dd1b2` · 状态：已完成
- 检索范围：未限定年份
- 查询（3 条，上限 3）：
  - "federated learning" AND "non-IID" AND "FedAvg" AND "FedProx" AND "data heterogeneity"（精确覆盖用户给出的核心概念，用于检索非IID数据异质性下FedAvg与FedProx的代表性论文。）
  - ("federated learning" OR "federated optimization") AND ("non-IID" OR "data heterogeneity") AND ("FedAvg" OR "FedProx") AND (survey OR review)（补充survey/review检索，便于整理该主题的研究现状与代表性工作脉络。）
  - ("FedAvg" AND "FedProx") AND ("non-IID" OR "data heterogeneity") AND (comparison OR comparative OR empirical)（聚焦FedAvg与FedProx在非IID数据异质性下的比较性/实证性文献，便于在现有条件下比较后确定研究问题。）

## 来源执行情况（各次尝试历史，含补跑前的失败）
- openalex：10 条
- arxiv：7 条
- openalex：10 条
- arxiv：10 条
- openalex：10 条
- arxiv：10 条

## 覆盖范围
本次为有限资料整理：来源各次有效返回合计 57 条（含重复），处理 20 条去重候选，其中 20 条纳入、0 条已返回但筛选排除；新保存 20 篇、关联已有 0 篇。纳入资料中摘要可用 20 条，仅元信息 0 条；未读取全文。候选上限另限制 25 条未处理。

> 证据边界：本轮候选均含摘要，分析范围仍以摘要为限，未阅读全文。

## 检索计数
以下为报告生成时的服务端统计快照；各次返回含重复，不等于唯一论文数。
- 来源原始记录累计：57
- 有效记录累计：57
- 去重后、候选上限前：45
- 已处理候选：20
- 受候选上限未处理：25
- 纳入综合：20
- 已返回但排除：0
- 新入库：20
- 关联已有：0
- 说明：候选数为去重合并后的任务级总数（上限见 limits.max_candidates），
  非每条查询、每个来源各自的上限；入库数指其中实际写入论文库的新文献。

## 代表性研究
- [`cand-39261f3`] 基于摘要，FedProx 关注联邦网络中系统异构与统计异构并存的问题，并在 FedAvg 基础上引入近端项且允许设备执行可变本地工作。
- [`cand-79f09d2`] 基于摘要，该比较研究关注 FedAvg 与 FedProx 在 IID 与 Dirichlet non-IID 划分下的表现差异。
- [`cand-59d5142`] 基于摘要，该研究关注数据分布从 IID 转为 non-IID 时 FedAvg 与 FedProx 的稳定性变化。
- [`cand-bd010a2`] 基于摘要，该工作关注客户端本地更新次数不一致与加权聚合导致的目标不一致问题，并将 FedAvg、FedProx 纳入统一收敛分析框架。
- [`cand-05ec43b`] 基于摘要，该工作关注 feature shift 型 non-IID，即不同客户端传感器或场景分布不同，并用本地 BatchNorm 减轻聚合前偏移。
- [`cand-ee38b1f`] 基于摘要，该工作关注 cluster-skew 型 non-IID，并用深度强化学习自适应确定客户端聚合权重。
- [`cand-09e37d2`] 基于摘要，该工作关注 non-IID 聚合策略，将 FedAvg 的模型平均、FedProx 的近端项与控制变量方法组合。
- [`cand-cb47bf5`] 基于摘要，该工作关注直接全局聚合造成性能下降的问题，提出服务器侧无数据知识蒸馏微调全局模型。
- [`cand-26299f8`] 基于摘要，该工作关注 FedAvg 在 non-IID 下的聚合局限，用余弦距离权重发散度量划分网络以进行分治训练。
- [`cand-f7b8016`] 基于摘要，该工作关注异构数据下边缘设备资源受限问题，将 FedProx/FedAvg 与模型剪枝、冻结结合。
- [`cand-115b48d`] 基于摘要，该工作关注多种联邦优化算法在 non-IID 数据、客户端参与率和通信预算变化下的受控比较。
- [`cand-ccc5525`] 基于摘要，该工作关注 non-IID 下客户端漂移与通信开销的耦合，并比较 FedAvg、FedProx 及其误差反馈 Top-k 压缩组合。
- [`cand-e4d421c`] 基于摘要，该工作关注 non-IID 下客户端漂移，提出基于粗糙度指数的正则化 FedAvg，并给出非凸收敛分析。
- [`cand-b0cace9`] 基于摘要，该应用研究关注 non-IID 网络流量下的联邦入侵检测，并比较 FedAvg 与 FedProx 配合 1D CNN 的表现。
- [`cand-0b8d3c2`] 基于摘要，该应用研究关注异构 IoT 入侵检测中的设备和数据异构，将硬件感知模糊聚类、Fuzzy-FedProx 与自适应校准结合。

## 方法之间的联系与差异
基于摘要，已有结论可归为几条关系。FedProx 被表述为 FedAvg 的泛化与重参数化，通过近端项处理统计异构，并通过允许可变本地工作量处理系统异构（cand-39261f3cd21d445491c0e21f902dd914）。多篇摘要层面的比较在 EMNIST、MNIST、FashionMNIST 或网络安全数据上报告：IID 下 FedAvg 收敛快且有竞争力，non-IID 下 FedProx 较稳定或较优，但这些结论受各自数据划分、客户端数和指标设置限制（cand-79f09d2342a14a039ba3359a5b256b43；cand-59d51421cb74479c81a222a8ecf657f8；cand-b0cace95490e45f289364afa68aa8af2）。FedNova 将 FedAvg、FedProx 纳入统一框架，指出客户端本地更新次数差异会造成目标不一致，并提出归一化聚合（cand-bd010a2ccc384e14bd7731549ccfe833）。FedBN 针对 feature shift 型 non-IID，用本地 BatchNorm 减轻聚合前偏移，摘要称其优于 FedAvg 与 FedProx（cand-05ec43b85efb40bf8b273e8755058132）。FedDRL、FedHybrid 与 RI-FedAvg 分别从自适应聚合权重、组合近端项与控制变量、粗糙度正则化来处理 non-IID 客户端漂移（cand-ee38b1f4d39945a18e6fe716a0a18fde；cand-09e37d2335404cb1a3f6ca8a29f06f9d；cand-e4d421c285a64a6b9e5a30b3b361279f）。FedFTG 从服务器侧用无数据知识蒸馏微调全局模型，以缓解直接聚合的性能下降（cand-cb47bf56fa6c40ef9ed982b3b015ecde）。Divide-and-Conquer 路线通过权重发散度量划分网络，使 FedAvg 可应对 non-IID（cand-26299f8b123e4e1b9e36d1c5cef571f5；cand-40c34a1002bf49cb8602f3ece3138d45）。系统与通信方向则把 FedProx/FedAvg 与剪枝、冻结或 Top-k 压缩结合（cand-f7b8016bce7a40c1a88014814c927d98；cand-ccc55253f9224812a8f24bb5da98c484），也有工作比较 FedAvg/FedProx 与不同优化器的组合（cand-e917cc602602457a864a33c2eef77712）。应用侧扩展到 IoT 入侵检测、恶意软件检测和空间分布场景，常把 FedAvg/FedProx 与聚类、原型交换、场协调或校准结合（cand-b0cace95490e45f289364afa68aa8af2；cand-0b8d3c2323db403ea0992caacc79c3e6；cand-9b16622c7e1d413cb2b951925b51836b；cand-7d60a18da7bf45fc9ebe165eec9e7dbb）。综合判断：这些资料显示处理 non-IID 的路径大致包括改本地目标、改聚合或归一化、改表示或训练流程、以及系统与通信协同优化；但摘要中的相对优势不能据此判定某一方法普遍更优，属于需要在统一条件下进一步核验的待验证结论。

## 有文献支持的局限或分歧
- 基于摘要，不同工作对 non-IID 的定义并不一致，涵盖标签偏斜、feature shift、class-skew 与 cluster-skew 等，跨论文比较方法优劣时不能直接等同（cand-05ec43b85efb40bf8b273e8755058132；cand-ee38b1f4d39945a18e6fe716a0a18fde；cand-d51d8458d32145b2b91e0220d519eb17；cand-79f09d2342a14a039ba3359a5b256b43）。（来源：`cand-05ec43b`、`cand-ee38b1f`、`cand-d51d845`、`cand-79f09d2`）
- 基于摘要，多篇比较实验依赖模拟客户端和特定数据集或划分方式，例如 EMNIST 的 40 个客户端与 Dirichlet 划分、MNIST/FashionMNIST 的 IID 与 non-IID 对比、10 客户端 class-skewed non-IID 设置；摘要未展开完整超参数与统计检验，因此结果外推受限（cand-79f09d2342a14a039ba3359a5b256b43；cand-59d51421cb74479c81a222a8ecf657f8；cand-d51d8458d32145b2b91e0220d519eb17；cand-b0cace95490e45f289364afa68aa8af2）。（来源：`cand-79f09d2`、`cand-59d5142`、`cand-d51d845`、`cand-b0cace9`）
- 基于摘要，FedAvg 与 FedProx 的比较结论受指标选择影响，涉及准确率、通信开销、收敛曲线 AUC、训练时间或稳定性等不同维度；部分摘要仅报告 FedProx 略更稳定，不能仅凭单一指标判断整体优劣（cand-79f09d2342a14a039ba3359a5b256b43；cand-59d51421cb74479c81a222a8ecf657f8；cand-e917cc602602457a864a33c2eef77712）。（来源：`cand-79f09d2`、`cand-59d5142`、`cand-e917cc6`）
- 基于摘要，理论或收敛性主张带有特定假设：FedProx 摘要称在非一致分布下提供收敛保证，FedNova 摘要强调目标不一致，RI-FedAvg 摘要称在标准假设下收敛到稳定点；摘要未展开全部假设，不能据此断定实际场景中必然稳定收敛（cand-39261f3cd21d445491c0e21f902dd914；cand-bd010a2ccc384e14bd7731549ccfe833；cand-e4d421c285a64a6b9e5a30b3b361279f）。（来源：`cand-39261f3`、`cand-bd010a2`、`cand-e4d421c`）
- 基于摘要，统计异构、系统资源与通信成本常相互耦合；通信压缩研究摘要提示强异构下运行间波动更大，通信轮数、传输比特数与端到端时间不可互相替代，边缘优化摘要也只在特定数据集与剪枝率下报告 F1，因此方法收益不宜直接横向外推（cand-ccc55253f9224812a8f24bb5da98c484；cand-f7b8016bce7a40c1a88014814c927d98）。（来源：`cand-ccc5525`、`cand-f7b8016`）
- 基于摘要，两条 Divide-and-Conquer 候选记录的研究问题、方法描述和结论高度相似，应视为同一路线的重复资料而非相互独立的证据（cand-26299f8b123e4e1b9e36d1c5cef571f5；cand-40c34a1002bf49cb8602f3ece3138d45）。（来源：`cand-26299f8`、`cand-40c34a1`）
- 基于摘要，应用型扩展多在特定数据集和架构上验证，如网络安全流量、IoT 恶意软件或空间分布场景；摘要报告的优势不能推断为其他 non-IID 部署环境的通用效果（cand-b0cace95490e45f289364afa68aa8af2；cand-0b8d3c2323db403ea0992caacc79c3e6；cand-9b16622c7e1d413cb2b951925b51836b；cand-7d60a18da7bf45fc9ebe165eec9e7dbb）。（来源：`cand-b0cace9`、`cand-0b8d3c2`、`cand-9b16622`、`cand-7d60a18`）
- 本整理只依据摘要，未核验全文中的完整算法细节、实验实现、统计显著性与复现条件；因此文中所有方法差异和相对优势均应视为基于摘要的待验证信息（cand-39261f3cd21d445491c0e21f902dd914；cand-79f09d2342a14a039ba3359a5b256b43；cand-115b48d470d54eb8aecbce36a746610e；cand-ccc55253f9224812a8f24bb5da98c484）。（来源：`cand-39261f3`、`cand-79f09d2`、`cand-115b48d`、`cand-ccc5525`）

## 尚需补充核查
- 仅当前查询与来源的有限资料整理；排除不等于未返回，入库不等于纳入综合；未读取全文。
- 仅元信息不能支持论文方法或结论；摘要节选不能替代全文核对。

## 可选择的下一步
- 核对纳入资料的摘要与原文，必要时补充材料
- 讨论并明确确认研究问题
- 讨论比较条件，拟定和审阅验证方案（不执行实验）

> 候选文献不等于已阅读或认可；入库论文不自动成为正式研究问题的依据关联。整理结果不代表研究结论得到验证。