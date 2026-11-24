"""Four-stage authored experience, using real routers and SQLite, offline only."""

import pytest

from tests.test_research_teaching_cases import (
    home,
    tree,
    offline,
    ok,
    choose,
    progress,
    workspace,
)
from core.workspace.conversations import ConversationService
from core.workspace.teaching import TeachingService
from core.workspace.teaching_packages import load_package, validate_package


def start(home, case="fl-heterogeneity", rid="experience"):
    r = ok(
        home.client.post(
            "/api/teaching-cases/start",
            json={
                "request_id": rid,
                "case_id": case,
                "version": "1.1.0",
            },
        )
    )
    return f"/api/projects/{r['project_id']}/conversations/{r['conversation_id']}", r


def test_teaching_experience_capability_is_explicit(home):
    assert (
        ok(home.client.get("/api/conversation-capabilities"))["teaching_experience"]
        == 1
    )


def test_reselecting_same_scope_does_not_fake_a_material_change(home):
    base, result = start(home)
    for option in (
        "begin",
        "continue",
        "compare",
        "adopt_question",
        "small",
        "show_plan",
        "adopt_plan",
    ):
        choose(home, base, option)
    before = workspace(home, result)
    choose(home, base, "scope_back")
    choose(home, base, "small")
    assert workspace(home, result) == before
    assert (
        "范围保持不变"
        in ok(home.client.get(base + "/messages"))["messages"][-1]["content"]
    )


@pytest.mark.parametrize(
    "case,question,first,second",
    [
        ("fl-heterogeneity", "compare", "small", "repeat"),
        ("fl-heterogeneity", "shift", "repeat", "small"),
        ("sulfide-design", "bulk", "narrow", "broader"),
        ("sulfide-design", "interface", "broader", "narrow"),
    ],
)
def test_four_stages_sources_explicit_adoption_and_meaningful_revision(
    home, case, question, first, second
):
    base, result = start(home, case)
    p = load_package(case, "1.1.0")
    assert progress(home, base)["stage_title"] == "明确条件"
    assert (
        next(o for o in progress(home, base)["options"] if o["id"] == "explain")["kind"]
        == "discussion"
    )
    assert (
        next(o for o in progress(home, base)["options"] if o["id"] == "begin")["kind"]
        == "operation"
    )
    assert (
        p["experience"]["scene"]
        in ok(home.client.get(base + "/messages"))["messages"][0]["content"]
    )
    choose(home, base, "interest_other")
    state = progress(home, base)
    assert state["stage_title"] == "了解研究现状"
    assert not workspace(home, result)["items"]
    material = ok(
        home.client.get(
            f"/api/projects/{result['project_id']}/workspace?include_chat=false"
        )
    )
    assert len(material["papers"]) == 3
    assert all(
        p["reading_scope"] and p["relevance"] and p["authors"]
        for p in material["papers"]
    )
    assert {s["paper_id"] for s in state["sources"]} == {
        s["paper_id"] for s in material["papers"]
    }
    before = tree(home.root)
    ok(home.client.get(base + "/messages"))
    progress(home, base)
    assert (
        tree(home.root) == before
    )  # Viewing, including literature, has no scientific write.
    choose(home, base, "continue")
    assert progress(home, base)["stage_title"] == "确认研究问题"
    assert progress(home, base)["options"][0]["id"] == list(p["questions"])[1]
    choose(home, base, question)
    assert workspace(home, result)["items"] == []
    choose(home, base, "adopt_question")
    assert progress(home, base)["stage_title"] == "形成并审阅方案"
    choose(home, base, first)
    choose(home, base, "show_plan")
    state = progress(home, base)
    old_draft = state["plan_suggestion"]
    messages = ok(home.client.get(base + "/messages"))["messages"]
    summary = messages[-1]["content"]
    assert all(
        x in summary
        for x in (
            "要回答什么",
            "准备怎么研究",
            "用什么证据判断",
            "需要哪些条件",
            "还有什么不确定",
        )
    )
    assert "研究目标与明确问题" not in summary  # Whole plan is NOT duplicated in chat.
    assert not any(s["paper_id"] in summary for s in state["sources"])
    assert not any(i["kind"] == "plan" for i in workspace(home, result)["items"])
    choose(home, base, "adopt_plan")
    old = next(i for i in workspace(home, result)["items"] if i["kind"] == "plan")
    choose(home, base, "scope_back")
    choose(home, base, second)
    assert next(
        i for i in workspace(home, result)["items"] if i["item_id"] == old["item_id"]
    )["needs_review"]
    assert not ok(
        home.client.get(
            f"/api/projects/{result['project_id']}/workspace?include_chat=false"
        )
    )["workflow"]["flow"]["planReady"]
    assert (
        "本次修改" in ok(home.client.get(base + "/messages"))["messages"][-1]["content"]
    )
    stale = next(
        s
        for s in ok(home.client.get(base + "/suggestions"))["suggestions"]
        if s["suggestion_id"] == old_draft
    )
    assert stale["applied_item_id"] == old["item_id"]
    choose(home, base, "show_plan")
    fresh = next(
        s
        for s in ok(home.client.get(base + "/suggestions"))["suggestions"]
        if s["suggestion_id"] == progress(home, base)["plan_suggestion"]
    )
    assert fresh["plan"]["method_details"] != old["plan"]["method_details"]
    assert fresh["supersedes_id"] == old["item_id"]
    choose(home, base, "adopt_plan")
    state = progress(home, base)
    assert state["completed"]
    w = workspace(home, result)
    plans = [i for i in w["items"] if i["kind"] == "plan"]
    assert (
        len(plans) == 2
        and plans[0]["status"] == "superseded"
        and plans[1]["plan_version"] == 2
    )
    assert not any(i["kind"] in ("result", "finding", "outline") for i in w["items"])
    chat = ConversationService(
        result["project_id"], home.config.paths.project_db(result["project_id"])
    )
    assert (
        TeachingService(chat).view(result["conversation_id"])["state_version"]
        == state["state_version"]
    )
    exported = home.client.get(base + "/teaching/export").text
    assert all(
        text in exported
        for text in ("1.1.0", "教学设定", "待复核", "未验证", "来源与阅读范围")
    )


@pytest.mark.parametrize("case", ["fl-heterogeneity", "sulfide-design"])
def test_bounded_questions_are_case_specific_persistent_and_never_advance(home, case):
    base, result = start(home, case)
    old = workspace(home, result)
    state = progress(home, base)
    prompts = [
        "为什么选这个问题？",
        "这个术语是什么意思？",
        "范围能不能再小一点？",
        "这些论文能说明什么？",
        "这个方案需要什么条件？",
    ]
    p = load_package(case, "1.1.0")
    for n, (prompt, key) in enumerate(
        zip(prompts, ["why", "terms", "narrow", "evidence", "conditions"])
    ):
        body = {"request_id": f"faq-{n}", "content": prompt}
        reply = ok(home.client.post(base + "/messages", json=body))
        assert p["experience"]["faq"][key] in reply["messages"][-1]["content"]
        assert (
            ok(home.client.post(base + "/messages", json=body))["messages"]
            == reply["messages"]
        )
    assert workspace(home, result) == old
    assert progress(home, base)["state_version"] == state["state_version"]
    for prompt in ("什么是量子纠缠？", "请实时检索最新论文"):
        reply = ok(
            home.client.post(
                base + "/messages", json={"request_id": prompt, "content": prompt}
            )
        )
        assert "未覆盖这个自由问题" in reply["messages"][-1]["content"]
    paused = ok(
        home.client.post(
            base + "/messages", json={"request_id": "pause", "content": "先暂停吧"}
        )
    )
    assert "先停在这里" in paused["messages"][-1]["content"]
    assert progress(home, base)["step"] == 0


def test_new_experience_invalid_descriptions_rejected_without_mutating_published_legacy():
    from copy import deepcopy

    p = load_package("fl-heterogeneity", "1.1.0")
    assert load_package("fl-heterogeneity", "1.0.0")["version"] == "1.0.0"
    for key in ("faq", "source_relations", "question_help"):
        broken = deepcopy(p)
        broken["experience"][key] = {}
        with pytest.raises(ValueError):
            validate_package(broken)
