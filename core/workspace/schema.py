"""工作台的数据契约。运行结果、判断和对话建议分别保存。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from core.workspace.plan_schema import ResearchPlan

Area = Literal["overview", "literature", "question", "study", "writing"]
ItemKind = Literal["note", "question", "plan", "result", "finding", "outline"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Brief(Contract):
    goal: str = Field(default="", max_length=2000)
    background: str = Field(default="", max_length=2000)
    resources: str = Field(default="", max_length=2000)
    time_budget: str = Field(default="", max_length=1000)


class ItemInput(Contract):
    kind: ItemKind
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=16000)
    paper_ids: list[str] = Field(default_factory=list, max_length=30)
    related_ids: list[str] = Field(default_factory=list, max_length=30)
    metrics: dict[str, Annotated[float, Field(strict=True)]] = Field(
        default_factory=dict, max_length=100
    )
    result_status: Literal["completed", "failed", "inconclusive"] | None = None
    assessment: Literal["supports", "refutes", "inconclusive"] | None = None
    supersedes_id: str | None = None
    plan: ResearchPlan | None = None


class SourceRef(Contract):
    kind: Literal["idea", "gap", "method", "experiment_plan", "experiment_result"]
    source_id: str
    fingerprint: str


class ResearchFocus(Contract):
    question: str = ""
    plan: str = ""


class WorkItem(ItemInput):
    item_id: str = Field(default_factory=lambda: uuid4().hex)
    status: Literal["active", "superseded"] = "active"
    needs_review: bool = False
    created_at: str = Field(default_factory=now)
    origin: Literal["user", "accepted_suggestion", "linked_material"] = "user"
    source_ref: SourceRef | None = None
    plan_version: int | None = None


class GapSource(Contract):
    paper_id: str = Field(default="", max_length=200)
    part: Literal["abstract", "excerpt"] | None = None
    quote: str = Field(default="", max_length=1200)
    excerpt_id: str | None = Field(default=None, max_length=320)
    evidence_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def needs_evidence_or_legacy_fields(self):
        if not self.evidence_id and (not self.paper_id or self.part is None or not 4 <= len(self.quote) <= 320):
            raise ValueError("引文需要本轮证据编号或完整的旧格式来源")
        return self


class ResearchGap(Contract):
    kind: Literal["stated_limitation", "comparison", "hypothesis"]
    summary: str = Field(min_length=1, max_length=240)
    existing_work: str | None = Field(default=None, max_length=240)
    why: str = Field(min_length=1, max_length=240)
    uncertainty: str = Field(min_length=1, max_length=240)
    sources: list[GapSource] = Field(default_factory=list, max_length=4)


class GapSourceSnapshot(GapSource):
    # A numbered table window can cover a whole uploaded chunk. Only the
    # server-resolved snapshot accepts that size; model-supplied quotes stay bounded.
    quote: str = Field(max_length=1600)
    title: str
    page: int | None = None
    origin: str | None = None
    chunk_id: str | None = None
    chunk_index: int | None = None
    start: int | None = None
    end: int | None = None
    # Relative to the delivered excerpt or preview, not the full PDF.
    quote_start: int | None = None
    quote_end: int | None = None


class ResearchGapSnapshot(ResearchGap):
    sources: list[GapSourceSnapshot] = Field(default_factory=list, max_length=4)
    reading_note: str | None = None


class QuestionGapLink(Contract):
    gap_index: int = Field(ge=0, le=2, strict=True)
    connection: str = Field(min_length=1, max_length=240)
    value: str = Field(min_length=1, max_length=200)
    requirements: str = Field(min_length=1, max_length=240)
    uncertainty: str = Field(min_length=1, max_length=240)


class SuggestedItem(Contract):
    # The guide cannot invent experiment results or approve conclusions.
    kind: Literal["note", "question", "plan", "outline"] = "note"
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)
    paper_ids: list[str] = Field(default_factory=list, max_length=12)
    related_ids: list[str] = Field(default_factory=list, max_length=12)
    supersedes_id: str | None = Field(default=None, max_length=200)
    plan: ResearchPlan | None = None
    gap_link: QuestionGapLink | None = None


class ReplyOption(Contract):
    """A possible USER utterance, never an instruction to execute a tool."""

    label: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=500)
    explanation: str | None = Field(default=None, max_length=160, description="本条讨论的目的与预期，例如得到比较清单；不承诺执行、保存或科研结论，与content含义一致")


class ActionProposal(Contract):
    type: Literal["view_references", "open_plan", "open_settings", "adopt_suggestion"]
    # Models can reference an allowed existing draft or an index in THIS reply.
    # They never choose a URL, endpoint, action ID or formal record to write.
    suggestion_index: int | None = Field(default=None, ge=0, le=2)
    suggestion_id: str | None = Field(default=None, min_length=1, max_length=200)


class GuidanceAction(Contract):
    action_id: str = Field(default_factory=lambda: uuid4().hex)
    type: Literal["view_references", "open_plan", "open_settings", "adopt_suggestion", "start_search"]
    label: str
    suggestion_id: str | None = None
    item_id: str | None = None
    paper_ids: list[str] = Field(default_factory=list)
    topic: str | None = None
    year_from: int | None = None
    year_to: int | None = None


class GuideDraft(Contract):
    _omitted_reply_options: int = PrivateAttr(default=0)

    @model_validator(mode="wrap")
    @classmethod
    def isolate_optional_reply_fields(cls, value, handler):
        omitted = 0
        if isinstance(value, dict) and isinstance(value.get("suggested_replies"), list) and len(value["suggested_replies"]) <= 4:
            options = []
            for option in value["suggested_replies"]:
                # Unknown metadata is not an executable capability. Drop the
                # ENTIRE affected optional option; never add fields to its schema
                # or apply permissive extra handling to scientific material.
                if isinstance(option, dict) and set(option) - set(ReplyOption.model_fields):
                    omitted += 1
                else:
                    options.append(option)
            if omitted:
                value = {**value, "suggested_replies": options}
        result = handler(value)
        if omitted:
            result._omitted_reply_options = omitted
        return result

    reply: str = Field(min_length=1, max_length=14000)
    cited_paper_ids: list[str] = Field(default_factory=list, max_length=12)
    questions: list[str] = Field(default_factory=list, max_length=3)
    suggestions: list[SuggestedItem] = Field(default_factory=list, max_length=3)
    research_gaps: list[ResearchGap] = Field(default_factory=list, max_length=3)
    reading_citations: list[GapSource] = Field(default_factory=list, max_length=8)
    # Bounded ingress lists; the policy validates ALL selectors before reducing
    # presentation to four combined options. Never repair the whole answer just
    # because both lists individually contain four valid items.
    suggested_replies: list[ReplyOption] = Field(
        default_factory=list,
        max_length=4,
        description="与actions合计最多4项，有操作时减少讨论选项",
    )
    actions: list[ActionProposal] = Field(
        default_factory=list,
        max_length=4,
        description="与suggested_replies合计最多4项，仅允许已绑定的安全操作",
    )


class GuideWireDraft(GuideDraft):
    """Advertise only selectable references to the generator.

    The underlying validators remain strict for both forms. Keeping the old
    parser here permits replay and reading old exact quotations without
    encouraging a current model to send duplicate, hand-copied fields.
    """

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        definitions = schema["$defs"]

        def shape(properties):
            return {"type": "object", "properties": properties, "required": list(properties),
                    "additionalProperties": False}

        def number(key):
            return {"type": "string", "minLength": 1, "maxLength": 80, "description": key}

        def basis(name):
            return {"type": "string", "const": name}

        fact = number("本轮 user_facts 中的原话编号")
        evidence = number("本轮 papers 中的原文编号")
        definitions["GapSource"] = shape({"evidence_id": evidence})
        definitions["PlanStatement"] = {"oneOf": [
            shape({"basis": basis("user"), "fact_id": fact}),
            shape({"basis": basis("literature"), "text": {"type": "string"}, "evidence_id": evidence}),
            shape({"basis": {"type": "string", "enum": ["proposal", "unverified"]}, "text": {"type": "string"}}),
        ]}
        definitions["PlanCondition"] = {"oneOf": [
            shape({"basis": basis("user"), "fact_id": fact}),
            shape({"basis": basis("unverified"), "value": {"type": "string", "const": "待确认"}}),
        ]}
        for key in ("revised_from_draft_id", "evidence_layout"):
            definitions["ResearchPlan"]["properties"].pop(key, None)
        for key in ("FactOrigin", "PaperOrigin"):
            definitions.pop(key, None)
        return schema


class DirectPaperWireDraft(GuideWireDraft):
    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        schema["properties"] = {
            key: value for key, value in schema["properties"].items()
            if key in ("reply", "cited_paper_ids", "reading_citations")
        }
        schema["$defs"] = {"GapSource": schema["$defs"]["GapSource"]}
        return schema


class Suggestion(SuggestedItem):
    suggestion_id: str = Field(default_factory=lambda: uuid4().hex)
    based_on_revision: int
    applied_item_id: str | None = None
    material_fingerprint: str | None = None
    basis_changed: bool = False
    conversation_id: str | None = None
    gap_snapshot: ResearchGapSnapshot | None = None


class Message(Contract):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    role: Literal["user", "assistant"]
    content: str
    area: Area
    mode: Literal["user", "rules", "model", "error"]
    cited_paper_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    suggestion_ids: list[str] = Field(default_factory=list)
    suggested_replies: list[ReplyOption] = Field(default_factory=list)
    actions: list[GuidanceAction] = Field(default_factory=list)
    delivery_warnings: list[str] = Field(default_factory=list, max_length=6)
    research_gaps: list[ResearchGapSnapshot] = Field(default_factory=list, max_length=3)
    research_gap_fingerprint: str | None = None
    reading_materials: list[dict] = Field(default_factory=list, max_length=12)
    reading_citations: list[GapSourceSnapshot] = Field(default_factory=list, max_length=8)
    reading_fingerprint: str | None = None
    reading_guide: dict | None = None
    reading_question: str | None = Field(default=None, max_length=360)
    # Server-authored task delivery metadata; not accepted in GuideDraft output.
    research_task_id: str | None = None
    report_version: int | None = None
    report_citations: list[dict] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)
    conversation_id: str | None = None
    request_id: str | None = None
    request_status: str | None = None
    error: str | None = None
    content_source: Literal["user", "model", "rules", "teaching_case"] | None = None


class Workspace(Contract):
    schema_version: int = 1
    project_id: str
    revision: int = 0
    chat_revision: int = 0
    brief: Brief = Field(default_factory=Brief)
    # Metadata only: no second copy of background or chat summary. Old stores
    # remain readable without migration; a manual change records its revision.
    brief_revision: int = Field(default=0, ge=0)
    focus: ResearchFocus = Field(default_factory=ResearchFocus)
    items: list[WorkItem] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now)
