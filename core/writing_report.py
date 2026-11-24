"""有实测数据但尚未证实创新时的结果报告结构；不是占位研究结论。"""
from core.experiment_results import has_real_metrics
from core.knowledge.schema import ExperimentStatus

REPORT_STYLE = "简体中文、精简结果记录稿。只使用已审核方法和实际指标；未验证论断只用于说明局限。没有原文和引用映射时不编造作者、数字引用、图表或理论证明。不宣称首创、普适优胜、显著性或系统独立科学核验。"


def build_results_outline(store, claim_ids, experiment_ids, topic):
    experiments = [store.get_experiment(eid) for eid in experiment_ids] if store else []
    measured = [item for item in experiments if item is not None
                and item.status == ExperimentStatus.COMPLETED and has_real_metrics(item.metrics)]
    if not measured:
        raise ValueError("没有可用于结果报告的实际指标")
    names = "、".join(item.name for item in measured)
    paper_count = len(store.list_papers())
    sections = [
        {"title":"研究问题与证据范围", "claim_ids":[], "key_points":[f"当前主题：{topic}",f"项目保存{paper_count}篇论文记录，摘要与目录不等于已核验全文；不据此宣称研究空白或新颖性。","说明本稿为已有方法的结果记录，不提出新算法，不安排理论证明或不存在的图表。"],"target_word_count":300},
        {"title":"已审核方法与执行条件", "claim_ids":[], "key_points":["严格按照本次提供的已审核方法描述数据、划分、威胁模型、算法和随机种子，不增加额外实验。","只说明实际执行条件，不将历史草案或待检验论断当作方法保证。"],"target_word_count":450},
        {"title":"实测结果与可复算记录", "claim_ids":[], "key_points":[f"实际结果任务：{names}。","从实际指标中选择关键对照，以均值和已提供的标准差呈现；没有检验数据时不写显著性、置信区间或理论阈值。","区分用户提交状态、结果观察和科研判断，数字必须可回溯至实际指标。"],"target_word_count":700},
        {"title":"未验证设想与局限", "claim_ids":list(claim_ids), "key_points":["本节关联的论断均需按实际状态解释；无法定论、未支持或被反驳的内容不能写成正面贡献。","说明当前数据、攻击类型、规模和复算范围，不外推未执行条件。","无来源的理论性质和作者/数字引用应删除，而不是凭空补上。"],"target_word_count":400},
        {"title":"结论与后续工作", "claim_ids":[], "key_points":["只总结本次配置下的观察和复现材料；后续工作明确标为尚未执行。","执行完成不等于创新成立，也不表示已完成系统外的独立科学审查。"],"target_word_count":250},
    ]
    return {"mode":"results_report", "sections":sections, "total_target_word_count":2100,
            "abstract":f"本报告记录“{topic}”的{len(measured)}组带实际指标的实验结果。写作依据为已审核方法和用户回填数据；论断支持情况仍需区分，结果仅限所列条件，不构成新算法或普适性证明。"}
