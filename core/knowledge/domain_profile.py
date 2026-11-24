"""理工科研究领域画像；与研究方法独立，旧非开放画像仅供历史读取。

设计动机
--------
系统原有实现把「材料科学」硬编码进了抽取 schema 与多处 prompt
（材料-性能-合成三元组、推荐材料体系、材料领域 Gap…），导致流程只能跑材料方向。

本模块把「领域知识」外置为可配置画像：每个研究领域只需要声明三件事——

    研究对象（entity）    ：这个领域在研究「什么」（材料体系 / 细胞系 / 算法…）
    评价指标（metric）    ：用什么数字衡量好坏（ZT / IC50 / Accuracy…）
    方法条件（method）    ：怎么得到它（合成工艺 / 实验方案 / 训练配置…）

节点在构造 prompt 时按领域取对应描述，材料只是其中一个预设值，
现行开放范围以 core/research_scope.py 为准。保留旧画像不代表开放临床或人体研究。

用法
----
    from core.knowledge.domain_profile import get_domain_profile, build_extraction_system_prompt

    profile = get_domain_profile("biology")
    prompt = build_extraction_system_prompt("biology")
"""

from __future__ import annotations

from typing import Optional
from core.research_scope import (
    SCOPE_NOTICE,
    is_public_domain,
    PUBLIC_DOMAINS,
    DOMAIN_ALIASES,
)

# ===== 领域画像预设 =====
# 三个字段 entity / metric / method 直接决定抽取 prompt 的语义：
#   entity —— 研究对象说明（含典型例子，帮助 LLM 对齐粒度）
#   metric —— 评价指标说明（含典型指标名）
#   method —— 方法/条件说明（含典型工艺或实验参数）
DOMAIN_PROFILES: dict[str, dict[str, str]] = {
    "engineering": {
        "label": "工程与技术",
        "core_topics": "工程系统建模、设计约束、可靠性、控制与测量规划",
        "entity": "工程系统、部件或模型，区分设计对象与已有设备条件",
        "metric": "按方法使用性能、可靠性或误差指标；理论方案使用适用条件与论证判据，不强填数值",
        "method": "依据问题选择计算、理论或实验研究设计；只摘取已有参数，不给未经支持的操作指导",
        "topic_hint": "明确工程对象、设计约束、比较或论证方法与可行性",
    },
    "mathematics": {
        "label": "数学",
        "core_topics": "数学对象、定义、命题、结构、证明、构造与反例",
        "entity": "数学对象、假设、命题及其适用域",
        "metric": "论证的适用条件、证明义务、反例和边界；数值研究才使用相应误差，不强制准确率或单位",
        "method": "按问题使用推导、证明、构造、反例分析或数值比较；待证明命题不得标为已证",
        "topic_hint": "明确待论证问题与已有理论的关系，不强制训练集或代码",
    },
    "computer_science": {
        "label": "软件工程与计算机系统",
        "core_topics": "软件测试与缺陷定位、程序分析、软件维护、数据库与检索系统、"
        "分布式系统、系统可靠性、性能优化与开发工具",
        "entity": "软件、工具或计算机系统，记录版本、架构、工作负载与实现环境",
        "metric": "任务完成率、缺陷检出率、误报率、吞吐量、延迟、资源占用与运行成本，"
        "需记录测量方法、负载、环境、重复次数及变化范围；未报告的数值留空",
        "method": "研究任务、数据来源与版本、比较工具、负载设置、运行环境、"
        "重复方案、故障条件与复现步骤；不强制套用模型训练参数",
        "topic_hint": "具体软件或系统、真实任务或负载、可靠性和性能瓶颈与可实施的比较",
    },
    "security": {
        "label": "计算机安全与隐私",
        "core_topics": "AI 系统安全、提示注入防护、隐私保护、鲁棒学习、软件漏洞检测、"
        "依赖与供应链风险、访问控制与安全评测",
        "entity": "受保护的模型、软件、数据或系统，记录威胁模型、攻击者能力、"
        "测试环境与明确授权范围；不涉及真实目标的未授权测试",
        "metric": "检测率、误报率、防护成功率、攻击成功率、任务质量损失与运行代价，"
        "需同时记录威胁条件、数据集与测量口径；不凭空宣称隐私或安全保证",
        "method": "公开数据或明确授权的隔离环境、攻击与防御假设、对照方案、"
        "数据划分、评测协议与复现步骤；区分模拟条件与真实部署",
        "topic_hint": "具体安全威胁、授权测试条件、防护机制及其有效性与代价",
    },
    "materials": {
        "label": "材料科学",
        "core_topics": "热电与能量转换材料、电池与储能材料、光伏材料、催化材料、"
        "超导与磁性材料、结构材料与合金、高分子与复合材料、半导体材料、"
        "计算材料学与材料基因工程",
        "entity": "材料体系（如 Bi2Te3 基热电材料、MAPbI3 钙钛矿、高熵合金），"
        "记录其化学式、晶体结构、空间群、晶格参数、组成掺杂、晶系与相组成",
        "metric": "性能指标（如 ZT、功率因子、热导率、电导率、Seebeck 系数、带隙、"
        "硬度、吸收系数、分解温度），需带数值、单位、测试条件，"
        "并标注 data_type（experimental/theoretical）与测试温度",
        "method": "合成与制备工艺（如固相反应、CVD、溶胶-凝胶、热压、水热法），"
        "记录前驱体及配比、溶剂、温度、压力、气氛、时间、升温速率、"
        "煅烧/退火条件、冷却方式、设备、产率、相纯度、粒径与步骤",
        "topic_hint": "具体材料体系、组分/掺杂/工艺手段与性能指标",
    },
    "chemistry": {
        "label": "化学与化工",
        "core_topics": "合成方法学与绿色化学、催化科学与反应工程、有机/无机合成、"
        "电化学与电催化、光化学、高分子化学、化学生物学、"
        "分离科学与化工过程强化",
        "entity": "反应或物质体系（如 Suzuki 偶联反应、MOF 催化剂、电解质体系），"
        "记录其分子式/结构、催化剂、底物与产物",
        "metric": "评价指标（如产率、选择性、转化率、TOF/TON、ee 值、"
        "反应速率常数、纯度、稳定性），需带数值、单位与测试条件",
        "method": "合成与反应条件（如溶剂、温度、压力、时间、催化剂负载量、"
        "配比、气氛、pH、后处理方式、分离纯化步骤与装置）",
        "topic_hint": "具体反应/催化剂/分子、合成路线与产率、选择性等指标",
    },
    "biology": {
        "label": "生命科学",
        "core_topics": "基因编辑与基因治疗、单细胞与空间组学、免疫学与肿瘤免疫、"
        "神经生物学、合成生物学、蛋白质设计与结构生物学、"
        "微生物组、表观遗传学",
        "entity": "生物研究对象（如细胞系、模式生物、基因/蛋白、通路、菌株），"
        "记录其物种来源、基因型、修饰方式与鉴定信息",
        "metric": "表型与功能指标（如表达量、活性、IC50/EC50、存活率、增殖速率、"
        "荧光强度、测序深度、统计显著性 p 值），需带数值、单位与实验条件",
        "method": "实验方法（如培养条件、转染/敲除方式、给药剂量与时长、"
        "测序或显微平台、抗体与试剂、样本量、统计检验方法）",
        "topic_hint": "具体基因/蛋白/通路/细胞系、表型指标与实验手段",
    },
    "medicine": {
        "label": "医学与药学",
        "core_topics": "肿瘤精准治疗与免疫治疗、罕见病与孤儿药、临床试验设计、"
        "药物递送系统、医学影像与AI辅助诊断、流行病学与公共卫生、"
        "再生医学与干细胞治疗",
        "entity": "疾病、靶点或干预对象（如 2 型糖尿病、PD-1 靶点、某候选药物、"
        "某术式），记录其人群特征、分期、剂量与对照设置",
        "metric": "临床与药效指标（如有效率、生存期、HR/OR、不良反应率、"
        "血药浓度、AUC、症状评分），需带数值、单位、随访时长与置信区间",
        "method": "干预与研究设计（如随机对照/队列/回顾性、样本量、纳入排除标准、"
        "给药方案、随访周期、统计分析模型与注册编号）",
        "topic_hint": "具体疾病/靶点/干预方案与临床终点指标",
    },
    "ai": {
        "label": "人工智能",
        "core_topics": "大语言模型与多模态大模型、智能体与工具调用、检索增强生成（RAG）、"
        "模型高效微调与对齐、AI 系统评测与可靠性、"
        "可信AI（可解释性/鲁棒性/公平性）、高效推理与模型压缩",
        "entity": "模型、算法或系统（如 Transformer、LoRA 微调方法、"
        "某检索增强框架），记录其架构、参数量、训练数据与开源情况",
        "metric": "评测指标（如 Accuracy、F1、BLEU、Pass@1、推理延迟、显存占用、"
        "训练成本），需带数值、数据集名称与实验设置",
        "method": "实现与训练配置（如框架、硬件、batch size、学习率与调度、"
        "优化器、epoch 数、随机种子、 ablation 设置与代码仓库）",
        "topic_hint": "具体模型/算法/数据集、训练方法与评测指标",
    },
    "physics": {
        "label": "物理学",
        "core_topics": "凝聚态物理与量子材料、量子信息与量子计算、引力波与天体物理、"
        "粒子物理与标准模型、冷原子与量子模拟、光学与光子学、"
        "统计物理与复杂系统、等离子体物理",
        "entity": "物理体系或对象（如拓扑绝缘体、冷原子体系、天体对象、"
        "粒子过程），记录其物相、对称性、参数区间与观测对象",
        "metric": "观测量与物理量（如能隙、相变温度、截面、谱线位置、"
        "精度、信噪比），需带数值、单位、误差棒与测量条件",
        "method": "实验装置或计算方法（如装置构型、探测器、真空与低温条件、"
        "采样时长、数值方法、边界条件、网格/截断参数与代码）",
        "topic_hint": "具体体系/物相/观测量与实验装置或数值方法",
    },
    "energy": {
        "label": "能源与环境",
        "core_topics": "太阳能光伏与光热、储能电池与氢能、碳捕集利用与封存（CCUS）、"
        "智能电网与能源互联网、风能与地热、水处理与资源回收、"
        "生命周期评价与能源系统优化",
        "entity": "器件、过程或系统（如钙钛矿太阳能电池、电解槽、碳捕集工艺、"
        "电网调度策略），记录其结构、材料/工质与规模",
        "metric": "性能与效率指标（如转换效率、能量密度、循环寿命、"
        "衰减率、捕集率、度电成本、碳排放强度），需带数值、单位与工况",
        "method": "工艺与运行条件（如制备流程、运行温度/压力、循环制度、"
        "催化剂/电解质、测试标准与老化协议）",
        "topic_hint": "具体器件/工艺/工况与效率、寿命、成本指标",
    },
}

# 未知 / 自定义领域的兜底画像：不预设任何学科，由研究主题自己决定语义
GENERIC_PROFILE: dict[str, str] = {
    "label": "通用（自定义领域）",
    "core_topics": "该领域当前最活跃、最核心的研究主题（由趋势数据与论文样本推断，"
    "不预设任何特定学科方向）",
    "entity": "本研究领域的核心研究对象（由论文内容决定，"
    "如某种材料、某个模型、某种生物体、某种方法），"
    "记录其名称、标识（化学式/编号/代号）与关键结构或属性描述",
    "metric": "该领域用于评价研究结果的关键量化指标，"
    "需带数值、单位与测试/评测条件，并标注数据来源类型"
    "（experimental 实验值 / theoretical 理论值）",
    "method": "获得该研究对象或其指标的方法、工艺与实验条件，"
    "记录关键参数（温度/剂量/超参/时长等）、设备与步骤",
    "topic_hint": "该领域的具体研究对象、科学问题、方法手段与评价指标",
}


def get_domain_profile(domain: Optional[str]) -> dict[str, str]:
    """取领域画像；未知或空时返回通用兜底画像。

    Args:
        domain: 领域标识（预设 key 或用户自定义的领域名称）

    Returns:
        含 label / entity / metric / method 的画像字典
    """
    if not domain:
        return dict(GENERIC_PROFILE)
    key = str(domain).strip().lower()
    key = DOMAIN_ALIASES.get(key, key)
    if key in DOMAIN_PROFILES:
        profile = dict(DOMAIN_PROFILES[key])
        profile["label"] = PUBLIC_DOMAINS.get(key, profile["label"])
        return profile
    # 自定义领域：保留用户填写的名称，语义走通用兜底
    profile = dict(GENERIC_PROFILE)
    profile["label"] = str(domain).strip()
    return profile


def domain_label(domain: Optional[str]) -> str:
    """领域显示名（用于 UI 与报告）。"""
    return get_domain_profile(domain)["label"]


def build_domain_hint(domain: Optional[str]) -> str:
    """生成一句话领域提示，可拼进任意阶段的 system prompt。

    例：``研究领域：生命科学。后续所有抽取与判断都按该领域的术语与标准进行。``
    """
    if not domain:
        return ""
    hint = f"研究领域：{domain_label(domain)}。后续所有抽取、判断与表述都按该领域的术语与标准进行。"
    return hint + (
        f"\n产品能力边界：{SCOPE_NOTICE}" if is_public_domain(domain) else ""
    )


def build_topic_recommend_hint(domain: Optional[str]) -> str:
    """方向推荐阶段的领域提示：该领域「具体」体现在哪些名词上。"""
    return get_domain_profile(domain).get("topic_hint", GENERIC_PROFILE["topic_hint"])


def build_core_topics_hint(domain: Optional[str]) -> str:
    """方向推荐阶段的核心研究主题锚点：明确告诉 LLM 该领域最主要的研究主题有哪些。

    这是防止推荐主题「跑偏到其他学科」的关键约束——LLM 拿到趋势数据后
    容易被训练记忆里的高频学科（如材料）带偏，此提示将其拉回所选领域。
    """
    p = get_domain_profile(domain)
    if domain and "core_topics" in p and domain.lower() in DOMAIN_PROFILES:
        return f"该领域（{p['label']}）当前最主要的核心研究主题包括：{p['core_topics']}。推荐方向必须落在这个范围内。"
    if domain:
        return f"研究领域为「{p['label']}」：推荐主题必须紧扣该领域自身的核心研究主题（依据趋势数据与论文样本判断），不要偏移到其他学科。"
    return "未指定研究领域：以用户研究兴趣与趋势数据为准，推荐该兴趣所属学科的核心研究主题，不要偏移到其他学科。"


def build_extraction_system_prompt(domain: Optional[str]) -> str:
    """生成「领域知识三元组抽取」的 system prompt。

    输出结构固定为「研究对象 → 评价指标 → 方法条件」三元组，
    三者的具体语义由领域画像决定，因此同一套 schema 可服务任意学科。
    """
    p = get_domain_profile(domain)
    return (
        f"你是{p['label']}领域的知识抽取专家。从论文标题与摘要中抽取结构化知识三元组：\n"
        f"1. 研究对象（entity）：{p['entity']}\n"
        f"2. 评价指标（property）：{p['metric']}\n"
        f"3. 方法条件（method）：{p['method']}\n"
        "字段映射约定（沿用统一的存储 schema）：\n"
        "- name 填研究对象名称；formula 填其标识（化学式/编号/代号）；\n"
        "- crystal_structure / space_group / lattice_parameters / composition "
        "等结构字段，用于记录该领域描述对象结构或构型的信息；\n"
        "- properties 列表填评价指标（property_name / value / unit / condition / data_type）；\n"
        "- synthesis 列表填方法条件（method / 关键参数 / steps）。\n"
        "硬约束：只抽取文本明确提到的信息，绝不臆造数值或实验参数；"
        "未出现的字段留空。每篇论文列出其主要研究对象。"
    )
