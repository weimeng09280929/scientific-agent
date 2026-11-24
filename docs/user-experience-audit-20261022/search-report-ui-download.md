# 研究现状整理：federated learning non-IID FedAvg FedProx comparison 的 并根据实际找到的资料

- 任务：`task-7d392b51d8ff41e8ab67426ce62c32f3` · 状态：部分完成（有来源失败）
- 检索范围：未限定年份
- 查询（3 条，上限 3）：
  - federated learning non-IID FedAvg FedProx comparison（直接组合用户给出的核心概念（federated learning、non-IID、FedAvg、FedProx、comparison），可覆盖最相关的代表性论文。）
  - FedProx FedAvg non-IID federated learning empirical evaluation benchmark（在保留原意的基础上补充 empirical evaluation/benchmark 等常见检索词，便于定位对 FedAvg 与 FedProx 在 non-IID 设定下进行实验对比的论文。）
  - federated learning non-IID algorithms survey FedAvg FedProx（加入 survey 一词，用于查找综述类文献，从而依据实际检索到的资料整理该方向的研究现状。）

## 来源执行情况（各次尝试历史，含补跑前的失败）
- openalex：10 条
- arxiv：**调用失败**（timeout）
- openalex：10 条
- arxiv：**调用失败**（timeout）
- openalex：10 条
- arxiv：**调用失败**（timeout）

## 覆盖范围
本次为有限资料整理：来源各次有效返回合计 30 条（含重复），处理 19 条去重候选，其中 1 条纳入、18 条已返回但筛选排除；新保存 19 篇、关联已有 0 篇。失败来源：arxiv。纳入资料中摘要可用 1 条，仅元信息 0 条；未读取全文。

> 证据边界：本轮候选均含摘要，分析范围仍以摘要为限，未阅读全文。

## 检索计数
以下为报告生成时的服务端统计快照；各次返回含重复，不等于唯一论文数。
- 来源原始记录累计：未知（历史记录未提供）
- 有效记录累计：30
- 去重后、候选上限前：19
- 已处理候选：19
- 受候选上限未处理：0
- 纳入综合：1
- 已返回但排除：18
- 新入库：19
- 关联已有：0
- 排除：Comparative Analysis of FedAvg and FedProx Algorithms in Federated Learning for Handwritten Character Recognition on the EMNIST Dataset；理由：比较FedAvg与FedProx，但未涉及非IID数据。；判定来源：model
- 排除：FedBN: Federated Learning on Non-IID Features via Local Batch Normalization；理由：聚焦非IID特征与本地批归一化，未比较FedAvg/FedProx。；判定来源：model
- 排除：A Study of Efficient Optimizer Configuration Strategies in Federated Learning for FedAvg and FedProx；理由：比较FedAvg与FedProx优化配置，但未涉及非IID。；判定来源：model
- 排除：Federated Learning on Non-IID Data Silos: An Experimental Study；理由：非IID联邦学习实验研究，未提及FedAvg/FedProx比较。；判定来源：model
- 排除：Collective Intelligence for Cybersecurity: Federated Learning under Non-IID Conditions for Intrusion Detection；理由：面向非IID入侵检测的联邦学习，未比较FedAvg/FedProx。；判定来源：model
- 排除：Communication-Efficient Federated Learning under Non-IID Data: Aggregation and Compression Strategies；理由：非IID下通信高效聚合压缩，未比较FedAvg/FedProx。；判定来源：model
- 排除：Federated Learning With Taskonomy for Non-IID Data；理由：非IID联邦学习任务分组方法，未比较FedAvg/FedProx。；判定来源：model
- 排除：FedProc: Prototypical contrastive federated learning on non-IID data；理由：非IID原型对比联邦学习，未比较FedAvg/FedProx。；判定来源：model
- 排除：Personalized Cross-Silo Federated Learning on Non-IID Data；理由：非IID个性化跨孤岛联邦学习，未比较FedAvg/FedProx。；判定来源：model
- 排除：Beyond Fedavg: Epochfed for Robust Non-IID Healthcare Federated Learning；理由：非IID医疗联邦学习，仅提及FedAvg未比较FedProx。；判定来源：model
- 排除：No Fear of Heterogeneity: Classifier Calibration for Federated Learning with Non-IID Data；理由：非IID分类校准联邦学习，未比较FedAvg/FedProx。；判定来源：model
- 排除：A Non-parametric View of FedAvg and FedProx: Beyond Stationary Points；理由：比较FedAvg与FedProx理论性质，但未涉及非IID。；判定来源：model
- 排除：Federated learning for privacy-preserving at-risk student prediction in health professional education: A multi-scenario evaluation of FedAvg and FedProx；理由：多场景评估FedAvg与FedProx，但未明确非IID。；判定来源：model
- 排除：FedDC: Federated Learning with Non-IID Data via Local Drift Decoupling and Correction；理由：非IID本地漂移解耦校正，未比较FedAvg/FedProx。；判定来源：model
- 排除：Client Selection for Federated Learning With Non-IID Data in Mobile Edge Computing；理由：非IID移动边缘客户端选择，未比较FedAvg/FedProx。；判定来源：model
- 排除：Comparative Evaluation of Federated Learning Algorithms in Dirichlet Non-IID Medical Imaging；理由：Dirichlet非IID医学影像算法比较，但未点名FedAvg/FedProx。；判定来源：model
- 排除：FedDRL: Deep Reinforcement Learning-based Adaptive Aggregation for Non-IID Data in Federated Learning；理由：非IID自适应聚合深度强化学习，未比较FedAvg/FedProx。；判定来源：model
- 排除：Non-IID data and Continual Learning processes in Federated Learning: A long road ahead；理由：综述非IID与持续学习，未比较FedAvg/FedProx。；判定来源：model
- 说明：候选数为去重合并后的任务级总数（上限见 limits.max_candidates），
  非每条查询、每个来源各自的上限；入库数指其中实际写入论文库的新文献。

## 代表性研究
- [`cand-37856f7`] 基于摘要，该工作关注类别偏斜 non-IID 联邦设置下 FedAvg 与 FedProx 的优化稳定性、收敛和分类性能比较，并结合 ResNet18、VGG16、VGG19 三种 DCNN 架构进行实验。

## 方法之间的联系与差异
基于摘要，cand-37856f7ade314ea5bba30d5598b16ee0 在同一实验条件下并列比较 FedAvg 与 FedProx，并将两种联邦优化算法分别与 ResNet18、VGG16、VGG19 三种 DCNN 架构结合。其方法联系在于二者作为联邦优化算法比较对象，共享 10 客户端、类别偏斜 non-IID、20 通信轮的实验设定；摘要还提到近端正则化，可能用于解释 FedProx 与 FedAvg 的差异，但摘要末句在“reduce the client”处截断，无法确认近端正则化的完整机制或具体结论。

## 有文献支持的局限或分歧
- 基于摘要，该实验设定仅明确提及 10 客户端、类别偏斜 non-IID 和 20 通信轮；这限制了对其他客户端规模、其他 non-IID 形式或程度、不同通信预算下结论的推广。（来源：`cand-37856f7`）
- 基于摘要，摘要末句在“reduce the client”处截断，无法确认近端正则化具体降低客户端的什么指标；不能将该未完成句补全为确定发现。（来源：`cand-37856f7`）
- 基于摘要，未提供数据集名称、超参数、统计显著性、重复次数等细节，因此无法判断 FedAvg 与 FedProx 比较结果的稳健性和可复现性。（来源：`cand-37856f7`）
- 基于摘要提供的有限内容，不能据此认定 FedAvg 与 FedProx 在 non-IID 比较方向上的创新性、研究空白或领域整体结论。（来源：`cand-37856f7`）

## 尚需补充核查
- 仅当前查询与来源的有限资料整理；排除不等于未返回，入库不等于纳入综合；未读取全文。
- 仅元信息不能支持论文方法或结论；摘要节选不能替代全文核对。

## 可选择的下一步
- 核对纳入资料的摘要与原文，必要时补充材料
- 讨论并明确确认研究问题
- 讨论比较条件，拟定和审阅验证方案（不执行实验）

> 候选文献不等于已阅读或认可；入库论文不自动成为正式研究问题的依据关联。整理结果不代表研究结论得到验证。