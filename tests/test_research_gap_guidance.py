"""API, real prompt, durable snapshots and explicit adoption using a provider spy."""
from contextlib import closing
import json
import sqlite3

import pytest

from core.workspace.schema import GuideDraft
from tests.local_backup_fixture import expect
from tests.research_gap_fixture import ABSTRACT, EXCERPT, REQUEST, draft_for, seed
from tests.research_partner_fixture import brief, create, sample, send, workspace


@pytest.fixture
def project(tmp_path):
    with sample(tmp_path) as s:
        seed(s)
        yield s


def reply(s, cid, papers, build):
    def generate(context, text):
        return GuideDraft.model_validate(build(context))
    s["control"]["result"] = generate
    return send(s, cid, REQUEST, selected_paper_ids=papers)


@pytest.mark.parametrize("ids,kind,scopes", [
    (["title-only"], "hypothesis", ["metadata"]),
    (["scope-abstract"], "stated_limitation", ["abstract"]),
    (["scope-excerpt"], "stated_limitation", ["excerpts"]),
    (["scope-abstract", "scope-excerpt"], "comparison", ["abstract", "excerpts"]),
])
def test_gap_scope_snapshot_and_bound_candidate(project, ids, kind, scopes):
    s = project
    cid = create(s, discussion_step="synthesis")
    before = workspace(s)
    result = expect(reply(s, cid, ids, lambda c: draft_for(c, kind)))
    call = s["calls"][-1]
    assert call["text"] == REQUEST and "【本轮研究缺口】" in call["system"]
    assert "saved_reading" in call["context"] and "paper_inventory 只是目录" in call["system"]
    assert [p["content_available"] for p in call["context"]["papers"]] == scopes
    assert call["context"]["brief"] == before["brief"]
    message = result["messages"][-1]
    candidate = result["suggestions"][0]
    gap = message["research_gaps"][0]
    assert gap == candidate["gap_snapshot"]
    assert gap["kind"] == kind and message["research_gap_basis_changed"] is False
    if kind == "hypothesis":
        assert "仅提供题名和元信息" in gap["reading_note"]
    else:
        assert gap["reading_note"] is None
    assert [e["quote"] for e in gap["sources"]] == [
        quote for source, quote in (("scope-abstract", ABSTRACT), ("scope-excerpt", EXCERPT)) if source in ids
    ]
    assert [e["page"] for e in gap["sources"]] == [7 if e["part"] == "excerpt" else None for e in gap["sources"]]
    assert candidate["paper_ids"] == [i for i in ids if i != "title-only"]
    assert not candidate["applied_item_id"] and workspace(s) == before
    assert (gap["existing_work"] is None) == (kind == "hypothesis")
    url = f'/api/projects/{s["pid"]}/conversations/{cid}'
    assert expect(s["a"].get(url + "/messages?tail=true"))["messages"][-1]["research_gaps"] == [gap]
    assert s["b"].get(url + "/messages").status_code == 404
    accepted = expect(s["a"].post(url + f'/suggestions/{candidate["suggestion_id"]}/apply',
                                  json={"expected_revision": before["revision"]}))
    assert accepted["item"]["paper_ids"] == candidate["paper_ids"]
    assert accepted["item"]["kind"] == "question"
    assert workspace(s)["items"] == [accepted["item"]]


@pytest.mark.parametrize("fault", [
    "invented_quote", "wrong_part", "title_claim", "inventory_only", "inventory_reply", "other_project", "other_account",
    "only_one_for_comparison", "bad_link", "novelty_claim",
])
def test_invalid_gap_does_not_publish_or_adopt(project, fault):
    s = project
    cid = create(s)
    before = workspace(s)
    def bad(context):
        d = draft_for(context, "stated_limitation")
        gap = d["research_gaps"][0]
        if fault == "title_claim":
            d = draft_for(context, "hypothesis")
            d["reply"] = "该论文采用了对照实验，实验结果证明了假设。"
        elif fault == "invented_quote":
            gap["sources"][0]["quote"] = "论文报告的数字为 999。"
        elif fault == "wrong_part":
            gap["sources"][0]["part"] = "excerpt"
        elif fault == "inventory_reply":
            d["cited_paper_ids"] = ["abstract-only"]
        elif fault in {"inventory_only", "other_project", "other_account"}:
            gap["sources"][0]["paper_id"] = {"inventory_only": "abstract-only",
                "other_project": "other-project-source", "other_account": "other-account-source"}[fault]
        elif fault == "only_one_for_comparison":
            gap["kind"] = "comparison"
        elif fault == "bad_link":
            d["suggestions"][0]["gap_link"]["gap_index"] = 2
        else:
            gap["summary"] = "此前没有人研究这个问题。"
        return d
    response = reply(s, cid, ["title-only" if fault == "title_claim" else "scope-abstract"], bad)
    assert response.status_code == 422
    assert len(s["calls"]) == 1
    url = f'/api/projects/{s["pid"]}/conversations/{cid}'
    assert not expect(s["a"].get(url + "/suggestions"))["suggestions"]
    assert all(m["role"] != "assistant" for m in expect(s["a"].get(url + "/messages"))["messages"])
    result = expect(s["a"].get(url + "/requests/turn"))
    assert result["failure"]["reason"] == "structure_invalid"
    assert not result["failure"]["can_retry"] and workspace(s) == before


def test_standalone_gap_is_read_only_and_replays_without_new_generation(project):
    s = project
    cid = create(s, discussion_step="synthesis")
    before = workspace(s)
    def standalone(context):
        draft = draft_for(context, "stated_limitation")
        draft["suggestions"] = []
        return draft
    first = expect(reply(s, cid, ["scope-excerpt"], standalone))
    second = expect(reply(s, cid, ["scope-excerpt"], standalone))
    assert first["messages"] == second["messages"]
    assert first["messages"][-1]["research_gaps"][0]["sources"][0]["quote"] == EXCERPT
    assert not first["suggestions"] and len(s["calls"]) == 1 and workspace(s) == before


def test_history_keeps_original_evidence_but_needs_review(project):
    s = project
    cid = create(s)
    result = expect(reply(s, cid, ["scope-abstract"], lambda c: draft_for(c, "stated_limitation")))
    old = result["messages"][-1]
    candidate = result["suggestions"][0]
    brief(s, resources="新的条件仍待核对")
    url = f'/api/projects/{s["pid"]}/conversations/{cid}'
    projected = expect(s["a"].get(url + "/guidance-actions", params={"message_ids": old["message_id"]}))
    assert projected["research_gap_basis_changed"][old["message_id"]] is True
    read = expect(s["a"].get(url + "/messages"))["messages"][-1]
    assert read["research_gap_basis_changed"] and read["research_gaps"] == old["research_gaps"]
    assert s["a"].post(url + f'/suggestions/{candidate["suggestion_id"]}/apply',
                       json={"expected_revision": workspace(s)["revision"]}).status_code == 409
    confused = expect(send(s, cid, "没看懂，先用简单的话解释", rid="explain", selected_paper_ids=["scope-abstract"]))
    explained = confused["messages"][-1]
    assert not explained["research_gaps"] and not explained["suggestion_ids"] and not explained["actions"]
    assert "【本轮研究缺口】" not in s["calls"][-1]["system"]
    previous = s["calls"][-1]["context"]["history"][-1]["prior_gap_discussion"]
    assert previous["basis_changed"] and "不能替代" in previous["notice"]
    assert s["state"]["source_calls"] == s["state"]["public_calls"] == 0


def test_old_candidate_and_formal_question_still_work(project):
    s = project
    cid = create(s)
    s["control"]["result"] = GuideDraft(reply="只提供旧格式问题。", suggestions=[
        {"kind": "question", "title": "旧格式", "content": "怎样做公平比较？"}
    ])
    result = expect(send(s, cid, "请给出一个候选问题"))
    candidate = result["suggestions"][0]
    # Model an existing database row from before these fields were introduced.
    db = s["api"]._CONFIG.paths.project_db(s["pid"])
    with closing(sqlite3.connect(db)) as conn:
        for seq, raw in conn.execute("SELECT seq,data FROM sra_chat_messages WHERE conversation_id=?", (cid,)):
            message = json.loads(raw)
            message.pop("research_gaps", None)
            message.pop("research_gap_fingerprint", None)
            conn.execute("UPDATE sra_chat_messages SET data=? WHERE conversation_id=? AND seq=?",
                         (json.dumps(message), cid, seq))
        sid = candidate["suggestion_id"]
        raw = conn.execute("SELECT data FROM sra_chat_suggestions WHERE suggestion_id=?", (sid,)).fetchone()[0]
        legacy = json.loads(raw)
        legacy.pop("gap_link", None)
        legacy.pop("gap_snapshot", None)
        conn.execute("UPDATE sra_chat_suggestions SET data=? WHERE suggestion_id=?", (json.dumps(legacy), sid))
        conn.commit()
    url = f'/api/projects/{s["pid"]}/conversations/{cid}'
    assert "research_gaps" not in expect(s["a"].get(url + "/messages"))["messages"][-1]
    assert "gap_link" not in expect(s["a"].get(url + "/suggestions"))["suggestions"][0]
    accepted = expect(s["a"].post(url + f'/suggestions/{candidate["suggestion_id"]}/apply',
                                  json={"expected_revision": workspace(s)["revision"]}))
    s["control"].clear()
    plan = expect(send(s, cid, "请生成研究方案。", rid="plan",
                       plan_request={"method": "computational", "question_id": accepted["item"]["item_id"]}))
    assert plan["suggestions"][0]["plan"]["question_id"] == accepted["item"]["item_id"]
