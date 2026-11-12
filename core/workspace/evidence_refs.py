"""Turn-scoped references over the exact material sent to the model."""

from __future__ import annotations

from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict

class FactOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    record_id: str
    version: str


class PaperOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    part: str
    origin: str | None = None
    page: int | None = None
    excerpt_id: str | None = None
    chunk_id: str | None = None
    start: int
    end: int


def ref_id(prefix, scope, identity):
    data = json.dumps([scope, identity], ensure_ascii=False, sort_keys=True).encode("utf-8")
    return prefix + sha256(data).hexdigest()[:24]


def fact_catalog(context, text, scope):
    """Use complete source fields/messages. A clipped message is never a fact."""
    facts = {}

    def add(source, record_id, version, value):
        if not value.strip() or len(value) > 2000:
            return
        origin = FactOrigin(source=source, record_id=str(record_id), version=str(version))
        ref = ref_id("fact-", scope, [origin.model_dump(), value])
        facts[ref] = {**origin.model_dump(), "text": value}

    for key, value in context.get("brief", {}).items():
        if key in ("goal", "background", "resources", "time_budget"):
            add("project_brief", key, context.get("project_background", {}).get("revision", 0), value)
    question = (context.get("plan_generation", {}).get("question") or {})
    if question.get("item_id") and not question.get("needs_review"):
        add("confirmed_question", question["item_id"], question.get("created_at", ""), question["content"])
    for msg in context.get("history", []):
        if (msg.get("role") == "user" and msg.get("message_id") and msg.get("source_revision") is not None
                and msg.get("full_user_message")
                and not msg.get("background_outdated")):
            add("user_message", msg["message_id"], msg.get("source_revision"), msg["content"])
    add("current_request", scope.get("request_id", "current"), scope.get("revision", ""), text)
    return facts


def resolve_fact(fact_id, facts, *, supplied=(), condition=None):
    from core.workspace.service import WorkspaceError

    fact = facts.get(fact_id)
    if fact is None:
        raise WorkspaceError("这条用户原话编号不属于本次材料或已经失效。原消息已保留，请核对后再请求。")
    if condition and (fact["source"], fact["record_id"]) != ("project_brief", condition):
        raise WorkspaceError("这条原话不是该项已保存的项目条件。请保留待确认或选择对应的项目背景。")
    raw = fact["text"]
    if condition and raw.strip().rstrip("。.!！") in ("未知", "待确认", "未确定", "尚未明确", "暂不确定"):
        raise WorkspaceError("这项条件的原话仍表示未知，请继续标为待确认。")
    if any(value and value != raw for value in supplied):
        raise WorkspaceError("原话编号与提交的文字不一致。请使用原话编号，将解释或建议单独列出。")
    return raw, FactOrigin.model_validate({k: v for k, v in fact.items() if k != "text"})
