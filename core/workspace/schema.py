"""工作台的数据契约。运行结果、判断和对话建议分别保存。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
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


class SuggestedItem(Contract):
    # The guide cannot invent experiment results or approve conclusions.
    kind: Literal["note", "question", "plan", "outline"] = "note"
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)
    paper_ids: list[str] = Field(default_factory=list, max_length=12)
    related_ids: list[str] = Field(default_factory=list, max_length=12)
    supersedes_id: str | None = Field(default=None, max_length=200)
    plan: ResearchPlan | None = None


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
    reply: str = Field(min_length=1, max_length=14000)
    cited_paper_ids: list[str] = Field(default_factory=list, max_length=12)
    questions: list[str] = Field(default_factory=list, max_length=3)
    suggestions: list[SuggestedItem] = Field(default_factory=list, max_length=3)
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


class Suggestion(SuggestedItem):
    suggestion_id: str = Field(default_factory=lambda: uuid4().hex)
    based_on_revision: int
    applied_item_id: str | None = None
    material_fingerprint: str | None = None
    basis_changed: bool = False
    conversation_id: str | None = None


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
