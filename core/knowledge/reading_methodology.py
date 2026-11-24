"""从现有阅读方法页提炼的任务规则；供阅读与指导共用。"""

READING_QUESTIONS = {
    "why": "为什么开展研究，现有方法的具体困难是什么？",
    "what": "提出了什么方法、数据或发现？",
    "how": "输入输出、关键步骤与适用条件是什么？",
    "result": "结果支持了什么，比较是否在相同条件下开展？",
    "weakness": "作者明确承认的局限是什么，哪些是读者的推断？",
    "ideas": "与当前问题有什么关系，什么改进值得进一步核查？",
}

READING_PURPOSES = {
    "orientation": "了解方向：先读摘要、引言和结论，建立问题与方法的分类。",
    "method": "理解方法：关注流程、输入输出、公式含义、实现前提和关键模块。",
    "comparison": "寻找基线：核对数据划分、实验预算、评价指标、开源条件与比较公平性。",
    "innovation": "寻找切入点：核查自述局限、未覆盖条件、反例和可替换模块。",
    "troubleshooting": "解决实验问题：围绕当前现象定向阅读相关段落与实验设置。",
}


def build_reading_hint(purpose: str = "orientation") -> str:
    purpose_hint = READING_PURPOSES.get(purpose, READING_PURPOSES["orientation"])
    questions = "；".join(READING_QUESTIONS.values())
    return (
        f"【阅读指导】{purpose_hint}\n六问按需取用，不强制每篇填满：{questions}\n"
        "论文事实、作者解释、用户想法和模型推断分别说明；关键判断附 paper_id 与原文位置。"
        "仅有标题或摘要时明确范围；有存储片段不代表已读完整篇。"
        "文献缺少描述不等于领域存在空白；没有定位到原文就写待核实。"
        "阅读成果应回答它怎样帮助当前研究决策，而非重复生成整篇综述。"
    )
