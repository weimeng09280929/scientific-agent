# 冻结无监督 SimCSE 与词面重叠基线在英文句对相似度排序上的小规模计算研究草案

状态：未采纳讨论稿，非当前正式方案；依据待复核：False

项目名称：这是隔离测试

研究问题：候选研究问题：冻结无监督 SimCSE 与词面重叠基线的排序一致性比较：在公开预训练权重可用、且预先固定来源、样本量与筛选规则的同一批带人工相似度评分的英文句对上，比较冻结的无监督 SimCSE（仅加载权重做前向推理，不更新参数）与一个明确设定的词面重叠基线（例如基于词元集合的 Jaccard 相似度）所产生的句对相似度排序，哪一种与人工评分排序更一致？评价以 Spearman 秩相关为主指标，并报告样本量与不确定性；数据来源、规模和词面基线的具体定义待确认。

方案类型：计算研究；版本：讨论稿，尚无正式版本；替代的正式方案：无

模板版本：stem-plan-v1

待确认：带人工相似度评分的英文句对来源、规模（不超过 100 对）、筛选规则和授权状态待确认；公开无监督 SimCSE 权重标识、版本及可用性待确认；词面重叠基线的分词、大小写、标点、停用词和并列分数处理待确认；人工评分的量纲、重复标注与标注者一致性处理待确认；Spearman 不确定性的重采样或检验方案、置信区间口径及最小有意义差值待确认；CPU 推理耗时、内存和存储需求待核实；是否固定使用余弦相似度以及模型池化与预处理细节待确认；数据缺失、句对重复或无效文本的处理规则待确认

参考来源：SimCSE: Simple Contrastive Learning of Sentence Embeddings (paper_b1c8fd1d238b)；DOI：无

**方案类型：计算研究 · 模板 stem-plan-v1**



> 研究方案建议，尚未执行。结构完整不等于科学有效；采纳状态见记录标记。



## 研究目标与明确问题

- 【用户提供】在公开预训练权重可用、且预先固定来源、样本量与筛选规则的同一批带人工相似度评分的英文句对上，比较冻结的无监督 SimCSE（仅加载权重做前向推理，不更新参数）与一个明确设定的词面重叠基线（例如基于词元集合的 Jaccard 相似度）所产生的句对相似度排序，哪一种与人工评分排序更一致？评价以 Spearman 秩相关为主指标，并报告样本量与不确定性；数据来源、规模和词面基线的具体定义待确认。

> 来源：已确认问题 · f1163c8faf3c47869a588d4ed7c8db4d · 2026-11-12T10:01:41.623356+00:00

- 【用户提供】请为刚确认的研究问题写一个小规模计算研究草案。只规划在同一批有人工相似度评分的英文句对上对比冻结的无监督 SimCSE 和词面重叠基线，以 Spearman 秩相关为主指标。最多 100 个句对，不训练或微调。按已保存背景区分条件和待确认事项，数据与权重的可用性待核实，不执行实验，不预填结果。 论文陈述只写选段直接报告的 SimCSE 方法或评价，不抄论文成绩，不在同一条写本项目建议。本项目如何做另列为拟议设计。Spearman 按配对样本的秩计算，不引入 top-k。

> 来源：本次用户消息 · message-b61e264b-1269-4dbc-9fa5-e3f022729c1c · 2



## 相关工作与实际引用依据

- 【文献依据（仅提供范围）】该文报告无监督 SimCSE 依赖 dropout 噪声，且使用默认 dropout 概率 p=0.1 的设置优于其测试的其他 dropout 设置。 [paper_b1c8fd1d238b]

> 见证据 1 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 4 页

- 【文献依据（仅提供范围）】该文报告从预训练检查点开始对初始对齐很重要，并且无监督 SimCSE 在使用 dropout 噪声时保持较稳定的对齐。 [paper_b1c8fd1d238b]

> 见证据 2 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 4 页



## 拟研究的差异或待验证设想

- 【系统建议（未验证）】拟检验的方向性假设是：在同一批句对和同一人工评分上，冻结无监督 SimCSE 的余弦相似度排序与人工评分的 Spearman 秩相关高于词元集合 Jaccard 相似度的排序；该假设尚未验证，结果也可能显示无差异或方向相反。

- 【待核实／待确定】若两种方法的 Spearman 差值很小，当前小样本量可能无法区分；最小有意义差值尚未确认。



## 研究对象、数据或材料条件

- 【用户提供】隔离测试背景：计划仅使用普通 CPU 和 Python，不训练或微调模型；数据与模型权重的可用性待核实。

> 来源：项目背景 · resources · 1

- 【用户提供】隔离测试背景：计划一周内完成设计，当前不执行实验。

> 来源：项目背景 · time_budget · 1



## 方法与技术路线

- 【用户提供】请为刚确认的研究问题写一个小规模计算研究草案。只规划在同一批有人工相似度评分的英文句对上对比冻结的无监督 SimCSE 和词面重叠基线，以 Spearman 秩相关为主指标。最多 100 个句对，不训练或微调。按已保存背景区分条件和待确认事项，数据与权重的可用性待核实，不执行实验，不预填结果。

> 来源：历史用户消息 · dba2be4d4d474f008dae43fa2c382dda · 2

- 【系统建议（未验证）】拟在同一批最多 100 个句对上分别产生冻结无监督 SimCSE 的余弦相似度和词元集合 Jaccard 相似度，再按配对样本计算 Spearman 秩相关；不引入 top-k 评价。



## 比较、验证或论证设计

- 【系统建议（未验证）】验证计划报告主指标 Spearman 秩相关、两种方法的差值、样本量及不确定性，并检查缺失值和并列分数对结果的影响。

- 【待核实／待确定】不确定性估计的重采样或置换方案、置信区间口径以及最小有意义差值尚未确认。



## 时间、资源与可行性

- 【用户提供】隔离测试背景：计划仅使用普通 CPU 和 Python，不训练或微调模型；数据与模型权重的可用性待核实。

> 来源：项目背景 · resources · 1

- 【待核实／待确定】公开模型权重和带人工评分句对是否可用、CPU 单对推理耗时与内存是否满足需求均待核实；确认前不能声称现有设备足够。



## 局限、未确认假设和待讨论事项

- 【系统建议（未验证）】最多 100 个句对的小样本会使 Spearman 估计的不确定性较大，结果仅针对预先固定的数据来源和筛选规则，不能外推到其他领域或更大规模。

- 【系统建议（未验证）】本草案不覆盖监督 SimCSE、其他词面基线或句子编码器超参数调优；数据与权重未核实前，方案完整不等于科学有效或可行。



## 参考来源

- 【文献依据（仅提供范围）】该文附录讨论 STS 评价设置的差异，包括是否使用额外回归器、报告指标和结果聚合方式。 [paper_b1c8fd1d238b]

> 见证据 3 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 13 页

- 【系统建议（未验证）】本草案的文献依据仅限本轮提供的 SimCSE 正文开头摘录和已保存片段，未使用全文；具体页码、完整实验设置和例外情况待核查。



## 方法专属设计（计算研究）

### 数据与任务

拟使用同一批带人工相似度评分的英文句对，最多 100 对，预先固定来源、筛选规则和缺失处理；每对分别计算冻结无监督 SimCSE 余弦相似度与词元集合 Jaccard 相似度，并与人工评分做配对 Spearman 秩相关。数据与权重可用性待核实，不执行实验。

### 基线

词面重叠基线明确为基于词元集合的 Jaccard 相似度；需预先规定分词、大小写、标点、停用词处理以及并列分数的处理规则，且不训练任何模型。

### 公平比较条件

两种方法在同一批句对、同一人工评分和同一评价口径下比较；共享句对、人工评分和评估脚本，各自独立产生相似度分数与排序；报告 Spearman 差值及不确定性，不引入 top-k。

### 预算与计算条件

仅使用普通 CPU 和 Python，不训练或微调模型；一周内完成设计，当前不执行实验。实际算力、内存和存储消耗待核实。

### 评估方法

主指标为配对样本的 Spearman 秩相关：对同一批句对，将每种方法的相似度分数与人工评分分别排序后计算秩相关；报告样本量、缺失与并列处理及不确定性；不按 top-k 命中率评价。

### 重复性与复现条件

记录数据来源与版本、句对筛选规则、模型权重标识与版本、推理设置、分词与 Jaccard 规则、随机种子（如涉及）和评估脚本；在获得授权与可用资源后由独立环境复跑核对。



## 已知条件与待确认

- 资源／设备：隔离测试背景：计划仅使用普通 CPU 和 Python，不训练或微调模型；数据与模型权重的可用性待核实。（用户提供）

- 期限：隔离测试背景：计划一周内完成设计，当前不执行实验。（用户提供）

- 预算：待确认（待核实／待确定）

- 数据或材料访问：待确认（待核实／待确定）



## 待讨论事项

- 带人工相似度评分的英文句对来源、规模（不超过 100 对）、筛选规则和授权状态待确认

- 公开无监督 SimCSE 权重标识、版本及可用性待确认

- 词面重叠基线的分词、大小写、标点、停用词和并列分数处理待确认

- 人工评分的量纲、重复标注与标注者一致性处理待确认

- Spearman 不确定性的重采样或检验方案、置信区间口径及最小有意义差值待确认

- CPU 推理耗时、内存和存储需求待核实

- 是否固定使用余弦相似度以及模型池化与预处理细节待确认

- 数据缺失、句对重复或无效文本的处理规则待确认



参考论文 ID：paper_b1c8fd1d238b

## 文献原文快照

### 证据 1 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 4 页

来源编号：ev-736c7db475b243a06ac599de

> 6897
> p 0.0 0.01 0.05 0.1
> STS-B 71.1 72.6 81.1 82.5
> p 0.15 0.2 0.5 Fixed 0.1
> STS-B 81.4 80.5 71.0 43.6
> Table 3: Effects of different dropout probabilities p
> on the STS-B development set (Spearman’s correlation,
> BERTbase). Fixed 0.1: default 0.1 dropout rate but ap-
> ply the same dropout mask on bothxi andx+i .
> h = fθ(g(x),z ) andg is a (random) discrete op-
> erator onx. We note that even deleting one word
> would hurt performance and none of the discrete
> augmentations outperforms dropout noise.
> We also compare this self-prediction training
> objective to the next-sentence objective used in Lo-
> geswaran and Lee (2018), taking either one encoder
> or two independent encoders. As shown in Table 2,
> we ﬁnd that SimCSE performs much better than
> the next-sentence objectives (82.5 vs 67.4 on STS-
> B) and using one encoder instead of two makes a
> signiﬁcant difference in our approach.
> Why does it work? To further understand the
> role of dropout noise in unsupervised SimCSE, we
> try out different dropout rates in Table 3 and ob-
> serve that all the variants underperform the default
> dropout probability p = 0.1 from Transformers.
> We ﬁnd two extreme cases particularly interesting:
> “no dropout” (p = 0) and “ﬁxed 0.1” (using default
> dropoutp = 0.1 but the same dropout masks for
> the pair). In both cases, the resulting embeddings
> for the pair are exactly the same, and it leads to
> a dramatic performance degradation. We take the
> checkpoints of these models every 10 steps during
> training and visualize the alignment and uniformity
> metrics3 in Figure 2, along with a simple data aug-
> 

### 证据 2 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 4 页

来源编号：ev-25ca3d25df146a1577885b39

>  degradation. We take the
> checkpoints of these models every 10 steps during
> training and visualize the alignment and uniformity
> metrics3 in Figure 2, along with a simple data aug-
> mentation model “delete one word”. As clearly
> shown, starting from pre-trained checkpoints, all
> models greatly improve uniformity. However, the
> alignment of the two special variants also degrades
> drastically, while our unsupervised SimCSE keeps
> a steady alignment, thanks to the use of dropout
> noise. It also demonstrates that starting from a pre-
> trained checkpoint is crucial, for it provides good
> initial alignment. At last, “delete one word” im-
> proves the alignment yet achieves a smaller gain
> on the uniformity metric, and eventually underper-
> forms unsupervised SimCSE.
> 3We take STS-B pairs with a score higher than 4 asppos
> and all STS-B sentences aspdata.
> −2.6 −2.4 −2.2 −2.0 −1.8 −1.6
> 8nifoUmity
> 0.200
> 0.225
> 0.250
> 0.275
> 0.300
> 0.325
> 0.350
> 0.375
> 0.400Alignment
> Fixed 0.1
> 1o dUoSout
> Delete one woUd
> 8nsuS. 6imC6E
>  
>  
> No dropout
> Delete one word
> Unsup. SimCSE
> 

### 证据 3 · SimCSE: Simple Contrastive Learning of Sentence Embeddings · 第 13 页

来源编号：ev-d7d881c4ec89216cd255e09a

> ce (ablation study in §6.3).
> Finally, we introduce one more optional variant
> which adds a masked language modeling (MLM)
> objective (Devlin et al., 2019) as an auxiliary loss
> to Eq. 1: 𝓁 +λ·𝓁mlm (λ is a hyperparameter).
> This helps SimCSE avoid catastrophic forgetting
> of token-level knowledge. As we will show in Ta-
> ble D.2, we ﬁnd that adding this term can help
> improve performance on transfer tasks (not on
> sentence-level STS tasks).
> B Different Settings for STS Evaluation
> We elaborate the differences in STS evaluation set-
> tings in previous work in terms of (a) whether to
> use additional regressors; (b) reported metrics; (c)
> different ways to aggregate results.
> Additional regressors. The default SentEval
> implementation applies a linear regressor on top of
> Paper Reg. Metric Aggr.
> Hill et al. (2016) Both all
> Conneau et al. (2017) ✓ Pearson mean
> Conneau and Kiela (2018) ✓ Pearson mean
> Reimers and Gurevych (2019) Spearman all
> Zhang et al. (2020) Spearman all
> Li et al. (2020) Spearman wmean
> Su et al. (2021) Spearman wmean
> Wieting et al.