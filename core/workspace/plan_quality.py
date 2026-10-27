"""Read-only delivery checks; source matching is not scientific validation."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.workspace.plan_schema import ResearchPlan
from core.workspace.schema import Contract
from core.workspace.material_review import MATERIAL_NOTICE, MaterialReview, review_material_evidence


NOTICE = "仅核对当前资料、引用片段和条件；不证明引文支持论断、科学正确性、创新性或可发表性。"
CONDITIONS = {
    "resources": "资源／设备",
    "time_budget": "期限",
    "budget": "预算",
    "data_access": "资料权限",
}


class DeliveryCheck(Contract):
    code: str
    label: str
    status: Literal["pass", "attention"]
    detail: str


class CitationCheck(Contract):
    section: str
    statement: str
    paper_id: str
    title: str
    doi: str = ""
    quote: str
    matched: bool
    location: str
    page: int | None = None


class ConditionDetail(Contract):
    field: str
    label: str
    value: str
    basis: Literal["user", "unverified"]


class PlanQualityReport(Contract):
    version: Literal[1] = 1
    target_id: str
    workspace_revision: int
    status: Literal["legacy", "needs_attention", "ready_for_discussion"]
    label: str
    checks: list[DeliveryCheck]
    evidence: list[CitationCheck] = Field(default_factory=list)
    pending_conditions: list[str] = Field(default_factory=list)
    condition_details: list[ConditionDetail] = Field(default_factory=list)
    material_review: list[MaterialReview] = Field(default_factory=list)
    material_notice: str = MATERIAL_NOTICE
    unconfirmed: list[str] = Field(default_factory=list)
    review_questions: list[str] = Field(default_factory=list)
    notice: str = NOTICE


def assess_plan(plan: ResearchPlan | None, papers, chunks, *, target_id,
                workspace_revision, needs_review=False):
    checks = [DeliveryCheck(
        code="structure", label="方案结构", status="pass" if plan else "attention",
        detail="已有结构化方案。" if plan else "历史方案缺少结构字段，请人工审阅原文；未自动重写。",
    ), DeliveryCheck(
        code="basis", label="材料版本", status="attention" if needs_review else "pass",
        detail="方案或关联问题的依据已变化，先复核再修订。" if needs_review else "未发现已标记的依据变更。",
    )]
    evidence, pending, material_review = [], [], []
    abstract_matches = False
    if plan:
        for section in plan.sections:
            for statement in section.statements:
                if statement.basis != "literature":
                    continue
                for pid in statement.paper_ids or [""]:
                    paper = papers.get(pid)
                    found = next((c for c in chunks.get(pid, [])
                                  if statement.quote and statement.quote in c.text), None)
                    abstract = bool(paper and statement.quote and statement.quote in (paper.abstract or ""))
                    matched = bool(paper and pid in plan.references and (found or abstract))
                    declared_scope = paper.metadata.get("reading_scope") if paper else None
                    declared_scope = declared_scope.strip() if isinstance(declared_scope, str) else ""
                    if matched and found:
                        location = "已存片段" + (f"（第 {found.page} 页；未核对全文）" if found.page is not None else "（未标页码；未核对全文）")
                        location = declared_scope + "；" + location if declared_scope else location + "；阅读范围未声明"
                    elif matched:
                        location = declared_scope + "；匹配已存资料（未核对全文）" if declared_scope else "摘要字段（未核对全文）"
                        abstract_matches = True
                    elif not paper:
                        location = "来源不在当前文献库"
                    elif pid not in plan.references:
                        location = "引用未列入参考来源"
                    else:
                        location = "未匹配当前摘要或正文片段"
                    evidence.append(CitationCheck(
                        section=section.key, statement=statement.text, paper_id=pid,
                        title=paper.title if paper else "来源未匹配",
                        doi=(paper.doi or "") if paper else "", quote=statement.quote,
                        matched=matched, location=location, page=found.page if matched and found else None,
                    ))
        pending = [label for field, label in CONDITIONS.items()
                   if getattr(plan.conditions, field).basis != "user"]
        unused = set(plan.references) - {e.paper_id for e in evidence}
        citations_ok = bool(evidence) and all(e.matched for e in evidence) and not unused
        checks += [DeliveryCheck(
            code="citations", label="文献依据", status="pass" if citations_ok else "attention",
            detail=("所引片段可在当前资料中找到，仍需核对是否支持相应陈述。"
                    if citations_ok
                    else "尚无文献事实依据，请补充资料。" if not evidence
                    else "部分引文未匹配当前资料，或参考来源未对应到具体文献陈述，请逐条核对。"),
        ), DeliveryCheck(
            code="conditions", label="研究条件", status="attention" if pending else "pass",
            detail="请确认：" + "、".join(pending) if pending else "条件已标为用户提供；请核对是否适用于本次研究。",
        )]
        if abstract_matches:
            checks.append(DeliveryCheck(
                code="reading_scope", label="阅读范围", status="attention",
                detail="部分依据仅匹配已存摘要字段或来源简介；以各条来源声明的范围为准，方法细节和精确参数仍需合法原文核对。",
            ))
        context = "\n".join([
            *(statement.text for section in plan.sections for statement in section.statements),
            *plan.method_details.model_dump().values(),
        ])
        material_review = review_material_evidence(evidence, context=context)
        if material_review:
            checks.append(DeliveryCheck(
                code="material_comparability", label="材料性质与可比性", status="attention",
                detail="已整理引用中的数值、条件与实验／计算线索，请逐项人工复核；不以关键词匹配判定公平比较或研究创新。",
            ))
    attention = any(c.status == "attention" for c in checks)
    return PlanQualityReport(
        target_id=target_id, workspace_revision=workspace_revision,
        status="legacy" if plan is None else "needs_attention" if attention else "ready_for_discussion",
        label="历史方案，需人工审阅" if plan is None else "有待补充或复核项" if attention else "可供讨论，仍需专业审阅",
        checks=checks, evidence=evidence, pending_conditions=pending,
        condition_details=[ConditionDetail(field=field, label=label,
                                          value=getattr(plan.conditions, field).value,
                                          basis=getattr(plan.conditions, field).basis)
                           for field, label in CONDITIONS.items()] if plan else [],
        material_review=material_review,
        unconfirmed=plan.unconfirmed if plan else [],
        review_questions=[
            "引文是否真正支持对应陈述，而不只是出现相同词句？",
            "已有工作是否已回答该问题，所述缺口是否仍需补充检索？",
            "比较条件与验证设计能否回答研究问题，资源是否可行？",
        ],
    )


def render_quality(report: PlanQualityReport):
    lines = ["## 交付检查（依据当前资料）", report.label, "> " + report.notice]
    for check in report.checks:
        lines.append(f"- {check.label}：{check.detail}")
    for condition in report.condition_details:
        basis = "用户提供" if condition.basis == "user" else "待用户确认"
        lines.append(f"- {condition.label}（{basis}）：{condition.value}")
    for entry in report.evidence:
        title = entry.title if entry.title and entry.title != entry.paper_id else "来源未匹配"
        lines += [f"- 引用：{title}；{entry.location}；DOI：{entry.doi or '未提供'}",
                  "> 对应陈述：" + entry.statement.replace("\n", " "),
                  "> 引用片段：" + entry.quote.replace("\n", " ")]
    if report.material_review:
        lines += ["### 材料性质与可比性审阅", "> " + report.material_notice]
        sources = {entry.paper_id: entry for entry in report.evidence}
        for row in report.material_review:
            source = sources.get(row.paper_id)
            label = source.title if source and source.title and source.title != row.paper_id else "来源未匹配"
            if source and source.doi:
                label += "；DOI：" + source.doi
            if not row.source_matched and label != "来源未匹配":
                label += "（片段未匹配）"
            lines.append("- 对应陈述：" + row.statement.replace("\n", " ") + "；来源：" + label)
            if row.source_matched:
                labels = {"experimental": "实验线索", "computational": "计算线索", "mixed": "实验与计算线索并存", "unspecified": "未明确识别"}
                lines += ["- 数值／单位片段：" + "；".join(row.quantities or ["未识别"]),
                          "- 温度片段：" + "；".join(row.temperatures or ["未识别"]),
                          "- 条件词：" + "；".join(row.condition_mentions or ["未识别"]),
                          "- 证据类型线索：" + labels[row.evidence_type] + "；" + "、".join(row.evidence_type_mentions)]
            lines += ["- 待复核：" + question for question in row.review_points]
    lines += ["### 研究者审阅要点", *["- " + question for question in report.review_questions]]
    return "\n\n".join(lines)
