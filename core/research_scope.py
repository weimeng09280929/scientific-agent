"""首版产品的开放范围；不删除旧领域画像，也不迁移历史项目。"""

from __future__ import annotations
import re

DEFAULT_DOMAIN = "computer_science"
PUBLIC_DOMAINS = {
    "computer_science": "软件工程与计算机系统",
    "ai": "AI 与机器学习",
    "security": "计算机安全与隐私",
    "engineering": "工程与技术",
    "materials": "材料科学",
    "physics": "物理学",
    "chemistry": "化学",
    "energy": "能源",
    "mathematics": "数学",
}
DOMAIN_ALIASES = {
    "cs": "computer_science",
    "computer science": "computer_science",
    "计算机": "computer_science",
    "计算机科学": "computer_science",
    "软件工程": "computer_science",
    "人工智能": "ai",
    "machine learning": "ai",
    "网络安全": "security",
    "cybersecurity": "security",
    "材料": "materials",
    "物理": "physics",
    "化学": "chemistry",
    "工程": "engineering",
    "能源": "energy",
    "数学": "mathematics",
    "materials science": "materials",
    "material science": "materials",
    "mathematics": "mathematics",
    "math": "mathematics",
    "maths": "mathematics",
    "工程与技术": "engineering",
    "物理学": "physics",
    "材料科学": "materials",
    "软件工程与计算机系统": "computer_science",
    "ai 与机器学习": "ai",
    "计算机安全与隐私": "security",
}
SCOPE_NOTICE = (
    "当前版本面向理工科的选题、文献与研究方案设计，覆盖计算机、AI、工程、材料、物理、化学、能源与数学。"
    "流程止于可与导师讨论的研究方案；不启动实验或训练，不生成实验结果，不保证创新成立。"
    "支持计算研究、理论研究与实验研究设计（仅规划）；领域不决定研究方法。"
    "暂不支持医学、临床、药物、湿实验及具体危险操作或实物设备操作指导，"
    "也不提供真人访谈或人体实验的完整指导与伦理审定。"
    "安全研究仅面向公开数据、模拟环境或明确授权的测试。"
)

_UNSUPPORTED = re.compile(
    r"医学|临床|诊疗|癌症|肿瘤|药物|人体|患者|病人|真人访谈|人类受试|湿实验|爆炸物|炸药|武器|medical|clinical|patient|drug|human subjects|wet[ -]?lab|explosive",
    re.I,
)
_STEM_TERMS = re.compile(
    r"计算|软件|人工智能|机器学习|智能体|大模型|自然语言|安全|隐私|工程|材料|物理|化学|能源|数学|电池|电解质|半导体|光学|力学|机械|电子|电路|控制|数论|拓扑|代数|统计|催化|量子|热力|流体|computer|software|agent|machine learning|artificial intelligence|security|engineering|material|physics|chemistry|energy|mathemat|battery|batteries|electrolyte|semiconductor|optics|algebra|topology|llzo|rag|federated|(?<![a-z])(?:ai|ml|llm)(?![a-z])",
    re.I,
)


def ensure_plan_scope(text: str) -> None:
    # A statement explicitly excluding these studies is not an authorization
    # for them. Remove only the negated term, not the remainder of the sentence.
    checked = re.sub(
        r"(?:不涉及|不开展|不进行|不提供|不支持|不含|非)(?:任何)?(?:临床|人体|药物|湿实验|危险操作)",
        "",
        text,
    )
    if _UNSUPPORTED.search(checked):
        raise ValueError(
            "当前仅支持理工科研究方案规划，不提供临床／人体／药物、湿实验或危险操作指导；请明确调整研究范围。历史材料仍可查看导出。"
        )


def normalize_public_domain(domain: str | None) -> str:
    """九个理工领域，或可识别的理工细分方向；历史值只读不迁移。"""
    original = str(domain or "").strip()
    value = original.lower()
    value = DOMAIN_ALIASES.get(value, value) or DEFAULT_DOMAIN
    if value.startswith("stem:"):
        custom = original[5:].strip()
        ensure_plan_scope(custom)
        if (
            not (0 < len(custom) <= 80)
            or not _STEM_TERMS.search(custom)
            or re.search(r"[<>\n\r]", custom)
        ):
            raise ValueError(
                "细分方向尚不能确定属于哪一理工领域，请先澄清或选择九个开放领域之一；不会自动归入计算机。"
            )
        return "stem:" + custom
    if value not in PUBLIC_DOMAINS:
        raise ValueError(
            "当前版本开放理工科研究方案，请选择对应领域或填写理工细分方向。"
            "原领域的历史项目与材料保留，可继续查看、记录和导出；"
            "暂不启动该领域的新推荐或自动科研指导。"
        )
    return value


def is_public_domain(domain: str | None) -> bool:
    try:
        normalize_public_domain(domain)
        return True
    except ValueError:
        return False
