"""Project-scoped conversation contracts; entry stage never advances research."""

from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator
from core.workspace.schema import Contract, Area
from core.workspace.plan_schema import PlanRequest
from core.workspace.reading_session import PassageRequest, ReadingGuide

GuidanceMode = Literal["beginner", "direct", "case"]
EntryStage = Literal["orientation", "literature", "question", "design"]
DiscussionStep = Literal["conditions", "exploration", "reading", "synthesis", "question", "design"]


class ConversationCreate(Contract):
    request_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=1, max_length=128
    )
    title: str = Field(default="新对话", min_length=1, max_length=200)
    discussion_goal: str = Field(default="", max_length=2000)
    guidance_mode: GuidanceMode = "direct"
    entry_stage: EntryStage = "orientation"
    discussion_step: DiscussionStep | None = None
    case_id: str | None = Field(default=None, max_length=200)

    @field_validator("title", "request_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("不能为空白")
        return value


class ConversationPatch(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    discussion_goal: str | None = Field(default=None, max_length=2000)
    guidance_mode: GuidanceMode | None = None
    entry_stage: EntryStage | None = None
    discussion_step: DiscussionStep | None = None
    case_id: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("标题不能为空白")
        return value


class ConversationSend(Contract):
    wait_for_completion: bool = True  # Transport preference, excluded from request identity.
    request_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=1, max_length=128
    )
    content: str = Field(min_length=1, max_length=6000)
    expected_revision: int | None = Field(default=None, ge=0)
    area: Area | None = None  # Historical writing is readable, never generatable.
    selected_item_id: str | None = Field(default=None, max_length=200)
    selected_paper_ids: list[str] = Field(default_factory=list, max_length=3)
    plan_request: PlanRequest | None = None
    reading_guide: ReadingGuide | None = None
    passage: PassageRequest | None = None
    recovery_of: str | None = Field(default=None, max_length=128)  # Server-created round only.
    reading_purpose: Literal[
        "orientation", "method", "comparison", "innovation", "troubleshooting"
    ] = "orientation"

    @field_validator("content", "request_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("消息和 request_id 不能为空白")
        return value


class ConversationRetry(Contract):
    expected_revision: int | None = Field(default=None, ge=0)
    wait_for_completion: bool = True


class ConversationRestart(Contract):
    acknowledge_model_cost: bool
    wait_for_completion: bool = True


class ConversationApply(Contract):
    expected_revision: int | None = Field(default=None, ge=0)
