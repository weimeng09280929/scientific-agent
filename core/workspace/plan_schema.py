"""Versioned STEM plan data embedded in existing suggestions and WorkItems."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from core.workspace.evidence_refs import FactOrigin, PaperOrigin

ResearchMethod = Literal["computational", "theoretical", "experimental"]


class PlanContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def preserve_legacy_shape(self, handler):
        data = handler(self)
        for key in ("revises_suggestion_id", "revised_from_draft_id", "evidence_layout", "fact_id", "evidence_id", "fact_origin", "paper_origin"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class PlanRequest(PlanContract):
    method: ResearchMethod | None = None
    question_id: str | None = Field(default=None, max_length=200)
    supersedes_id: str | None = Field(default=None, max_length=200)
    revises_suggestion_id: str | None = Field(default=None, max_length=200)


class PlanStatement(PlanContract):
    text: str = Field(default="", max_length=2000)
    basis: Literal["literature", "user", "proposal", "unverified"]
    paper_ids: list[str] = Field(default_factory=list, max_length=12)
    quote: str = Field(default="", max_length=2000)
    fact_id: str | None = Field(default=None, max_length=80)
    evidence_id: str | None = Field(default=None, max_length=80)
    fact_origin: FactOrigin | None = None
    paper_origin: PaperOrigin | None = None

    @model_validator(mode="after")
    def needs_text_or_fact(self):
        if not self.text.strip() and not self.fact_id:
            raise ValueError("方案陈述需要正文或用户原话编号")
        return self


SectionKey = Literal[
    "objective",
    "related_work",
    "hypothesis",
    "conditions",
    "method",
    "validation",
    "feasibility",
    "limitations",
    "references",
]


class PlanSection(PlanContract):
    key: SectionKey
    statements: list[PlanStatement] = Field(min_length=1, max_length=8)


class PlanCondition(PlanContract):
    value: str = Field(default="", max_length=2000)
    basis: Literal["user", "unverified"]
    quote: str = Field(default="", max_length=2000)
    fact_id: str | None = Field(default=None, max_length=80)
    fact_origin: FactOrigin | None = None

    @model_validator(mode="after")
    def needs_value_or_fact(self):
        if not self.value.strip() and not self.fact_id:
            raise ValueError("方案条件需要值或用户原话编号")
        return self


class PlanConditions(PlanContract):
    resources: PlanCondition
    time_budget: PlanCondition
    budget: PlanCondition
    data_access: PlanCondition


class ComputationalDesign(PlanContract):
    type: Literal["computational"]
    data_task: str = Field(min_length=1, max_length=1500)
    baselines: str = Field(min_length=1, max_length=1500)
    comparisons: str = Field(min_length=1, max_length=1500)
    budget: str = Field(min_length=1, max_length=1500)
    evaluation: str = Field(min_length=1, max_length=1500)
    reproducibility: str = Field(min_length=1, max_length=1500)


class TheoreticalDesign(PlanContract):
    type: Literal["theoretical"]
    definitions: str = Field(min_length=1, max_length=1500)
    assumptions: str = Field(min_length=1, max_length=1500)
    propositions: str = Field(min_length=1, max_length=1500)
    derivation_route: str = Field(min_length=1, max_length=1500)
    proof_obligations: str = Field(min_length=1, max_length=1500)
    counterexamples: str = Field(min_length=1, max_length=1500)
    boundaries: str = Field(min_length=1, max_length=1500)
    related_theory: str = Field(min_length=1, max_length=1500)


class ExperimentalDesign(PlanContract):
    type: Literal["experimental"]
    research_object: str = Field(min_length=1, max_length=1500)
    variables: str = Field(min_length=1, max_length=1500)
    controls: str = Field(min_length=1, max_length=1500)
    measurements: str = Field(min_length=1, max_length=1500)
    repetition_error: str = Field(min_length=1, max_length=1500)
    resource_needs: str = Field(min_length=1, max_length=1500)
    professional_review: str = Field(min_length=1, max_length=1500)


class ResearchPlan(PlanContract):
    template_version: Literal["stem-plan-v1"] = "stem-plan-v1"
    method: ResearchMethod
    question_id: str = Field(min_length=1, max_length=200)
    sections: list[PlanSection] = Field(min_length=9, max_length=9)
    conditions: PlanConditions
    method_details: Annotated[
        ComputationalDesign | TheoreticalDesign | ExperimentalDesign,
        Field(discriminator="type"),
    ]
    unconfirmed: list[str] = Field(min_length=1, max_length=30)
    references: list[str] = Field(default_factory=list, max_length=12)
    revised_from_draft_id: str | None = Field(default=None, max_length=200)
    evidence_layout: Literal["appendix-v1"] | None = None

    @model_validator(mode="after")
    def consistent(self):
        keys = {s.key for s in self.sections}
        if len(keys) != 9 or self.method_details.type != self.method:
            raise ValueError("共同方案章节必须完整且无重复，研究方法和方法设计必须一致")
        return self
