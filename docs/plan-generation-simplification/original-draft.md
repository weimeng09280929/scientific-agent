# 同一公开语料与固定查询下的 BM25、DPR 与 BPR 段落检索比较（方法草案）

状态：未采纳讨论稿，非当前正式方案；依据待复核：False

项目名称：我想比较几种从公开资料里找段落的方法

研究问题：同一评估集的段落检索比较（验收）：在相同公开段落语料和固定查询集合上，BM25、DPR 与 BPR 的按问题计 Top-k 命中率、各自索引体积和查询延迟有何差异？这是流程验收的比较问题，尚未执行，未据此宣称原论文使用不同指标或存在新的研究空白。硬件、可用时间、语料授权和预算仍待确认。

方案类型：计算研究；版本：讨论稿，尚无正式版本；修订自：无

模板版本：stem-plan-v1

待确认：可用硬件（CPU/GPU 型号、内存、存储）与是否可独占测量环境；可用时间与项目截止时间；计算/存储预算；公开语料的授权范围、可再分发性与获取方式；查询集来源、规模与语言/领域分布；gold 相关段落判定由谁标注、标注粒度与一致性检查；k 的取值与是否报告多个 k；BM25 实现版本与参数；DPR/BPR 预训练权重、索引库与近似最近邻配置；是否允许使用外部模型权重与下载；延迟测量口径（冷/热启动、并发、批大小）；是否只比较检索或还需接下游问答

参考来源：Dense Passage Retrieval for Open-Domain Question Answering (paper_8f3858d2f42a)；DOI：无

参考来源：Efficient Passage Retrieval with Hashing for Open-domain Question Answering (paper_0dda881c3109)；DOI：无

**方案类型：计算研究 · 模板 stem-plan-v1**



> 研究方案建议，尚未执行。结构完整不等于科学有效；采纳状态见记录标记。



## 研究目标与明确问题

- 【用户提供】在同一公开语料与固定查询上比较段落检索方法（仅验收设定，尚未执行）

> 来源：项目背景 · goal · 1

- 【系统建议（未验证）】本草案把比较目标限定为同一语料快照、同一切分、同一查询集与同一相关性判定下的段落检索方法比较；公平比较的实现方式是共享输入与评价脚本，各方法分别建立并测量自己的索引，并分别报告按问题计 Top-k 命中率、索引体积与查询延迟。



## 相关工作与实际引用依据

- 【文献依据（仅提供范围）】DPR 论文摘要报告，其稠密检索在多个开放域问答数据集上，以 top-20 段落检索准确率计，比强 Lucene-BM25 系统绝对高 9%–19%。 [paper_8f3858d2f42a]

> 见证据 1 · Dense Passage Retrieval for Open-Domain Question Answering

- 【文献依据（仅提供范围）】BPR 论文摘要报告，BPR 将学习哈希技术集成到 DPR 中，用紧凑二值码而非连续向量表示段落索引，以降低内存需求。 [paper_0dda881c3109]

> 见证据 2 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering



## 拟研究的差异或待验证设想

- 【系统建议（未验证）】在语料快照、段落切分、查询集、gold 判定和 k 完全相同的条件下，三种方法的按问题计 Top-k 命中率差异才可按检索方法解释；索引体积与查询延迟分别独立测量，不合并为单一分数。

- 【待核实／待确定】BM25、DPR 与 BPR 在该固定设定下的命中率、索引体积和延迟差异方向与幅度尚待实验确认。



## 研究对象、数据或材料条件

- 【用户提供】在相同公开段落语料和固定查询集合上，BM25、DPR 与 BPR 的按问题计 Top-k 命中率、各自索引体积和查询延迟有何差异？这是流程验收的比较问题，尚未执行，未据此宣称原论文使用不同指标或存在新的研究空白。硬件、可用时间、语料授权和预算仍待确认。

> 来源：已确认问题 · 7c8e9f7999f140c2bd98e448b6bb0d86 · 2026-11-11T03:32:25.987732+00:00

- 【系统建议（未验证）】比较前需锁定同一公开语料快照与段落切分、同一查询集与 gold 判定、同一 k 与同一评价脚本；上述任一条件变化时，应重跑全部方法而不是只重跑其中一个。



## 方法与技术路线

- 【系统建议（未验证）】共享输入为语料快照、段落切分、查询集与 gold；BM25、DPR、BPR 各自建立并测量自己的索引，共用查询集与评估脚本。索引体积按各方法实际索引文件字节数分别记录，查询延迟按同一查询集逐条计时。

- 【文献依据（仅提供范围）】BPR 论文描述 BPR 采用两阶段流程：先用问题二值码与段落二值码的 Hamming 距离生成 top-l 候选，再用连续向量内积对候选重排并取 top-k。 [paper_0dda881c3109]

> 见证据 3 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering · 第 3 页



## 比较、验证或论证设计

- 【文献依据（仅提供范围）】DPR 论文在固定 k 下将 retriever 的 top-k retrieval accuracy 定义为：返回的候选集合 CF 中含有能回答问题的 span 的问题比例。 [paper_8f3858d2f42a]

> 见证据 4 · Dense Passage Retrieval for Open-Domain Question Answering · 第 2 页

- 【系统建议（未验证）】评价单位为问题：每个查询的 top-k 返回结果中至少包含一个 gold 相关段落即记为命中，命中率分母为查询总数；索引体积按各方法实际生成的索引文件字节数测量；查询延迟在同一查询集上逐条计时并报告均值与分位数；按段计的相关段落比例或端到端问答正确率不与按问题计命中率互换。



## 时间、资源与可行性

- 【待核实／待确定】硬件、可用时间、语料授权与预算均待确认；在确认前不能判断单机或任何具体设备是否足以建立 DPR/BPR 索引并完成延迟测量。

- 【系统建议（未验证）】可行性需通过三步核查：确认语料许可与查询集来源；估算 DPR/BPR 编码与索引构建所需的算力和存储；在与最终测量相同的软硬件环境下试跑小规模子集并记录峰值内存与延迟。



## 局限、未确认假设和待讨论事项

- 【系统建议（未验证）】本草案不生成实验结果，也不把任何方法的预期优势写成已取得结果；实际差异需在锁定条件下测量。

- 【待核实／待确定】各方法在段落切分粒度、索引实现、是否使用重排等细节上可能不同；若未统一或未记录，命中率、索引体积和延迟可能不可比。



## 参考来源

- 【文献依据（仅提供范围）】Karpukhin et al. (2020), Dense Passage Retrieval for Open-Domain Question Answering：本方案引用其摘要片段中关于稠密检索与 Lucene-BM25 的 top-20 段落检索准确率比较。 [paper_8f3858d2f42a]

> 见证据 1 · Dense Passage Retrieval for Open-Domain Question Answering

- 【文献依据（仅提供范围）】Yamada et al. (2021), Efficient Passage Retrieval with Hashing for Open-domain Question Answering：本方案引用其摘要片段中关于 BPR 用紧凑二值码表示段落索引的说明。 [paper_0dda881c3109]

> 见证据 2 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering



## 方法专属设计（计算研究）

### 数据与任务

任务为在固定公开段落语料和固定查询集上的段落检索比较：对每个查询，BM25、DPR、BPR 各自返回 top-k 段落；语料快照、段落切分、查询文本、gold 相关段落判定和 k 对所有方法一致。BM25 为稀疏词项检索，DPR 为稠密双编码器检索，BPR 为在 DPR 上加入二值哈希的检索方法。这里只规划，不执行。

### 基线

以 BM25 与 DPR 作为主要对照基线，BPR 作为待比较的哈希检索方法；三者使用同一语料、同一段落切分、同一查询集、同一 gold 判定与同一 k。BM25 的具体实现版本、DPR/BPR 的预训练权重和索引库版本待确认。

### 公平比较条件

每组比较共享语料、切分、查询、gold 和评估脚本；BM25、DPR、BPR 分别建立并测量自己的索引。报告三者的按问题计 Top-k 命中率、索引体积（字节）和查询延迟；不把不同方法的索引混用，也不把延迟与命中率合成单一分数。k 取值和是否报告多个 k 待确认。

### 预算与计算条件

计算预算与时间预算尚未确认。规划中需分别记录：语料预处理与切分耗时、各方法索引构建耗时与峰值内存、查询集逐条延迟；DPR/BPR 的编码与索引构建可能构成主要算力开销，需在确认硬件后估算。不声称任何具体设备足够。

### 评估方法

评价单位为问题：对每个查询，top-k 返回结果中至少包含一个 gold 相关段落即记为命中，命中率分母为查询总数。索引体积按各方法实际生成的索引文件字节数测量；查询延迟在同一查询集上逐条计时，报告均值与分位数。按段计的相关段落比例或端到端问答正确率不与按问题计命中率互换。

### 重复性与复现条件

固定并记录语料快照标识、段落切分脚本与参数、查询集与 gold 标注版本、k 值、各方法代码与依赖版本、BM25/DPR/BPR 的模型权重或参数、索引构建参数、随机种子（如适用）以及延迟测量所用硬件与软件环境。公开可再分发范围内提供脚本与配置；受许可限制的数据只记录获取方式。



## 已知条件与待确认

- 资源／设备：待确认（待核实／待确定）

- 期限：待确认（待核实／待确定）

- 预算：待确认（待核实／待确定）

- 数据或材料访问：待确认（待核实／待确定）



## 待讨论事项

- 可用硬件（CPU/GPU 型号、内存、存储）与是否可独占测量环境

- 可用时间与项目截止时间

- 计算/存储预算

- 公开语料的授权范围、可再分发性与获取方式

- 查询集来源、规模与语言/领域分布

- gold 相关段落判定由谁标注、标注粒度与一致性检查

- k 的取值与是否报告多个 k

- BM25 实现版本与参数

- DPR/BPR 预训练权重、索引库与近似最近邻配置

- 是否允许使用外部模型权重与下载

- 延迟测量口径（冷/热启动、并发、批大小）

- 是否只比较检索或还需接下游问答



参考论文 ID：paper_8f3858d2f42a, paper_0dda881c3109

## 文献原文快照

### 证据 1 · Dense Passage Retrieval for Open-Domain Question Answering

来源编号：ev-4e5201afed61a72474be135a

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

### 证据 2 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering

来源编号：ev-96e1efb179a8871bb9dc14ec

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

### 证据 3 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering · 第 3 页

来源编号：ev-ce79976839db7dee57773c8a

> sign function
> using the scaled tanh function during the training:
> ˜h = tanh(βe), (4)
> whereβ is a scaling parameter. Whenβ increases,
> the function gradually becomes non-smooth, and
> as β → ∞, it converges to the sign function.
> At each training step, we increase β by setting
> β =√γ·step + 1, where step is the number of
> ﬁnished training steps. We setγ = 0.1 and explain
> the effects of changing it in Appendix B.
> 3.3 Two-stage Approach
> To reduce the computational cost without losing
> accuracy, BPR splits the task into candidate genera-
> tion and reranking stages. At the candidate genera-
> tion stage, we efﬁciently obtain the top-l candidate
> passages using the Hamming distance between the
> binary code of question hq and that of each pas-
> sage, hp. We then rerank thel candidate passages
> using the inner product between the continuous
> embedding of question eq and hp and select the
> top-k passages from the reranked candidates. We
> perform candidate generation using binary code
> hq for search-time efﬁciency, and reranking using
> 

### 证据 4 · Dense Passage Retrieval for Open-Domain Question Answering · 第 2 页

来源编号：ev-37d0a00c9958c6dbde1f30fd

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