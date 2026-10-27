"""Prepared review text overrides old generation drafts without losing the draft."""

import pytest

from core.workspace.plan_schema import PlanRequest
from core.workspace.plans import requested
from core.workspace.schema import GuideDraft
from tests.test_project_conversations import chat, create, send
from tests.test_research_plan_templates import model


OLD_GENERATION = "请为所选研究问题生成一份研究方案，具体要求："
DISCUSSION = "请和我核对方案的引用片段，保留尚未确认的研究条件。"
NO_GENERATION = "本次不生成或修订方案。"
REVIEW_DRAFT = OLD_GENERATION + "\n\n" + DISCUSSION + "\n\n" + NO_GENERATION


@pytest.mark.parametrize("method", ["computational", "theoretical", "experimental"])
def test_review_intent_overrides_old_generation_text_and_stale_metadata(method):
    request = PlanRequest(method=method, question_id="selected-question")
    assert requested(OLD_GENERATION) is not None
    assert requested(REVIEW_DRAFT) is None
    assert requested(REVIEW_DRAFT, request) is None
    assert REVIEW_DRAFT.startswith(OLD_GENERATION)


def test_explicit_generation_resumes_only_after_conflicting_constraint_is_removed():
    request = PlanRequest(method="computational", question_id="new-question")
    appended = REVIEW_DRAFT + "\n\n请生成新的研究方案。"
    assert requested(appended, request) is None
    edited = appended.replace(NO_GENERATION, "")
    assert requested(edited, request) is request
    assert requested(edited, request).question_id == "new-question"


@pytest.mark.parametrize("stale_metadata", [False, True])
def test_review_draft_is_ordinary_discussion_in_actual_offline_route(chat, stale_metadata):
    cid = create(chat)
    before = chat.services["p1"].load(include_chat=False).model_dump()
    chat.control["generate"] = lambda area, text, context: GuideDraft(reply="先核对这条依据；缺失条件继续待确认。")
    extra = {"plan_request": {"method": "computational"}} if stale_metadata else {}
    response = send(chat, cid, REVIEW_DRAFT, rid="review", **extra)
    assert response.status_code == 200, response.text
    assert "plan_generation" not in chat.calls[-1]["context"]
    assert not response.json()["suggestions"]
    assert chat.services["p1"].load(include_chat=False).model_dump() == before


def test_edited_draft_and_explicit_question_produce_only_an_unadopted_plan(chat):
    cid = create(chat)
    question = next(item for item in chat.services["p1"].load().items if item.kind == "question")
    before = chat.services["p1"].load(include_chat=False).model_dump()
    chat.control["generate"] = lambda area, text, context: model(context)
    edited = REVIEW_DRAFT.replace(NO_GENERATION, "") + "\n\n请生成新的研究方案。"
    response = send(chat, cid, edited, rid="explicit-plan", plan_request={"method": "computational", "question_id": question.item_id})
    assert response.status_code == 200, response.text
    assert chat.calls[-1]["context"]["plan_generation"]["question"]["item_id"] == question.item_id
    suggestion = response.json()["suggestions"][0]
    assert suggestion["kind"] == "plan"
    assert suggestion["plan"]["question_id"] == question.item_id
    assert not suggestion["applied_item_id"]
    assert chat.services["p1"].load(include_chat=False).model_dump() == before
