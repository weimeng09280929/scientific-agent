"""Model-only plan contract, projected into the existing draft lifecycle."""

from functools import lru_cache

from pydantic import BaseModel, Field, create_model

from core.workspace.plan_schema import (
    ComputationalDesign, ExperimentalDesign, PlanConditions, PlanContract,
    PlanSection, ResearchPlan, TheoreticalDesign,
)
from core.workspace.schema import GuideDraft, GuideWireDraft, SuggestedItem
from core.workspace.service import WorkspaceError


class PlanOutput(PlanContract):
    title: str = Field(min_length=1, max_length=200)
    sections: list[PlanSection] = Field(min_length=9, max_length=9)
    conditions: PlanConditions
    method_details: BaseModel
    unconfirmed: list[str] = Field(min_length=1, max_length=30)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        # Reuse the existing numbered-source shapes without exposing the
        # general guidance envelope, persisted provenance or UI suggestions.
        numbered = GuideWireDraft.model_json_schema()["$defs"]
        for name in ("PlanStatement", "PlanCondition"):
            schema["$defs"][name] = numbered[name]
        for name in ("FactOrigin", "PaperOrigin"):
            schema["$defs"].pop(name, None)
        return schema

    def to_guidance(self, context):
        generation = context["plan_generation"]
        if not generation["allowed"]:
            raise WorkspaceError("当前研究问题或方法尚未核对，无法生成方案草案")
        plan = ResearchPlan(
            method=generation["method"],
            question_id=generation["question"]["item_id"],
            sections=self.sections,
            conditions=self.conditions,
            method_details={"type": generation["method"], **self.method_details.model_dump()},
            unconfirmed=self.unconfirmed,
            references=list(dict.fromkeys(
                paper for section in self.sections for statement in section.statements
                for paper in statement.paper_ids
            )),
        )
        revising = generation.get("draft_revision") or generation.get("revision_target")
        return GuideDraft(
            reply=("修订草案已生成，请核对修改、依据和待确认条件。"
                   if revising else "方法草案已生成，请审阅正文、依据和待确认条件。"),
            suggestions=[SuggestedItem(
                kind="plan", title=self.title, content="待审阅方法草案",
                related_ids=[plan.question_id],
                supersedes_id=generation["supersedes_id"], plan=plan,
            )],
        )


@lru_cache(maxsize=3)
def plan_output_schema(method):
    details = {
        "computational": ComputationalDesign,
        "theoretical": TheoreticalDesign,
        "experimental": ExperimentalDesign,
    }[method]
    # The selected method is already checked by the service. Only its actual
    # design fields are generated, so the model cannot switch the method.
    core_details = create_model(
        details.__name__ + "Output", __base__=PlanContract,
        **{key: (field.annotation, field) for key, field in details.model_fields.items() if key != "type"},
    )
    return create_model(
        details.__name__ + "PlanOutput", __base__=PlanOutput,
        method_details=(core_details, ...),
    )


def revision_for_model(target):
    """Keep the editable design while source validation uses this turn's IDs."""
    if not target or not target.get("plan"):
        return target
    plan = target["plan"]
    return {
        "title": target["title"],
        "plan": {
            "sections": [
                {"key": section["key"], "statements": [
                    {"text": statement["text"], "basis": statement["basis"]}
                    for statement in section["statements"]
                ]} for section in plan["sections"]
            ],
            "conditions": {
                key: {"value": value["value"], "basis": value["basis"]}
                for key, value in plan["conditions"].items()
            },
            "method_details": plan["method_details"],
            "unconfirmed": plan["unconfirmed"],
        },
    }
