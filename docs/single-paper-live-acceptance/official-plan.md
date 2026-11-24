项目名称：这是隔离测试

研究问题：候选研究问题：冻结无监督 SimCSE 与词面重叠基线的排序一致性比较：在公开预训练权重可用、且预先固定来源、样本量与筛选规则的同一批带人工相似度评分的英文句对上，比较冻结的无监督 SimCSE（仅加载权重做前向推理，不更新参数）与一个明确设定的词面重叠基线（例如基于词元集合的 Jaccard 相似度）所产生的句对相似度排序，哪一种与人工评分排序更一致？评价以 Spearman 秩相关为主指标，并报告样本量与不确定性；数据来源、规模和词面基线的具体定义待确认。

方案类型：计算研究；版本：1；替代的正式方案：无

模板版本：stem-plan-v1

待确认：带人工相似度评分的英文句对来源、授权状态及能否凑足低/中/高各 20 对待确认；人工评分的量纲、低/中/高区间切点及重复标注与标注者一致性处理待确认；公开无监督 SimCSE 权重标识、版本及可用性待确认；词面重叠基线的分词、大小写、标点、停用词和并列分数处理待确认；常量相似度出现时的报告格式及是否仍纳入总体分母待确认；Spearman 不确定性的重采样或检验方案、置信区间口径及最小有意义差值待确认；CPU 推理耗时、内存和存储需求待核实；是否固定使用余弦相似度以及模型池化与预处理细节待确认；数据缺失、句对重复或无效文本的处理规则待确认；分层抽样后总体 Spearman 与分层 Spearman 的报告口径待确认；区间样本不足时是否保持各区间 20 对目标不变待确认

修订来源草案：6520817deccc43a78fb1f68ec71ca3eb

参考来源：SimCSE: Simple Contrastive Learning of Sentence Embeddings (paper_b1c8fd1d238b)；DOI：无

# 修订草案：冻结无监督 SimCSE 与词面重叠基线在最多 60 个英文句对上的排序一致性比较

项目：proj_fb6108a0e35d5354b6381085001be3a2

记录：f8791bf164984cbeb132a66caa39b1ec · 材料版本：3

状态：当前正式方案，可审阅，尚未验证；active；需要复核：False

> 方案保存不代表实验已执行或结论得到验证。原文中的草稿、教学、预期与历史标记均保留。

**方案类型：计算研究 · 模板 stem-plan-v1**



> 研究方案建议，尚未执行。结构完整不等于科学有效；采纳状态见记录标记。



修订自草案：6520817deccc43a78fb1f68ec71ca3eb



## 研究目标与明确问题

- 【用户提供】在公开预训练权重可用、且预先固定来源、样本量与筛选规则的同一批带人工相似度评分的英文句对上，比较冻结的无监督 SimCSE（仅加载权重做前向推理，不更新参数）与一个明确设定的词面重叠基线（例如基于词元集合的 Jaccard 相似度）所产生的句对相似度排序，哪一种与人工评分排序更一致？评价以 Spearman 秩相关为主指标，并报告样本量与不确定性；数据来源、规模和词面基线的具体定义待确认。

> 来源：已确认问题 · f1163c8faf3c47869a588d4ed7c8db4d · 2026-11-12T10:01:41.623356+00:00

- 【系统建议（未验证）】本草案将已确认问题收敛为小规模计算研究设计：在同一批最多 60 个带人工相似度评分的英文句对上，按低、中、高三个评分区间各计划 20 对，比较冻结无监督 SimCSE 与词元集合 Jaccard 基线的相似度排序与人工评分排序的一致性；不增加模型，不训练或执行实验。



## 相关工作与实际引用依据

- 【文献依据（仅提供范围）】该文报告 dropout 作为最小数据增强，移除它会导致表示坍缩。 [paper_b1c8fd1d238b]

> 见证据 1 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 1 页

- 【文献依据（仅提供范围）】该文在标准语义文本相似度（STS）任务上评价 SimCSE，并以 Spearman 相关报告结果。 [paper_b1c8fd1d238b]

> 见证据 1 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 1 页



## 拟研究的差异或待验证设想

- 【系统建议（未验证）】拟检验的方向性假设是：在同一批最多 60 个句对和同一人工评分上，冻结无监督 SimCSE 的余弦相似度排序与人工评分的 Spearman 秩相关高于词元集合 Jaccard 相似度的排序；该假设尚未验证，结果也可能显示无差异或方向相反。

- 【待核实／待确定】按低、中、高三个评分区间各 20 对分层后，区间内样本量可能偏小，Spearman 估计的不确定性可能较大；最小有意义差值尚未确认。



## 研究对象、数据或材料条件

- 【用户提供】隔离测试背景：计划仅使用普通 CPU 和 Python，不训练或微调模型；数据与模型权重的可用性待核实。

> 来源：项目背景 · resources · 1

- 【用户提供】隔离测试背景：计划一周内完成设计，当前不执行实验。

> 来源：项目背景 · time_budget · 1



## 方法与技术路线

- 【用户提供】请修订原草案：将上限从 100 个英文句对收缩为 60 个，按人工评分低、中、高三个区间各计划 20 对，区间样本不足时报告实际数量；两种方法使用同一批句对，Spearman 采用平均秩处理并列并说明常量向量不可计算。保留已确认的问题、两种比较方法和待核实条件，不增加模型，不训练或执行实验。 请继续将原文直接事实和本项目的拟议设计分成独立陈述，论文依据只用于支持其直接陈述。

> 来源：本次用户消息 · message-80ac7fc0-fe50-4c45-ace4-e44f495916b4 · 2

- 【系统建议（未验证）】拟在同一批最多 60 个句对上按人工评分低、中、高区间各计划 20 对；两种方法共享同一批句对，分别计算冻结无监督 SimCSE 余弦相似度和词元集合 Jaccard 相似度，再按配对样本计算 Spearman 秩相关，并列采用平均秩；若某方法在全部句对上产生常量相似度（例如句向量为常量向量），则 Spearman 不可计算并如实报告。



## 比较、验证或论证设计

- 【系统建议（未验证）】验证计划报告总体 Spearman、各评分区间实际句对数、并列处理方式（平均秩）、常量相似度不可计算的情况、缺失值和无效文本处理；评价单位为句对，分母为具有人工评分且两种方法均产生有效相似度的句对数，不引入 top-k 命中率。

- 【待核实／待确定】不确定性估计的重采样或检验方案、置信区间口径、低/中/高区间切点及最小有意义差值尚未确认。



## 时间、资源与可行性

- 【用户提供】隔离测试背景：计划仅使用普通 CPU 和 Python，不训练或微调模型；数据与模型权重的可用性待核实。

> 来源：项目背景 · resources · 1

- 【待核实／待确定】公开模型权重和带人工评分句对是否可用、CPU 单对推理耗时与内存是否满足需求均待核实；确认前不能声称现有设备足够。



## 局限、未确认假设和待讨论事项

- 【系统建议（未验证）】最多 60 个句对且按三个评分区间各计划 20 对分层，样本量小，Spearman 估计的不确定性较大；结果仅针对预先固定的数据来源、区间切点和筛选规则，不能外推到其他领域或更大规模。

- 【待核实／待确定】低、中、高评分区间的切点、区间样本不足时的报告格式以及分层后整体与分层 Spearman 的报告口径尚未确认。



## 参考来源

- 【文献依据（仅提供范围）】该文正文开头说明代码与预训练模型公开可用，并给出项目链接。 [paper_b1c8fd1d238b]

> 见证据 2 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 1 页

- 【系统建议（未验证）】本草案文献依据仅限本轮提供的 SimCSE 正文开头摘录和三段已保存片段，未使用全文；具体页码、完整实验设置和例外情况待核查。



## 方法专属设计（计算研究）

### 数据与任务

拟使用同一批带人工相似度评分的英文句对，最多 60 对，按人工评分低、中、高三个区间各计划 20 对；区间样本不足时报告实际数量。每对分别计算冻结无监督 SimCSE 余弦相似度与词元集合 Jaccard 相似度，并与人工评分做配对 Spearman 秩相关。数据与权重可用性待核实，不执行实验、不训练或微调模型。

### 基线

词面重叠基线为基于词元集合的 Jaccard 相似度；需预先规定分词、大小写、标点、停用词处理及并列分数规则，不训练任何模型。

### 公平比较条件

两种方法使用同一批最多 60 个句对、同一人工评分和同一评价脚本；共享句对、人工评分与评估流程，各自独立产生相似度分数与排序；不增加模型，不调参，不引入 top-k。报告整体 Spearman 差值及分层实际样本量。

### 预算与计算条件

仅使用普通 CPU 和 Python，不训练或微调模型；设计阶段计划一周内完成，当前不执行实验。实际算力、内存和存储消耗待核实。

### 评估方法

评价单位为句对；分母为具有人工评分且两种方法均产生有效相似度的句对数。将每种方法的相似度与人工评分分别排序后计算配对 Spearman 秩相关，并列采用平均秩；若某方法在全部句对上产生常量相似度，则 Spearman 不可计算并报告。报告总体与各评分区间实际句对数、缺失和无效文本处理；不按 top-k 命中率评价。

### 重复性与复现条件

记录数据来源与版本、低/中/高区间切点与筛选规则、模型权重标识与版本、推理设置、分词与 Jaccard 规则、并列与常量处理、评估脚本；在获得授权与可用资源后由独立环境复跑核对。



## 已知条件与待确认

- 资源／设备：隔离测试背景：计划仅使用普通 CPU 和 Python，不训练或微调模型；数据与模型权重的可用性待核实。（用户提供）

- 期限：隔离测试背景：计划一周内完成设计，当前不执行实验。（用户提供）

- 预算：待确认（待核实／待确定）

- 数据或材料访问：待确认（待核实／待确定）



## 待讨论事项

- 带人工相似度评分的英文句对来源、授权状态及能否凑足低/中/高各 20 对待确认

- 人工评分的量纲、低/中/高区间切点及重复标注与标注者一致性处理待确认

- 公开无监督 SimCSE 权重标识、版本及可用性待确认

- 词面重叠基线的分词、大小写、标点、停用词和并列分数处理待确认

- 常量相似度出现时的报告格式及是否仍纳入总体分母待确认

- Spearman 不确定性的重采样或检验方案、置信区间口径及最小有意义差值待确认

- CPU 推理耗时、内存和存储需求待核实

- 是否固定使用余弦相似度以及模型池化与预处理细节待确认

- 数据缺失、句对重复或无效文本的处理规则待确认

- 分层抽样后总体 Spearman 与分层 Spearman 的报告口径待确认

- 区间样本不足时是否保持各区间 20 对目标不变待确认



参考论文 ID：paper_b1c8fd1d238b

关联记录：f1163c8faf3c47869a588d4ed7c8db4d

引用论文：paper_b1c8fd1d238b

## 文献原文快照

### 证据 1 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 1 页

来源编号：ev-68e9ffa5f2f4d232ba40a490

> -
> forming on par with previous supervised coun-
> terparts. We ﬁnd that dropout acts as mini-
> mal data augmentation and removing it leads
> to a representation collapse. Then, we pro-
> pose a supervised approach, which incorpo-
> rates annotated pairs from natural language
> inference datasets into our contrastive learn-
> ing framework, by using “entailment” pairs
> as positives and “contradiction” pairs as hard
> negatives. We evaluate SimCSE on standard
> semantic textual similarity (STS) tasks, and
> our unsupervised and supervised models using
> BERTbase achieve an average of 76.3% and
> 81.6% Spearman’s correlation respectively, a
> 4.2% and 2.2% improvement compared to
> previous best results. We also show—both
> theoretically and empirically—that contrastive
> 

### 证据 2 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 1 页

来源编号：ev-d4a3a937c5264714d303ad5d

> 6.3% and
> 81.6% Spearman’s correlation respectively, a
> 4.2% and 2.2% improvement compared to
> previous best results. We also show—both
> theoretically and empirically—that contrastive
> learning objective regularizes pre-trained em-
> beddings’ anisotropic space to be more uni-
> form, and it better aligns positive pairs when
> supervised signals are available.1
> 1 Introduction
> Learning universal sentence embeddings is a fun-
> damental problem in natural language process-
> ing and has been studied extensively in the litera-
> ture (Kiros et al., 2015; Hill et al., 2016; Conneau
> et al., 2017; Logeswaran and Lee, 2018; Cer et al.,
> 2018; Reimers and Gurevych, 2019, inter alia ).
> In this work, we advance state-of-the-art sentence
> *The ﬁrst two authors contributed equally (listed in alpha-
> betical order). This work was done when Xingcheng visited
> the Princeton NLP group remotely.
> 1Our code and pre-trained models are publicly available at
> https://github.com/princeton-nlp/SimCSE.
> embedding methods and demonstrate that a con-
> 