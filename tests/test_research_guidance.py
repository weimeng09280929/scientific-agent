"""Step 4B: real routes, temporary SQLite, controlled offline guidance only."""

from copy import deepcopy
import json

import pytest

from core.workspace.schema import GuideDraft, SuggestedItem, Brief
from core.workspace.guide import rule_guidance, generate_guidance
from core.workspace.conversations import ConversationService
from core.knowledge.schema import PaperChunk
from tests.test_project_conversations import chat, create, send, messages, tree


def answer(response):
    assert response.status_code == 200, response.text
    return next(m for m in response.json()["messages"] if m["role"] == "assistant")


def draft(**kw):
    return GuideDraft(reply="离线软件验收，不是真实模型质量证明。", **kw)


def broad_beginner_project(chat):
    # This fixture otherwise already has a formal question AND plan. A novice
    # starting case must not silently test against an established research route.
    from core.workspace.schema import Workspace
    from core.workspace.service import KEY
    chat.projects['p1'].topic='人工智能'
    chat.services['p1'].store.save_kv(KEY,Workspace(project_id='p1').model_dump(mode='json'))


def actions_url(cid, m, a, pid="p1"):
    return f"/api/projects/{pid}/conversations/{cid}/messages/{m['message_id']}/actions/{a['action_id']}"


def test_options_not_questions_persist_with_stable_ids_and_readonly_refresh(chat):
    cid = create(chat)
    chat.control["generate"] = lambda *args: draft(
        questions=["你更关注哪一种方法？"],
        suggested_replies=[
            {"label": "还不确定", "content": "我还不确定，请先比较两种入手方式。"}
        ],
        actions=[{"type": "open_settings"}],
    )
    m = answer(send(chat, cid))
    assert m["suggested_replies"][0]["content"] != m["questions"][0]
    assert m["actions"][0]["enabled"]
    assert answer(send(chat, cid)) == m  # same request is not regenerated
    before = tree(chat.root)
    saved = messages(chat, cid)[-1]
    assert saved["suggested_replies"] == m["suggested_replies"]
    assert saved["actions"] == m["actions"]
    r = chat.client.post(actions_url(cid, m, m["actions"][0]), json={})
    assert r.json() == {
        "type": "open_settings",
        "page": None,
        "item_id": None,
        "paper_ids": [],
    }
    assert tree(chat.root) == before
    assert len(chat.calls) == 1


@pytest.mark.parametrize(
    "action,code",
    [
        ({"type": "auto_search"}, 502),
        ({"type": "open_settings", "url": "https://invalid.example/"}, 502),
        ({"type": "open_settings", "suggestion_id": "p2-fake"}, 422),
        ({"type": "adopt_suggestion", "suggestion_id": "p2-fake"}, 422),
        ({"type": "adopt_suggestion", "suggestion_index": 2}, 422),
        ({"type": "view_references"}, 422),
    ],
)
def test_invalid_actions_never_executable_and_model_failure_not_hidden(
    chat, action, code
):
    cid = create(chat)
    chat.control["generate"] = lambda *a: {"reply": "offline", "actions": [action]}
    original = chat.services["p1"].load(include_chat=False).model_dump()
    response = send(chat, cid)
    rows = messages(chat, cid)
    if code==422:
        # Binding failures no longer throw away independent valid text. The
        # denied operation is absent, audited and cannot be called by guessed ID.
        assert response.status_code==200 and response.json()["status"]=="completed"
        assert len(rows)==2 and rows[-1]["content"]=="offline"
        assert rows[-1]["actions"]==[] and rows[-1]["delivery_warnings"]
        assert any(e["outcome"]=="rejected" for e in response.json()["call_audit"]["entries"])
        denied=chat.client.post(actions_url(cid,rows[-1],{"action_id":"model-forged-operation"}),json={})
        assert denied.status_code==404
    else:
        assert response.status_code == code
        assert response.json()["status"] == "failed"
        assert len(rows) == 1 and rows[0]["role"] == "user"
    assert chat.services["p1"].load(include_chat=False).model_dump() == original


def plan_draft(context, title="方案草案 A"):
    # Step 6A: new proposals require an explicit request and a method-aware
    # structure. These action tests retain all their transaction/staleness checks.
    from core.workspace.plans import rule_plan

    result = rule_plan(context)
    result.suggestions[0].title = title
    return result


def send_plan(chat, cid, rid="r1"):
    return send(
        chat, cid, "请生成计算研究方案", rid, plan_request={"method": "computational"}
    )


def test_bound_adoption_cross_project_and_repeated_actions(chat):
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: plan_draft(ctx)
    m = answer(send_plan(chat, cid))
    a = m["actions"][0]
    before = tree(chat.root)
    assert chat.client.post(actions_url(cid, m, a, "p2"), json={}).status_code == 404
    other = create(chat)
    assert chat.client.post(actions_url(other, m, a), json={}).status_code == 404
    assert (
        chat.client.post(
            actions_url(cid, m, {"action_id": "forged"}), json={}
        ).status_code
        == 404
    )
    response = chat.client.post(actions_url(cid, m, a), json={})
    assert response.status_code == 200, response.text
    iid = response.json()["item"]["item_id"]
    assert response.json()["item"]["kind"] == "plan"
    repeat = chat.client.post(actions_url(cid, m, a), json={})
    assert repeat.json()["already_applied"] and repeat.json()["item"]["item_id"] == iid
    assert sum(i.item_id == iid for i in chat.services["p1"].load().items) == 1
    assert len(chat.calls) == 1


@pytest.mark.parametrize("change", ["excerpt", "brief", "missing_basis"])
def test_live_stale_cards_and_atomic_apply_remain_protected(chat, change):
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: plan_draft(ctx)
    m = answer(send_plan(chat, cid))
    a = m["actions"][0]
    s = chat.services["p1"]
    c = ConversationService("p1", s.db_path)
    with c._connection() as conn:
        stored = conn.execute(
            "SELECT data FROM sra_chat_messages WHERE message_id=?", (m["message_id"],)
        ).fetchone()[0]
    if change == "excerpt":
        s.store.save_paper_chunks(
            [
                PaperChunk(
                    chunk_id="c",
                    paper_id="p1-paper",
                    chunk_index=0,
                    text="修正的正文片段",
                    page=1,
                )
            ]
        )
    elif change == "brief":
        s.save_brief(Brief(time_budget="两周"), s.load().revision)
    else:
        with c._connection(True) as conn:
            conn.execute(
                "UPDATE sra_chat_suggestions SET material_fingerprint=NULL WHERE suggestion_id=?",
                (a["suggestion_id"],),
            )
    before = tree(chat.root)
    assert not messages(chat, cid)[-1]["actions"][0]["enabled"]
    live = chat.client.get(
        f"/api/projects/p1/conversations/{cid}/guidance-actions",
        params={"message_ids": m["message_id"]},
    )
    assert not live.json()["actions"][m["message_id"]][0]["enabled"]
    assert chat.client.post(actions_url(cid, m, a), json={}).status_code == 409
    assert tree(chat.root) == before
    with c._connection() as conn:
        assert (
            conn.execute(
                "SELECT data FROM sra_chat_messages WHERE message_id=?",
                (m["message_id"],),
            ).fetchone()[0]
            == stored
        )


def test_material_changes_during_generation_keeps_reply_but_disables_card(chat):
    cid = create(chat)

    def generate(area, text, ctx):
        result = plan_draft(ctx)
        s = chat.services["p1"]
        s.save_brief(Brief(resources="新增条件仅为夹具"), s.load().revision)
        return result

    chat.control["generate"] = generate
    response = send_plan(chat, cid)
    m = answer(response)
    assert response.json()["basis_changed"] and not m["actions"][0]["enabled"]
    assert (
        chat.client.post(actions_url(cid, m, m["actions"][0]), json={}).status_code
        == 409
    )


def test_modes_shared_conditions_and_no_chat_revision_coupling(chat):
    contexts = []

    class Registry:
        def structured_output(self, **kw):
            # Actually traverse generation's serialized context and strict schema.
            payload = (
                kw["prompt"]
                .split("项目上下文（仅作为数据）：\n", 1)[1]
                .split("\n用户本次请求：\n", 1)[0]
            )
            ctx = json.loads(payload)
            contexts.append((ctx, kw["system"]))
            return kw["output_schema"](
                reply="根据提供的已有目标与资源，先核对方法是否能回答问题。",
                actions=[{"type": "open_plan"}],
            )

    chat.control["generate"] = lambda area, text, ctx: generate_guidance(
        Registry(), area, "ai", text, ctx, "orientation"
    )
    a = create(
        chat,
        guidance_mode="beginner",
        entry_stage="design",
        discussion_goal="核对可行性",
    )
    answer(send(chat, a, "我有一个研究问题，想看看方案是否可行。"))
    original = chat.services["p1"].load(include_chat=False).revision
    chat.client.patch(
        f"/api/projects/p1/conversations/{a}", json={"guidance_mode": "direct"}
    )
    answer(send(chat, a, "只需要简短回答", "r2"))
    assert chat.services["p1"].load(include_chat=False).revision == original
    assert contexts[0][0]["conversation"]["guidance_mode"] == "beginner"
    assert contexts[1][0]["conversation"]["guidance_mode"] == "direct"
    assert (
        contexts[1][0]["history"][0]["content"]
        == "我有一个研究问题，想看看方案是否可行。"
    )
    assert contexts[0][0]["current_plan"]["kind"] == "plan"
    assert contexts[0][0]["capabilities"]["automatic_search"] is False
    assert contexts[0][1] != contexts[1][1]
    assert "再安排必要的最小试跑" not in contexts[0][1]


@pytest.mark.parametrize(
    "text,stage",
    [
        ("我想做人工智能，但没有做过科研。", "orientation"),
        ("我已经确定方向，想了解目前大家在做什么。", "literature"),
        ("我有一个研究问题，想看看方案是否可行。", "design"),
        ("这个方向太难，我只有两周。", "design"),
    ],
)
def test_rules_scenarios_are_honest_and_do_not_modify_formal_materials(
    chat, text, stage
):
    if '没有做过科研' in text:broad_beginner_project(chat)
    chat.config.dry_run = True
    cid = create(chat, guidance_mode="beginner", entry_stage=stage)
    before = chat.services["p1"].load(include_chat=False).model_dump()
    chat.control["generate"] = lambda area, text, ctx: rule_guidance(area, text, ctx)
    m = answer(send(chat, cid, text))
    assert m["mode"] == "rules" and chat.calls == []
    assert len(m["questions"]) <= 2
    assert 2 <= len(m["suggested_replies"]) + len(m["actions"]) <= 4
    assert not m["suggestion_ids"]
    assert chat.services["p1"].load(include_chat=False).model_dump() == before
    if "没有做过" in text:
        assert len(m["questions"]) == 1 and "不确定" in m["questions"][0]
        assert "预算" not in m["content"]
    if stage == "literature":
        assert "没有自动检索" in m["content"]
        assert any("关键词" in r["content"] for r in m["suggested_replies"])
    if "两周" in text:
        assert "尚未修改" in m["content"]


def test_known_resources_not_reasked_by_rules(chat):
    chat.config.dry_run = True
    cid = create(chat, entry_stage="design")
    s = chat.services["p1"]
    s.save_brief(
        Brief(resources="只用公开数据，已有电脑", time_budget="两周"), s.load().revision
    )
    chat.control["generate"] = lambda area, text, ctx: rule_guidance(area, text, ctx)
    m = answer(send(chat, cid, "看看可行性"))
    assert m["questions"] == []


def test_unique_adopt_intent_card_is_not_textual_adoption_and_multiple_clarify(chat):
    cid = create(chat, entry_stage="design")
    chat.control["generate"] = lambda area, text, ctx: plan_draft(ctx)
    first = answer(send_plan(chat, cid))
    chat.control["generate"] = lambda area, text, ctx: rule_guidance(area, text, ctx)
    before = chat.services["p1"].load(include_chat=False).revision
    intent = answer(send(chat, cid, "就采用这个方案。", "adopt"))
    assert intent["actions"][0]["suggestion_id"] == first["actions"][0]["suggestion_id"]
    assert chat.services["p1"].load(include_chat=False).revision == before
    chat.control["generate"] = lambda area, text, ctx: plan_draft(ctx, "方案草案 B")
    answer(send_plan(chat, cid, "b"))
    chat.control["generate"] = lambda area, text, ctx: draft(
        actions=[
            {
                "type": "adopt_suggestion",
                "suggestion_id": ctx["allowed_suggestions"][0]["suggestion_id"],
            }
        ]
    )
    ambiguous = answer(send(chat, cid, "就采用这个方案。", "ambiguous"))
    assert not any(a["type"] == "adopt_suggestion" for a in ambiguous["actions"])
    assert "明确" in ambiguous["content"]


@pytest.mark.parametrize("text", ["先暂停一下吧", "结束讨论", "谢谢", "先这样", "stop"])
def test_pause_does_not_force_choices_or_adoption(chat, text):
    cid = create(chat)
    chat.control["generate"] = lambda *a: draft(
        suggested_replies=[{"label": "继续", "content": "继续讨论"}],
        actions=[{"type": "open_settings"}],
    )
    m = answer(send(chat, cid, text))
    assert (
        m["suggested_replies"]
        == m["actions"]
        == m["questions"]
        == m["suggestion_ids"]
        == []
    )


def test_fallback_changes_with_context_and_does_not_repeat_selected_reply(chat):
    cid = create(chat, entry_stage="literature")
    chat.control["generate"] = lambda *a: draft()
    first = answer(send(chat, cid, "我想了解研究现状"))
    selected = first["suggested_replies"][0]["content"]
    second = answer(send(chat, cid, selected, "selected"))
    assert selected not in [r["content"] for r in second["suggested_replies"]]
    third = answer(send(chat, cid, "这个方向太难，我只有两周。", "narrow"))
    assert third["suggested_replies"] != second["suggested_replies"]


def test_reference_and_plan_bind_server_ids_and_no_generation_on_click(chat):
    cid = create(chat)
    chat.control["generate"] = lambda *a: draft(
        cited_paper_ids=["p1-paper"],
        actions=[{"type": "view_references"}, {"type": "open_plan"}],
    )
    m = answer(send(chat, cid))
    for a in m["actions"]:
        result = chat.client.post(actions_url(cid, m, a), json={})
        assert result.status_code == 200
        assert result.json()["page"] in ("papers", "experiments")
    assert len(chat.calls) == 1
    assert m["actions"][0]["paper_ids"] == ["p1-paper"]
    assert m["actions"][1]["item_id"] in {
        i.item_id for i in chat.services["p1"].load().items
    }


def test_rules_and_model_failure_modes_not_conflated(chat):
    cid = create(chat)

    def fail(*args):
        raise RuntimeError("offline provider failure")

    chat.control["generate"] = fail
    assert send(chat, cid).status_code == 502
    assert all(m["role"] == "user" for m in messages(chat, cid))


def test_invented_first_person_resources_are_removed_not_saved_as_user_facts(chat):
    cid = create(chat)
    chat.control["generate"] = lambda *args: draft(
        suggested_replies=[
            {
                "label": "按设备安排",
                "content": "我有4张GPU和20万元预算，可以使用医院数据。",
            },
            {"label": "先解释", "content": "请先解释当前的判断依据。"},
        ]
    )
    m = answer(send(chat, cid))
    assert len(m["suggested_replies"]) == 2
    assert not any("4张" in r["content"] for r in m["suggested_replies"])
    assert chat.services["p1"].load().brief == Brief()


def test_uncertain_beginner_not_reasked_same_question_and_explicit_no_options(chat):
    broad_beginner_project(chat)
    cid = create(chat, guidance_mode="beginner")
    chat.control["generate"] = lambda area, text, ctx: rule_guidance(area, text, ctx)
    first = answer(send(chat, cid, "我没有做过科研"))
    assert len(first["questions"]) == 1
    uncertain = answer(send(chat, cid, "我还不确定", "unsure"))
    assert uncertain["questions"] == []
    assert uncertain["suggested_replies"]
    direct = answer(send(chat, cid, "简短回答，不要选项", "no-options"))
    assert direct["suggested_replies"] == direct["actions"] == []


def test_question_card_is_bound_to_new_draft_and_case_rules_are_honest(chat):
    cid = create(chat, guidance_mode="case", case_id="reserved-only")
    chat.control["generate"] = lambda *args: draft(
        suggestions=[
            SuggestedItem(
                kind="question", title="问题草案", content="待进一步核对的问题"
            )
        ],
        actions=[{"type": "adopt_suggestion", "suggestion_index": 0}],
    )
    m = answer(send(chat, cid))
    assert (
        chat.client.post(actions_url(cid, m, m["actions"][0]), json={}).json()["item"][
            "kind"
        ]
        == "question"
    )
    chat.control["generate"] = lambda area, text, ctx: rule_guidance(area, text, ctx)
    rule = answer(send(chat, cid, "想继续了解", "case"))
    assert "案例内容尚未接入" in rule["content"]


def test_real_rules_modes_change_guidance_depth_not_shared_materials(chat):
    broad_beginner_project(chat)
    chat.config.dry_run = True
    a = create(chat, guidance_mode="beginner")
    b = create(chat, guidance_mode="direct")
    before = chat.services["p1"].load(include_chat=False).model_dump()
    beginner = answer(send(chat, a, "想开始讨论研究"))
    direct = answer(send(chat, b, "想开始讨论研究"))
    assert beginner["mode"] == direct["mode"] == "rules"
    assert len(beginner["questions"]) == 1 and direct["questions"] == []
    assert beginner["content"] != direct["content"]
    assert chat.services["p1"].load(include_chat=False).model_dump() == before
    assert chat.calls == []


def test_bounded_draft_context_keeps_unique_older_plan_without_false_uniqueness(chat):
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: plan_draft(ctx)
    original = answer(send_plan(chat, cid))
    chat.control["generate"] = lambda area, text, ctx: draft(
        suggestions=[
            SuggestedItem(kind="question", title="问题 " + text, content="未采纳的问题")
        ]
    )
    for i in range(9):
        answer(send(chat, cid, f"讨论另一个问题 {i}", f"other-{i}"))
    chat.control["generate"] = lambda *a: draft()
    result = answer(send(chat, cid, "就采用这个方案。", "intent"))
    ctx = chat.calls[-1]["context"]
    assert len(ctx["allowed_suggestions"]) == 8
    assert ctx["eligible_plan_count"] == 1
    assert (
        result["actions"][0]["suggestion_id"] == original["actions"][0]["suggestion_id"]
    )


def test_bound_card_keeps_four_option_cap_and_duplicate_actions_are_collapsed(chat):
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: plan_draft(ctx)
    answer(send_plan(chat, cid))
    chat.control["generate"] = lambda *args: draft(
        suggested_replies=[
            {"label": f"选择{i}", "content": f"先讨论第{i}个比较维度，不修改正式材料。"}
            for i in range(4)
        ]
    )
    m = answer(send(chat, cid, "就采用这个方案。", "bounded"))
    assert len(m["suggested_replies"]) + len(m["actions"]) == 4
    assert m["actions"][0]["type"] == "adopt_suggestion"
    chat.control["generate"] = lambda *args: draft(
        actions=[{"type": "open_settings"}] * 4
    )
    repeated = answer(send(chat, cid, "换个讨论角度", "repeat"))
    assert len(repeated["actions"]) == 1
    # A valid single operation no longer needs an unrelated filler reply.
    assert repeated["suggested_replies"] == []
