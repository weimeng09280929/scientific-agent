"""Literal review cues from cited material-property passages, never property data."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from core.workspace.schema import Contract


MATERIAL_NOTICE = (
    "仅从已匹配的引用片段摘取数值、单位和条件词；不自动换算、跨文献排名或判定可比性。"
    "实验／计算标签只是原文线索，缺少线索不等于论文未报告，更不等于研究空白。"
)
_MATERIAL = re.compile(
    r"材料(?:科学|性能|性质|体系|成分)|固态电解质|硫化物|合金|晶体|热电|半导体|催化剂|"
    r"电导率|热导率|离子传导|带隙|形成能|弹性模量|杨氏模量|抗拉强度|比容量|"
    r"\b(?:materials? (?:science|properties)|solid[- ]state electrolytes?|sulfides?|alloys?|"
    r"semiconductors?|thermoelectric|conductivity|band ?gap|formation energy|"
    r"young['’]?s modulus|tensile strength|specific capacity)\b", re.I,
)
_NUMBER = r"[+−-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+−-]?\d+|\s*[×x]\s*10\s*\^?\s*[−-]?\d+)?"
_VALUE = rf"{_NUMBER}(?:\s*[–—~～至-]\s*{_NUMBER})?"
_EXPONENT = r"(?:\^?\s*[−-]?\s*[12]|[⁻−]?[¹²])"
_UNIT = (
    rf"(?:[munµμ]?S\s*(?:/\s*(?:cm|m)|[·⋅ ]*\s*(?:cm|m)\s*{_EXPONENT})|"
    rf"W\s*(?:/\s*\(?\s*m\s*[·⋅ ]?\s*K\s*\)?|[·⋅ ]*m\s*{_EXPONENT}\s*[·⋅ ]*K\s*{_EXPONENT})|"
    r"(?:m?A\s*h)\s*/\s*(?:k?g)|(?:m?eV)(?:\s*/\s*atom)?|(?:G|M|k)?Pa|"
    r"(?:m|µ|μ)?V|(?:m|µ|μ)?A|(?:k|m)?Hz|(?:m|µ|μ|n)?m|(?:wt|at)\s*%|%)"
)
_QUANTITY = re.compile(rf"(?<![A-Za-z0-9_.,，^⁻−+-]){_VALUE}\s*{_UNIT}(?![A-Za-z0-9]|\s*[/^·⋅⁻−¹²³]|-\d)")
_TEMPERATURE = re.compile(rf"(?<![A-Za-z0-9_.,，^⁻−+-]){_VALUE}\s*(?:°\s*[CK]|℃|K|K)(?![A-Za-z0-9]|\s*[/^·⋅⁻−¹²³]|-\d)")
_DIMENSIONLESS = re.compile(rf"(?<![A-Za-z])ZT\s*(?:=|of|为|约)?\s*{_VALUE}(?![0-9.,，])", re.I)
_CONDITIONS = re.compile(
    r"室温|低温|高温|单晶|多晶|薄膜|块体|压力|湿度|气氛|频率|晶相|孔隙率|致密度|"
    r"交流阻抗|阻抗谱|四探针|烧结|退火|样品|"
    r"\b(?:room temperature|single[- ]crystal|polycrystalline|thin[- ]film|bulk|"
    r"pressure|humidity|atmosphere|frequency|phase|porosity|density|impedance|"
    r"four[- ]probe|sinter(?:ed|ing)?|anneal(?:ed|ing)?|sample)\b", re.I,
)
_EXPERIMENT = re.compile(
    r"实验测量|实验值|实测|测得|测量结果|\b(?:experimentally|experimental (?:data|values?|results?)|measured)\b", re.I,
)
_COMPUTATION = re.compile(
    r"计算预测|计算值|计算结果|理论值|第一性原理|密度泛函|分子动力学|"
    r"\b(?:DFT|ab initio|first[- ]principles|density functional|molecular dynamics|"
    r"computed|calculated|predicted)\b", re.I,
)


class MaterialReview(Contract):
    section: str
    statement: str
    paper_id: str
    source_matched: bool
    quantities: list[str] = Field(default_factory=list)
    temperatures: list[str] = Field(default_factory=list)
    condition_mentions: list[str] = Field(default_factory=list)
    evidence_type: Literal["experimental", "computational", "mixed", "unspecified"] = "unspecified"
    evidence_type_mentions: list[str] = Field(default_factory=list)
    review_points: list[str] = Field(default_factory=list)


def _mentions(pattern, text, *, skip_negated=False):
    values = []
    for match in pattern.finditer(text):
        # shortcut: local negation only; these are review cues, not semantic classification.
        before = text[max(0, match.start() - 40):match.start()]
        after = text[match.end():match.end() + 25]
        if skip_negated and (
            re.search(r"(?:\b(?:not|no|without)\b|并非|没有|不含|缺少|未进行|未开展|非)[^.;；。\n]{0,24}$", before, re.I)
            or re.match(r"\s*(?:不存在|缺失|不足|尚缺|未报告|(?:is |are |was |were )?(?:unavailable|not available))", after, re.I)
        ):
            continue
        value = match.group(0)
        if value not in values:
            values.append(value)
    return values


def review_material_evidence(evidence, *, context=""):
    """Context selects the review scope; only matched quotes supply actual cues."""
    material_context = bool(_MATERIAL.search(context))
    rows = []
    for entry in evidence:
        if not material_context and not _MATERIAL.search(entry.statement + "\n" + entry.quote):
            continue
        row = MaterialReview(section=entry.section, statement=entry.statement,
                             paper_id=entry.paper_id, source_matched=entry.matched)
        rows.append(row)
        if not entry.matched:
            row.review_points = ["先修复来源匹配；未从未匹配的片段提取材料数据线索。"]
            continue
        quote = entry.quote
        row.quantities = _mentions(_QUANTITY, quote) + _mentions(_DIMENSIONLESS, quote)
        row.temperatures = _mentions(_TEMPERATURE, quote)
        row.condition_mentions = _mentions(_CONDITIONS, quote)
        experimental = _mentions(_EXPERIMENT, quote, skip_negated=True)
        computational = _mentions(_COMPUTATION, quote, skip_negated=True)
        row.evidence_type_mentions = experimental + computational
        row.evidence_type = "mixed" if experimental and computational else "experimental" if experimental else "computational" if computational else "unspecified"
        if not row.quantities:
            row.review_points.append("片段未识别到常见数值／单位线索；若陈述涉及定量比较，请回原文核对性质定义、数值与单位，不能由此判断论文缺失数据。")
        else:
            row.review_points.append("逐项核对数值对应的性质、材料成分／晶相、单位及误差；这些片段未自动关联为完整测量记录。")
        if not row.temperatures:
            row.review_points.append("片段未识别到数值温度；室温不能自动当作 25 ℃，比较前应核对原文温度和测试条件。")
        row.review_points.append("跨来源比较前核对样品状态、制备与测量方法、压力／频率等适用条件；出现相同条件词不证明可比。")
        if row.evidence_type == "mixed":
            row.review_points.append("片段同时含实验和计算线索；分别标明数值来源，不能直接合并排名或视为相互验证。")
        elif row.evidence_type == "computational":
            row.review_points.append("有计算线索；核对计算方法、模型假设及适用范围，不能将预测当作实测结果。")
        elif row.evidence_type == "unspecified":
            row.review_points.append("片段未明确识别实验或计算线索；请查方法与数据出处，不能按方案的研究类型替文献定性。")
    return rows
