"""Delivery regressions from the 2026-10-22 audit; real routes, offline model."""

import pytest
from tests.test_project_conversations import chat, create, send, messages
from tests.test_research_plan_templates import model, generate
from core.workspace.schema import Brief, GuideDraft, ItemInput
from core.invocation_audit import read_audit
from tests.test_research_tasks import rt


def options():
    return [{"label": f"维度{i}", "content": f"请解释比较维度{i}。"} for i in range(4)]


def conditions_before_question(chat, brief):
    s = chat.services["p1"]
    old = s.load().items[0]
    s.save_brief(brief, s.load().revision)
    s.add_item(
        ItemInput(
            kind="question",
            title="条件确认后的问题",
            content=old.content,
            paper_ids=old.paper_ids,
            supersedes_id=old.item_id,
        ),
        s.load().revision,
    )
    return s


def test_combined_overflow_delivers_once_after_all_safety_checks(chat):
    cid = create(chat)
    chat.control["generate"] = lambda *a: GuideDraft(
        reply="经过校验的回答",
        cited_paper_ids=["p1-paper"],
        suggested_replies=options(),
        actions=[{"type": "open_settings"}, {"type": "view_references"}],
    )
    first = send(chat, cid)
    assert first.status_code == 200, first.text
    assistant = first.json()["messages"][-1]
    assert assistant["content"] == "经过校验的回答"
    assert len(assistant["actions"]) + len(assistant["suggested_replies"]) == 4
    assert send(chat, cid).json()["messages"] == first.json()["messages"]
    assert len(messages(chat, cid)) == 2 and len(chat.calls) == 1
    audit = first.json()["call_audit"]["entries"]
    assert any(e["name"] == "display_normalization" for e in audit)


@pytest.mark.parametrize(
    "invalid", ["foreign", "empty_refs", "invalid_action", "foreign_record"]
)
def test_overflow_cannot_hide_invalid_content(chat, invalid):
    cid = create(chat)

    def result(*a):
        d = {
            "reply": "不能交付无效动作",
            "suggested_replies": options(),
            "actions": [{"type": "open_settings"}, {"type": "view_references"}],
            "cited_paper_ids": ["p1-paper"],
        }
        if invalid == "foreign":
            d["cited_paper_ids"] = ["p2-paper"]
        if invalid == "empty_refs":
            d["cited_paper_ids"] = []
        if invalid == "invalid_action":
            d["actions"][-1] = {"type": "execute_experiment"}
        if invalid == "foreign_record":
            d["actions"][-1] = {"type": "adopt_suggestion", "suggestion_id": "foreign"}
        return d

    chat.control["generate"] = result
    r = send(chat, cid)
    if invalid in ("empty_refs", "foreign_record"):
        # Content/evidence is valid; reject the unusable card independently.
        # This does not make its foreign resource or empty citation executable.
        assert r.status_code == 200 and r.json()["status"] == "completed"
        m=messages(chat,cid)[-1]
        assert m["delivery_warnings"]
        assert all(a["type"]=="open_settings" for a in m["actions"])
        assert not r.json()["suggestions"]
        assert len(messages(chat,cid))==2
        assert any(e["outcome"]=="rejected" for e in r.json()["call_audit"]["entries"])
    else:
        assert r.status_code in (422, 502) and r.json()["status"] == "failed"
        assert len(messages(chat, cid)) == 1


@pytest.mark.parametrize(
    "condition", ["exact", "spacing", "missing", "invented", "paraphrase"]
)
def test_condition_provenance_and_one_bounded_repair(chat, condition):
    cid = create(chat)
    s = conditions_before_question(
        chat, Brief(resources="普通笔记本 CPU", time_budget="四周")
    )

    def result(area, text, ctx):
        assert ctx["brief"]["resources"] == "普通笔记本 CPU"
        assert ctx["plan_generation"]["question"]["item_id"]
        d = model(ctx)
        c = d.suggestions[0].plan.conditions.resources
        if condition == "spacing":
            c.value = "普通笔记本CPU"
        if condition == "missing":
            c.basis = "unverified"
            c.value = "待确认"
            c.quote = ""
        if condition == "invented":
            c.value = "GPU"
            c.quote = "我有GPU"
        if condition == "paraphrase":
            c.value = "一台个人计算机"
        return d

    chat.control["generate"] = result
    r = generate(chat, cid)
    if condition in ("invented", "paraphrase"):
        assert r.status_code == 422, r.text
        assert "资源／设备" in r.json()["error"] and "原文" in r.json()["error"]
        assert len(messages(chat, cid)) == 1
        assert len(chat.calls) == (2 if condition == "paraphrase" else 1)
        if condition == "paraphrase":
            assert chat.calls[-1]["context"]["condition_repair"]["field"] == "resources"
        assert r.json()["failure"]["reason"] == "structure_invalid"
        assert r.json()["failure"]["can_retry"] is False
        before = s.load(include_chat=False).model_dump(mode="json")
        call_count = len(chat.calls)
        # P1 deliberately forbids blind retries of invalid evidence. Swapping
        # the model substitute cannot authorize a new execution of this request.
        chat.control["generate"] = lambda area, text, ctx: model(ctx)
        rr = chat.client.post(
            f"/api/projects/p1/conversations/{cid}/requests/plan/retry", json={}
        )
        assert rr.status_code == 422, rr.text
        assert rr.json()["status"] == "failed"
        assert rr.json()["failure"]["reason"] == "structure_invalid"
        assert rr.json()["failure"]["can_retry"] is False
        assert rr.json()["attempt"] == r.json()["attempt"]
        assert rr.json()["request_id"] == r.json()["request_id"]
        assert rr.json()["call_audit"] == r.json()["call_audit"]
        assert len(chat.calls) == call_count
        assert len(messages(chat, cid)) == 1 and rr.json()["suggestions"] == []
        assert s.load(include_chat=False).model_dump(mode="json") == before
    else:
        assert r.status_code == 200, r.text
        assert not r.json()["suggestions"][0]["applied_item_id"]
        if condition == "spacing":
            assert (
                r.json()["suggestions"][0]["plan"]["conditions"]["resources"]["quote"]
                == "普通笔记本 CPU"
            )
    assert len(s.load().items) == 3  # question revision retained, no plan adoption


def test_repair_is_audited_and_revalidated_before_delivery(chat):
    cid = create(chat)
    s = conditions_before_question(chat, Brief(resources="普通笔记本 CPU"))

    def result(area, text, ctx):
        d = model(ctx)
        if not ctx.get("condition_repair"):
            d.suggestions[0].plan.conditions.resources.value = "个人电脑"
        return d

    chat.control["generate"] = result
    r = generate(chat, cid)
    assert r.status_code == 200, r.text
    assert len(chat.calls) == 2
    assert any(
        e["name"] == "condition_repair:resources"
        for e in r.json()["call_audit"]["entries"]
    )
    assert len(messages(chat, cid)) == 2


def test_guidance_budgets_are_task_specific_and_context_preserves_evidence():
    from core.workspace.guide import generate_guidance

    calls = []

    class Registry:
        def structured_output(self, **kw):
            calls.append(kw)
            return GuideDraft(reply="offline")

    for area, plan, budget in [
        ("overview", False, 4096),
        ("question", False, 6144),
        ("study", True, 12000),
    ]:
        ctx = {
            "topic": "联邦学习",
            "brief": {"resources": "普通笔记本 CPU", "time_budget": "四周"},
            "papers": [],
            "conversation": {},
            "history": [],
        }
        if plan:
            ctx["plan_generation"] = {
                "allowed": True,
                "question": {"content": "待确认问题", "paper_ids": []},
            }
        generate_guidance(Registry(), area, "ai", "本轮用户原文", ctx, "orientation")
        assert calls[-1]["max_tokens_override"] == budget
        assert "普通笔记本 CPU" in calls[-1]["prompt"] and "四周" in calls[-1]["prompt"]


def test_filtering_state_is_persisted_before_rank_call(rt, monkeypatch):
    import sqlite3
    from core.research_tasks import service
    from tests.test_research_tasks import (
        create as task_chat,
        start_via_message,
        wait_terminal,
    )

    original = service.rank_candidates
    seen = []

    def rank(*args):
        with sqlite3.connect(rt.config.paths.project_db("p1")) as conn:
            seen.append(
                conn.execute("SELECT stage FROM sra_research_tasks").fetchone()[0]
            )
        return original(*args)

    monkeypatch.setattr(service, "rank_candidates", rank)
    cid = task_chat(rt)
    task = start_via_message(rt, cid)
    result = wait_terminal(rt, "p1", cid, task["task_id"])
    assert result["status"] in ("completed", "partial")
    assert seen == ["ranking"]


def test_literal_condition_cannot_drop_negative_qualifier(chat):
    cid = create(chat)
    conditions_before_question(chat, Brief(resources="没有GPU，设备权限待确认"))

    def output(area, text, ctx):
        d = model(ctx)
        d.suggestions[0].plan.conditions.resources.value = "GPU"
        return d

    chat.control["generate"] = output
    r = generate(chat, cid)
    assert r.status_code == 422 and len(chat.calls) == 2
    assert len(messages(chat, cid)) == 1
    assert any(
        "condition_qualifier_omitted" in e["name"]
        for e in r.json()["call_audit"]["entries"]
    )


def test_explicit_not_generating_plan_stays_ordinary_guidance(chat):
    cid = create(chat)
    r = send(chat, cid, "请解释联邦学习概念，不生成方案。")
    assert r.status_code == 200, r.text
    assert len(chat.calls) == 1 and "plan_generation" not in chat.calls[0]["context"]
    assert all(s["kind"] != "plan" for s in r.json()["suggestions"])


def test_final_truncation_is_actionable_not_a_partial_success(chat):
    from core.llm.base import LLMTruncatedError

    cid = create(chat)

    def truncated(*args):
        raise LLMTruncatedError("technical private diagnostic")

    chat.control["generate"] = truncated
    r = send(chat, cid)
    assert r.status_code == 502 and r.json()["status"] == "failed"
    assert "没有保存不完整草案" in r.json()["error"]
    assert "LLMTruncatedError" not in r.json()["error"]
    assert "technical private diagnostic" not in r.json()["error"]
    assert len(messages(chat, cid)) == 1
    assert any(
        e["name"] == "final_failure:LLMTruncatedError"
        for e in r.json()["call_audit"]["entries"]
    )


@pytest.mark.parametrize("origin", ["history", "question"])
def test_condition_evidence_from_current_chat_or_formal_question(chat, origin):
    from core.workspace.plan_schema import PlanCondition

    cid = create(chat)
    if origin == "history":
        assert (
            send(
                chat, cid, "我的期限四周，资源普通笔记本 CPU。", "conditions"
            ).status_code
            == 200
        )
    else:
        s = chat.services["p1"]
        old = s.load().items[0]
        s.add_item(
            ItemInput(
                kind="question",
                title="含已确认条件的问题",
                content="普通笔记本 CPU、四周条件下如何比较方法？",
                supersedes_id=old.item_id,
            ),
            s.load().revision,
        )

    def output(area, text, ctx):
        d = model(ctx)
        d.suggestions[0].plan.conditions.resources = PlanCondition(
            value="普通笔记本 CPU", basis="user", quote="普通笔记本 CPU"
        )
        d.suggestions[0].plan.conditions.time_budget = PlanCondition(
            value="四周", basis="user", quote="四周"
        )
        return d

    chat.control["generate"] = output
    result = generate(chat, cid)
    assert result.status_code == 200, result.text
    assert (
        result.json()["suggestions"][0]["plan"]["conditions"]["time_budget"]["value"]
        == "四周"
    )
    ctx = chat.calls[-1]["context"]
    assert ctx["brief"]["resources"] == ""
    assert (
        ("普通笔记本 CPU" in ctx["history"][0]["content"])
        if origin == "history"
        else ("四周" in ctx["plan_generation"]["question"]["content"])
    )
