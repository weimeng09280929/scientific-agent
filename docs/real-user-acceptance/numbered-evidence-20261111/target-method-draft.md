# 小规模比较方案：命中口径是否改变 BM25/DPR/BPR 排序

状态：未采纳讨论稿，非当前正式方案；依据待复核：False

项目名称：我对长文档里怎么找到有用的段落感兴趣

研究问题：候选问题：命中口径（按问题计 vs 按段落计）是否改变段落检索方法的排序：研究问题（候选，待核查）：把 top-k 命中定义为“前 k 段中至少一段含答案”（按问题计）还是“前 k 段中含答案段落的比例”（按段落计），是否会改变 DPR、BPR 与 BM25 之间的相对排序？

依据：DPR §2 正文明确按问题计（“the fraction of questions for which C_F contains a span that answers the question”），而 DPR 表 2 表注写的是“the percentage of top 20/100 retrieved passages that contain the answer”，BPR §4 评估部分写的是“top-k recall (the percentage of positive passages in the top-k passages)”，字面按段落计。k>1 时两者数值不同。

待补：两篇的实验附录、指标实现或代码，用以判定表注措辞是笔误还是另一套统计；以及在 k=20 时用两种口径重算后的排序是否反转。

注意：这是候选问题，本轮材料不足以判定哪种口径是作者实际采用的。

方案类型：计算研究；版本：讨论稿，尚无正式版本；修订自：无

模板版本：stem-plan-v1

待确认：设备规格与可用数量；语料来源与规模；段落切分方式；正例标注规则与每问题正例数；时间预算；经费预算；方法版本与预训练权重；使用公开 QA 子集还是单篇长文档

参考来源：Dense Passage Retrieval for Open-Domain Question Answering (paper_a4b04e1f8ecc)；DOI：无

参考来源：Efficient Passage Retrieval with Hashing for Open-domain Question Answering (paper_2b2f6cdcb6c5)；DOI：无

**方案类型：计算研究 · 模板 stem-plan-v1**



> 研究方案建议，尚未执行。结构完整不等于科学有效；采纳状态见记录标记。



## 研究目标与明确问题

- 【用户提供】研究问题（候选，待核查）：把 top-k 命中定义为“前 k 段中至少一段含答案”（按问题计）还是“前 k 段中含答案段落的比例”（按段落计），是否会改变 DPR、BPR 与 BM25 之间的相对排序？

依据：DPR §2 正文明确按问题计（“the fraction of questions for which C_F contains a span that answers the question”），而 DPR 表 2 表注写的是“the percentage of top 20/100 retrieved passages that contain the answer”，BPR §4 评估部分写的是“top-k recall (the percentage of positive passages in the top-k passages)”，字面按段落计。k>1 时两者数值不同。

待补：两篇的实验附录、指标实现或代码，用以判定表注措辞是笔误还是另一套统计；以及在 k=20 时用两种口径重算后的排序是否反转。

注意：这是候选问题，本轮材料不足以判定哪种口径是作者实际采用的。

> 来源：已确认问题 · 5ff64bcbca39492d8797973e97b70fc4 · 2026-11-10T17:22:43.058466+00:00

> 依据原文：
> 研究问题（候选，待核查）：把 top-k 命中定义为“前 k 段中至少一段含答案”（按问题计）还是“前 k 段中含答案段落的比例”（按段落计），是否会改变 DPR、BPR 与 BM25 之间的相对排序？
> 
> 依据：DPR §2 正文明确按问题计（“the fraction of questions for which C_F contains a span that answers the question”），而 DPR 表 2 表注写的是“the percentage of top 20/100 retrieved passages that contain the answer”，BPR §4 评估部分写的是“top-k recall (the percentage of positive passages in the top-k passages)”，字面按段落计。k>1 时两者数值不同。
> 
> 待补：两篇的实验附录、指标实现或代码，用以判定表注措辞是笔误还是另一套统计；以及在 k=20 时用两种口径重算后的排序是否反转。
> 
> 注意：这是候选问题，本轮材料不足以判定哪种口径是作者实际采用的。

- 【系统建议（未验证）】小规模阶段的目标不是复现论文全部数值，而是检验同一批排名列表换用两种命中口径后，BM25、DPR、BPR 的相对排序是否变化。



## 相关工作与实际引用依据

- 【文献依据（仅提供范围）】DPR §2 将 top-k retrieval accuracy 定义为：在前 k 个候选段落中至少有一个能回答问题的 span 的问题所占比例，分母是问题数。 [paper_a4b04e1f8ecc]

> 文献：Dense Passage Retrieval for Open-Domain Question Answering · 第 2 页

> 依据原文：
> iven a questionq,
> the task is to ﬁnd a spanw(i)s ,w (i)s+1,··· ,w (i)e from
> one of the passagespi that can answer the question.
> Notice that to cover a wide variety of domains, the
> corpus size can easily range from millions of docu-
> ments (e.g., Wikipedia) to billions (e.g., the Web).
> As a result, any open-domain QA system needs to
> include an efﬁcientretriever component that can se-
> lect a small set of relevant texts, before applying the
> reader to extract the answer (Chen et al., 2017). 4
> Formally speaking, a retriever R : (q,C)→C F
> is a function that takes as input a questionq and a
> corpusC and returns a much smaller ﬁlter set of
> textsCF⊂C , where|CF| =k≪|C| . For a ﬁxed
> k, a retriever can be evaluated in isolation on top-k
> retrieval accuracy, which is the fraction of ques-
> tions for whichCF contains a span that answers the
> question.
> 3 Dense Passage Retriever (DPR)
> We focus our research in this work on improv-
> ing the retrieval component in open-domain QA.
> Given a collection ofM text passages, the goal of
> 

- 【文献依据（仅提供范围）】DPR 表 2 表注把 top-20/100 retrieval accuracy 写成“前 20/100 个检索段落中含答案段落的百分比”，字面分母是段落数。 [paper_a4b04e1f8ecc]

> 文献：Dense Passage Retrieval for Open-Domain Question Answering · 第 5 页

> 依据原文：
> 6773
> Training Retriever Top-20 Top-100
> NQ TriviaQA WQ TREC SQuAD NQ TriviaQA WQ TREC SQuAD
> None BM25 59.1 66.9 55.0 70.9 68.8 73.7 76.7 71.1 84.1 80.0
> Single DPR 78.4 79.4 73.2 79.8 63.2 85.4 85.0 81.4 89.1 77.2BM25 + DPR 76.6 79.8 71.0 85.2 71.5 83.8 84.5 80.5 92.7 81.3
> Multi DPR 79.4 78.8 75.0 89.1 51.6 86.0 84.7 82.9 93.9 67.6BM25 + DPR 78.0 79.9 74.7 88.5 66.2 83.9 84.4 82.3 94.1 78.6
> Table 2: Top-20 & Top-100 retrieval accuracy on test sets, measured as the percentage of top 20/100 retrieved
> passages that contain the answer. Single and Multi denote that our Dense Passage Retriever (DPR) was trained
> using individial or combined training datasets (all the datasets excluding SQuAD). See text for more details.
> traditional retrieval methods, the effects of different
> training schemes and the run-time efﬁciency.
> The DPR model used in our main experiments
> is trained using the in-batch negative setting (Sec-
> tion 3.2) with a batch size of128 and one additional
> BM25 negative passage per question. We trained
> 

- 【文献依据（仅提供范围）】BPR §4 将 top-k recall 写成“top-k passages 中正例段落的百分比”，字面分母也是段落数。 [paper_2b2f6cdcb6c5]

> 文献：Efficient Passage Retrieval with Hashing for Open-domain Question Answering · 第 4 页

> 依据原文：
> elines We compare our BPR with DPR with
> linear scan and DPR with Hierarchical Naviga-
> ble Small World (HSNW) graphs (Malkov and
> Yashunin, 2020) – which builds a multi-layer struc-
> ture consisting of a hierarchical set of proximity
> graphs, following Karpukhin et al. (2020) – for
> our primary baselines. We also apply two popular
> post-hoc quantization algorithms to the DPR pas-
> sage index: simple locality sensitive hashing (LSH)
> (Neyshabur and Srebro, 2015) and product quan-
> tization (PQ) (Jégou et al., 2011). We conﬁgure
> these algorithms such that their passage representa-
> tions have the same size as that of BPR: the number
> of bits per passage of the LSH is set as 768, and
> the number of centroids and the code size of the
> PQ are conﬁgured as 96 and 8 bits, respectively.
> Experimental settings Our experimental setup
> follows Karpukhin et al. (2020). We evaluate our
> model based on its top-k recall (the percentage of
> positive passages in the top-k passages), retrieval
> 4https://github.com/facebookresearch/
> DPR
> 

- 【系统建议（未验证）】三处表述的字面分母并不一致：DPR 正文按问题计，而 DPR 表注和 BPR §4 按段落计；本轮材料不足以判定表注是笔误还是另一套统计。



## 拟研究的差异或待验证设想

- 【系统建议（未验证）】若每个问题恰有一个正例段落、k 固定且排名列表没有重复段落，则按段落计的正例比例等于按问题计 hit@k 除以 k，两种口径下的方法排序不应改变。

- 【待核实／待确定】若每个问题有多个正例、各问题正例数不同、同一段落重复出现，或按段落计时采用不同分母，两种口径可能给出不同排序，需要用数据核查。



## 研究对象、数据或材料条件

- 【用户提供】比较长文档段落检索效果（验收设定）

> 来源：项目背景 · goal · 1

> 依据原文：
> 比较长文档段落检索效果（验收设定）

- 【待核实／待确定】设备、语料、时间与预算尚未确认；本方案中的规模、方法版本和 k 取值只作为待讨论候选，不表示已具备条件。



## 方法与技术路线

- 【系统建议（未验证）】对同一批问题分别运行 BM25、DPR、BPR，保存每个问题的完整 top-100 排名列表；两种口径都从这份列表离线计算，避免因重跑或截断位置不同引入差异。

- 【系统建议（未验证）】公平比较要求共享语料快照、段落切分、问题集、正例标注和 k；各方法独立产生自己的排名，调参只在开发集进行，测试集不按指标口径回头调参。



## 比较、验证或论证设计

- 【系统建议（未验证）】主分析同时报告按问题计 hit@k 与按段落计正例比例@k；k 至少取 1、20、100。按问题计定义为平均的“前 k 段至少有一个正例”指示值；按段落计定义为平均的“前 k 段中唯一正例段落数除以 k”。

- 【系统建议（未验证）】用按问题重采样的配对 bootstrap 给出方法间差值的区间；若两种口径下排序不一致，再报告发生反转的具体方法对、k 值和样本量。



## 时间、资源与可行性

- 【待核实／待确定】设备规格与可用数量未确认，不能判断单机或单卡是否足够；需要先确认可用硬件、能否加载公开预训练模型，以及是否只需推理。

- 【系统建议（未验证）】小规模阶段可先只做推理和事后指标计算，不训练新模型；资源不足时缩减问题数和 k 范围，但不改变两种口径的定义。



## 局限、未确认假设和待讨论事项

- 【文献依据（仅提供范围）】DPR 表 2 显示 SQuAD top-20 上 BM25 为 68.8、Single DPR 为 63.2，BM25 更高，说明数据特性本身也会影响方法排序。 [paper_a4b04e1f8ecc]

> 文献：Dense Passage Retrieval for Open-Domain Question Answering · 第 5 页

> 依据原文：
> 6773
> Training Retriever Top-20 Top-100
> NQ TriviaQA WQ TREC SQuAD NQ TriviaQA WQ TREC SQuAD
> None BM25 59.1 66.9 55.0 70.9 68.8 73.7 76.7 71.1 84.1 80.0
> Single DPR 78.4 79.4 73.2 79.8 63.2 85.4 85.0 81.4 89.1 77.2BM25 + DPR 76.6 79.8 71.0 85.2 71.5 83.8 84.5 80.5 92.7 81.3
> Multi DPR 79.4 78.8 75.0 89.1 51.6 86.0 84.7 82.9 93.9 67.6BM25 + DPR 78.0 79.9 74.7 88.5 66.2 83.9 84.4 82.3 94.1 78.6
> Table 2: Top-20 & Top-100 retrieval accuracy on test sets, measured as the percentage of top 20/100 retrieved
> passages that contain the answer. Single and Multi denote that our Dense Passage Retriever (DPR) was trained
> using individial or combined training datasets (all the datasets excluding SQuAD). See text for more details.
> traditional retrieval methods, the effects of different
> training schemes and the run-time efﬁciency.
> The DPR model used in our main experiments
> is trained using the in-batch negative setting (Sec-
> tion 3.2) with a batch size of128 and one additional
> BM25 negative passage per question. We trained
> 

- 【系统建议（未验证）】小规模子集上的排序差异不能外推到全库或所有数据集；若改用单篇长文档，领域偏移、段落切分和正例标注都会影响结论，不能把排序变化全部归因于指标口径。



## 参考来源

- 【文献依据（仅提供范围）】Karpukhin et al., Dense Passage Retrieval for Open-Domain Question Answering, EMNLP 2020. [paper_a4b04e1f8ecc]

> 文献：Dense Passage Retrieval for Open-Domain Question Answering

> 依据原文：
> Proceedings of the 2020 Conference on Empirical Methods in Natural Language Processing , pages 6769–6781,
> November 16–20, 2020. c©2020 Association for Computational Linguistics
> 6769
> Dense Passage Retrieval for Open-Domain Question Answering
> Vladimir Karpukhin∗, Barlas O˘guz∗, Sewon Min†, Patrick Lewis,
> Ledell Wu, Sergey Edunov, Danqi Chen‡, Wen-tau Yih
> Facebook AI †University of Washington ‡Princeton University
> {vladk, barlaso, plewis, ledell, edunov, scottyih }@fb.com
> sewon@cs.washington.edu
> danqic@cs.princeton.edu
> Abstract
> Open-domain question answering relies on ef-
> ﬁcient passage retrieval to select candidate
> contexts, where traditional sparse vector space
> models, such as TF-IDF or BM25, are the de
> facto method. In this work, we show that
> retrieval can be practically implemented us-
> ing dense representations alone, where em-
> beddings are learned from a small number
> of questions and passages by a simple dual-
> encoder framework. When evaluated on a
> wide range of open-domain QA datasets, our
> dense retriever outperforms a strong Lucene-
> BM25 system greatly by 9%-19% absolute in
> terms of top-20 passage retrieval accuracy, and
> helps our end-to-end QA system establish new
> 

- 【文献依据（仅提供范围）】Yamada et al., Efficient Passage Retrieval with Hashing for Open-domain Question Answering, ACL-IJCNLP 2021. [paper_2b2f6cdcb6c5]

> 文献：Efficient Passage Retrieval with Hashing for Open-domain Question Answering

> 依据原文：
> Proceedings of the 59th Annual Meeting of the Association for Computational Linguistics
> and the 11th International Joint Conference on Natural Language Processing (Short Papers) , pages 979–986
> August 1–6, 2021. ©2021 Association for Computational Linguistics
> 979
> Efﬁcient Passage Retrieval with Hashing for Open-domain
> Question Answering
> Ikuya Yamada†,‡ Akari Asai∗ Hannaneh Hajishirzi∗,§
> †Studio Ousia ‡RIKEN ∗University of Washington
> §Allen Institute for AI
> ikuya@ousia.jp {akari,hannaneh}@cs.washington.edu
> Abstract
> Most state-of-the-art open-domain question
> answering systems use a neural retrieval model
> to encode passages into continuous vectors
> and extract them from a knowledge source.
> However, such retrieval models often require
> large memory to run because of the massive
> size of their passage index. In this paper, we
> introduce Binary Passage Retriever (BPR), a
> memory-efﬁcient neural retrieval model that
> integrates a learning-to-hash technique into
> the state-of-the-art Dense Passage Retriever
> (DPR) (Karpukhin et al., 2020) to represent
> the passage index using compact binary codes
> rather than continuous vectors. BPR is trained
> with a multi-task objective over two tasks: ef-
> 



## 方法专属设计（计算研究）

### 数据与任务

在固定的小规模评测集上，对同一批问题运行 BM25、DPR、BPR，保存每个问题的 top-k 排名列表（k=1,20,100），再离线用两种口径计算指标；语料来源、段落切分和正例标注待确认，候选为公开 QA 子集或单篇长文档加人工标注查询。

### 基线

BM25、DPR、BPR 三个方法；使用公开官方实现和论文默认或明确报告的主要设置，调参只在开发集进行，测试集不调参。

### 公平比较条件

共享同一语料快照、段落切分、问题集、正例标注和 k；每个方法各自产生排名列表；比较每个方法在两种口径下的分数及方法排序，不跨口径直接比较绝对数值。

### 预算与计算条件

预算待确认；当前只规划小规模推理与指标重算所需的时间和存储，不声称现有设备足够。

### 评估方法

按问题计 hit@k = 平均 1[前 k 段至少有一个正例]；按段落计 positive proportion@k = 平均(前 k 段中唯一正例段落数/k)。补充诊断 positive recall@k = 前 k 段中正例段落数/该问题正例总数。k 取 1、20、100，报告配对差值和按问题 bootstrap 区间。

### 重复性与复现条件

固定数据版本、段落切分、正例标注、方法版本、预训练权重和随机种子；保存每个问题的排名列表与指标计算脚本；硬件与运行环境待确认。



## 已知条件与待确认

- 资源／设备：待确认（待核实／待确定）

- 期限：待确认（待核实／待确定）

- 预算：待确认（待核实／待确定）

- 数据或材料访问：待确认（待核实／待确定）



## 待讨论事项

- 设备规格与可用数量

- 语料来源与规模

- 段落切分方式

- 正例标注规则与每问题正例数

- 时间预算

- 经费预算

- 方法版本与预训练权重

- 使用公开 QA 子集还是单篇长文档



参考论文 ID：paper_a4b04e1f8ecc, paper_2b2f6cdcb6c5