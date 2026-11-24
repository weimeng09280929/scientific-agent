# 同一公开语料与固定查询下的 BM25、DPR 与 BPR 段落检索比较（修订方法草案：延迟口径与 BPR 重排表示核查）

状态：未采纳讨论稿，非当前正式方案；依据待复核：False

项目名称：我想比较几种从公开资料里找段落的方法

研究问题：同一评估集的段落检索比较（验收）：在相同公开段落语料和固定查询集合上，BM25、DPR 与 BPR 的按问题计 Top-k 命中率、各自索引体积和查询延迟有何差异？这是流程验收的比较问题，尚未执行，未据此宣称原论文使用不同指标或存在新的研究空白。硬件、可用时间、语料授权和预算仍待确认。

方案类型：计算研究；版本：讨论稿，尚无正式版本；修订自：无

模板版本：stem-plan-v1

待确认：可用硬件（CPU/GPU 型号、内存、存储）与能否固定同一测量环境；可用时间与项目截止时间；计算/存储预算；公开语料的授权范围、可再分发性与获取方式；查询集来源、规模与语言/领域分布；gold 相关段落判定由谁标注、标注粒度与一致性检查；k 的取值；每查询重复次数 R、冷/热启动与预热策略；是否同时报告不含查询编码的索引检索延迟及其边界定义；BM25 实现版本与参数；DPR/BPR 预训练权重、索引库与近似最近邻配置；是否允许使用外部模型权重与下载；延迟测量并发与批大小；BPR 重排阶段查询和段落各自表示，需查全文或代码确认；是否只比较检索或还需接下游问答

修订自草案：e67f3e864724462a83627fd61fc8c187

参考来源：Dense Passage Retrieval for Open-Domain Question Answering (paper_8f3858d2f42a)；DOI：无

参考来源：Efficient Passage Retrieval with Hashing for Open-domain Question Answering (paper_0dda881c3109)；DOI：无

**方案类型：计算研究 · 模板 stem-plan-v1**



> 研究方案建议，尚未执行。结构完整不等于科学有效；采纳状态见记录标记。



修订自草案：e67f3e864724462a83627fd61fc8c187



## 研究目标与明确问题

- 【用户提供】在相同公开段落语料和固定查询集合上，BM25、DPR 与 BPR 的按问题计 Top-k 命中率、各自索引体积和查询延迟有何差异？这是流程验收的比较问题，尚未执行，未据此宣称原论文使用不同指标或存在新的研究空白。硬件、可用时间、语料授权和预算仍待确认。

> 来源：已确认问题 · 7c8e9f7999f140c2bd98e448b6bb0d86 · 2026-11-11T03:32:25.987732+00:00

- 【系统建议（未验证）】本次修订保留同一研究问题和 BM25、DPR、BPR 三组方法对照；补充延迟计时起止、是否包含查询编码、固定同一环境与 k、记录重复次数并分别报告中位数和 P95；BPR 重排表示在已提供片段不足处标为待核查。



## 相关工作与实际引用依据

- 【文献依据（仅提供范围）】DPR 论文摘要报告，其稠密检索在多个开放域问答数据集上以 top-20 段落检索准确率计，比强 Lucene-BM25 系统绝对高 9%–19%。 [paper_8f3858d2f42a]

> 见证据 1 · Dense Passage Retrieval for Open-Domain Question Answering

- 【文献依据（仅提供范围）】BPR 论文摘要报告，BPR 用紧凑二值码而非连续向量表示段落索引，并报告相较 DPR 将内存成本从 65GB 降至 2GB，且在两个标准开放域问答基准上无准确率损失。 [paper_0dda881c3109]

> 见证据 2 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering



## 拟研究的差异或待验证设想

- 【系统建议（未验证）】在语料快照、段落切分、查询集、gold 判定、k、软硬件环境和延迟口径完全相同的条件下，三种方法的按问题计 Top-k 命中率、索引体积和查询延迟差异才可按检索方法解释；延迟主口径为端到端并包含查询编码，另报不含查询编码的索引检索延迟。

- 【待核实／待确定】BM25、DPR 与 BPR 在该固定设定下的命中率、索引体积和延迟差异方向与幅度尚待实验确认。



## 研究对象、数据或材料条件

- 【用户提供】请修订草案，保留同一个研究问题和三组对照。说清楚延迟从什么时候开始计时，是否包括查询编码；固定同一环境和 k，记录重复次数，分别报告中位数和 P95。请核对 BPR 重排中查询和段落各用什么表示，原文不够就标为待核查。设备、时间、语料授权和预算仍待确认。

> 来源：本次用户消息 · message-e3534c62-d206-4ad5-9125-0a929346a7a9 · 2

- 【系统建议（未验证）】正式测量前锁定同一软硬件环境、同一 k、同一批查询与同一评价脚本；每个查询重复 R 次，分别报告中位数和 P95，并记录冷/热启动与预热策略；R、k、预热策略待确认。



## 方法与技术路线

- 【系统建议（未验证）】共享输入为语料快照、段落切分、查询集与 gold；BM25、DPR、BPR 各自建立并测量自己的索引，共用查询集与评估脚本。延迟主口径从查询字符串进入检索接口前开始计时，包含分词/查询编码、候选生成与重排，到返回 top-k 停止；次口径从查询表示就绪后开始，不含查询编码。索引体积按各方法实际索引文件字节数分别记录。

- 【文献依据（仅提供范围）】BPR 论文摘要报告，BPR 采用二值码候选生成与连续向量重排的两阶段流程。 [paper_0dda881c3109]

> 见证据 2 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering



## 比较、验证或论证设计

- 【文献依据（仅提供范围）】DPR 论文正文将固定 k 下的 top-k retrieval accuracy 定义为：返回候选集合 CF 中含有能回答问题 span 的问题比例。 [paper_8f3858d2f42a]

> 见证据 3 · Dense Passage Retrieval for Open-Domain Question Answering · 第 2 页

- 【系统建议（未验证）】评价单位为查询：每个查询的 top-k 返回结果中至少包含一个 gold 相关段落即记为命中，分母为查询总数；索引体积按各方法索引文件字节数测量；查询延迟每查询重复 R 次，报告每查询中位数和 P95 并汇总总体中位数/P95，主口径含查询编码、次口径不含；按段计相关比例或端到端问答正确率不与按问题计命中率互换。k 与 R 待确认。



## 时间、资源与可行性

- 【待核实／待确定】硬件、可用时间、语料授权与预算均待确认；在确认前不能判断单机或任何具体设备是否足以建立 DPR/BPR 索引并完成同一环境下的延迟测量。

- 【系统建议（未验证）】可行性需核查：确认语料许可与查询集来源；估算 DPR/BPR 编码与索引构建算力和存储；在与最终测量相同的软硬件环境试跑小规模子集，记录峰值内存、冷/热启动和每查询重复计时分布。



## 局限、未确认假设和待讨论事项

- 【系统建议（未验证）】本草案不生成实验结果，不把任何方法的预期优势写成已取得结果；实际差异需在锁定条件下测量。

- 【待核实／待确定】已提供片段未明确 BPR 重排阶段查询和段落各自采用何种表示，也未说明 BPR 表1查询时间的计时起点、是否含查询编码与重复次数；这些均列为待核查。



## 参考来源

- 【文献依据（仅提供范围）】Karpukhin et al. (2020), Dense Passage Retrieval for Open-Domain Question Answering：本方案引用其摘要中关于 top-20 段落检索准确率比 Lucene-BM25 绝对高 9%–19% 的报告，并在验证章节单独引用其正文 top-k 定义。 [paper_8f3858d2f42a]

> 见证据 1 · Dense Passage Retrieval for Open-Domain Question Answering

- 【文献依据（仅提供范围）】Yamada et al. (2021), Efficient Passage Retrieval with Hashing for Open-domain Question Answering：本方案引用其摘要中关于二值码段落索引、二值码候选生成、连续向量重排与内存成本 65GB 到 2GB 的报告；表1查询时间与重排表示细节在已提供片段中不足，列为待核查。 [paper_0dda881c3109]

> 见证据 2 · Efficient Passage Retrieval with Hashing for Open-domain Question Answering



## 方法专属设计（计算研究）

### 数据与任务

任务为在固定公开段落语料和固定查询集上的段落检索比较：对每个查询，BM25、DPR、BPR 各自返回 top-k 段落；三方法共享语料快照、段落切分、查询文本、gold 相关段落判定和 k。这里只规划，不执行。

### 基线

以 BM25 与 DPR 作为主要对照基线，BPR 作为待比较方法；三组对照为 BM25–DPR、BM25–BPR、DPR–BPR，均共享语料、切分、查询、gold 与评价脚本，各方法分别建立并测量自己的索引。BM25 实现版本、DPR/BPR 权重和索引库版本待确认。

### 公平比较条件

每组对照共享输入与评价脚本；BM25、DPR、BPR 分别建立并测量自己的索引。报告三者的按问题计 Top-k 命中率、索引体积（字节）、端到端延迟（从查询字符串进入检索接口前开始，包含查询编码）和不含查询编码的索引检索延迟；不把不同方法的索引混用，也不把延迟与命中率合成单一分数。k 取值待确认。

### 预算与计算条件

计算预算与时间预算尚未确认。规划中需分别记录：语料预处理与切分耗时、各方法索引构建耗时与峰值内存、每查询重复 R 次计时的总体中位数和 P95；不声称任何具体设备足够。

### 评估方法

评价单位为查询，分母为查询总数；top-k 返回结果中至少包含一个 gold 相关段落即记为命中。索引体积按各方法实际索引文件字节数测量。延迟主口径从查询字符串进入检索接口前开始计时，包含分词/查询编码、候选生成与重排，到返回 top-k 停止；次口径从查询表示就绪后开始，不含查询编码。每查询重复 R 次，报告每查询中位数与 P95，并汇总总体中位数/P95。按段计相关比例或端到端问答正确率不与按问题计命中率互换。k 与 R 待确认。

### 重复性与复现条件

固定并记录语料快照标识、段落切分脚本与参数、查询集与 gold 标注版本、k、每查询重复次数 R、冷/热启动与预热策略、各方法代码与依赖版本、BM25 参数、DPR/BPR 模型权重与索引构建参数、随机种子（如适用）以及延迟测量所用软硬件环境。公开可再分发范围内提供脚本与配置；受许可限制的数据只记录获取方式。



## 已知条件与待确认

- 资源／设备：待确认（待核实／待确定）

- 期限：待确认（待核实／待确定）

- 预算：待确认（待核实／待确定）

- 数据或材料访问：待确认（待核实／待确定）



## 待讨论事项

- 可用硬件（CPU/GPU 型号、内存、存储）与能否固定同一测量环境

- 可用时间与项目截止时间

- 计算/存储预算

- 公开语料的授权范围、可再分发性与获取方式

- 查询集来源、规模与语言/领域分布

- gold 相关段落判定由谁标注、标注粒度与一致性检查

- k 的取值

- 每查询重复次数 R、冷/热启动与预热策略

- 是否同时报告不含查询编码的索引检索延迟及其边界定义

- BM25 实现版本与参数

- DPR/BPR 预训练权重、索引库与近似最近邻配置

- 是否允许使用外部模型权重与下载

- 延迟测量并发与批大小

- BPR 重排阶段查询和段落各自表示，需查全文或代码确认

- 是否只比较检索或还需接下游问答



参考论文 ID：paper_8f3858d2f42a, paper_0dda881c3109

## 文献原文快照

### 证据 1 · Dense Passage Retrieval for Open-Domain Question Answering

来源编号：ev-1f832a5259af8c2cb7caf213

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

来源编号：ev-339f4306905b3bf50f4eba35

>  2020) to represent
> the passage index using compact binary codes
> rather than continuous vectors. BPR is trained
> with a multi-task objective over two tasks: ef-
> ﬁcient candidate generation based on binary
> codes and accurate reranking based on con-
> tinuous vectors. Compared with DPR, BPR
> substantially reduces the memory cost from
> 65GB to 2GB without a loss of accuracy on
> two standard open-domain question answering
> benchmarks: Natural Questions and TriviaQA.
> Our code and trained models are available
> at https://github.com/studio-ousia/
> bpr.
> 1 Introduction
> Open-domain question answering (QA) is the task
> of answering arbitrary factoid questions based on a
> knowledge source (e.g., Wikipedia). Recent state-
> of-the-art QA models are typically based on a two-
> stage retriever–reader approach (Chen et al., 2017)
> using a retriever that obtains a small number of
> relevant passages from a large knowledge source
> and a reader that processes these passages to pro-
> duce an a

### 证据 3 · Dense Passage Retrieval for Open-Domain Question Answering · 第 2 页

来源编号：ev-f6f183d4cf0e7aaca581b37f

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