"""Multi-conversation contracts. Temporary SQLite, offline models, no user data."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.config import GlobalConfig, ProjectPaths
from core.knowledge.schema import Paper, PaperChunk, Idea
from core.workspace.schema import (
    Brief,
    GuideDraft,
    ItemInput,
    Message,
    Suggestion,
    SuggestedItem,
)
from core.workspace.service import WorkspaceService, KEY
from core.workspace.conversation_schema import ConversationCreate, ConversationSend
from core.workspace.conversations import ConversationService
from web.workspace_api import build_workspace_router


def tree(root):
    return {
        str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from web import workspace_api

    config = GlobalConfig(paths=ProjectPaths.from_root(tmp_path), dry_run=False)
    services = {}
    projects = {}
    for pid in ("p1", "p2"):
        s = WorkspaceService(pid, config.paths.project_db(pid))
        services[pid] = s
        s.store.save_paper(
            Paper(
                paper_id=pid + "-paper",
                title="Offline fixture " + pid,
                abstract="Not scientific evidence",
            )
        )
        q = s.add_item(
            ItemInput(
                kind="question",
                title=pid + " question",
                content="待核查的问题",
                paper_ids=[pid + "-paper"],
            ),
            0,
        ).items[-1]
        s.add_item(
            ItemInput(
                kind="plan",
                title=pid + " plan",
                content="尚未执行的方法方案",
                related_ids=[q.item_id],
            ),
            1,
        )
        projects[pid] = SimpleNamespace(
            project_id=pid, topic=pid + " offline project", domain="ai", notes=[]
        )
    calls = []
    control = {}

    def generate(registry, area, domain, text, context, purpose):
        calls.append({"area": area, "text": text, "context": deepcopy(context)})
        if control.get("generate"):
            return control["generate"](area, text, context)
        return GuideDraft(
            reply="仅为离线软件测试回复：" + text,
            cited_paper_ids=[context["papers"][0]["paper_id"]],
            suggestions=[
                SuggestedItem(title="草案 " + text[:20], content="待用户核对的笔记")
            ],
        )

    monkeypatch.setattr(workspace_api, "generate_guidance", generate)

    def require(pid):
        if pid not in projects:
            raise HTTPException(404, "项目不存在")
        return projects[pid]

    app = FastAPI()
    app.include_router(
        build_workspace_router(config, require, registry_factory=lambda: object())
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client,
            services=services,
            projects=projects,
            calls=calls,
            control=control,
            config=config,
            root=tmp_path,
        )


def create(chat, pid="p1", **extra):
    response = chat.client.post(
        f"/api/projects/{pid}/conversations",
        json={"title": "Offline conversation", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()["conversation"]["conversation_id"]


def send(chat, cid, text="hello", rid="r1", pid="p1", **extra):
    return chat.client.post(
        f"/api/projects/{pid}/conversations/{cid}/messages",
        json={"content": text, "request_id": rid, **extra},
    )


def messages(chat, cid, pid="p1", **params):
    return chat.client.get(
        f"/api/projects/{pid}/conversations/{cid}/messages", params=params
    ).json()["messages"]


def apply(chat, cid, sid, **body):
    return chat.client.post(
        f"/api/projects/p1/conversations/{cid}/suggestions/{sid}/apply", json=body
    )


def test_shared_materials_but_isolated_histories_and_settings(chat):
    a = create(
        chat,
        guidance_mode="beginner",
        entry_stage="design",
        discussion_goal="learn design",
    )
    b = create(
        chat, guidance_mode="case", entry_stage="literature", case_id="future-contract"
    )
    focus = chat.services["p1"].load().focus.model_dump()
    revision = chat.services["p1"].load().revision
    assert send(chat, a, "only conversation A").status_code == 200
    assert send(chat, b, "only conversation B").status_code == 200
    assert send(chat, a, "continue A", rid="r2").status_code == 200
    c1, c2, c3 = [call["context"] for call in chat.calls]
    assert c1["history"] == [] and c2["history"] == []
    assert any("only conversation A" in m["content"] for m in c3["history"])
    assert not any("only conversation B" in m["content"] for m in c3["history"])
    for c in (c1, c2, c3):
        assert {r["kind"] for r in c["records"]} == {"question", "plan"}
        assert c["papers"][0]["paper_id"] == "p1-paper"
    assert (
        c1["conversation"]["guidance_mode"] == "beginner"
        and c1["conversation"]["entry_stage"] == "design"
    )
    assert c2["conversation"]["case_available"] is False
    assert chat.services["p1"].load().revision == revision
    assert chat.services["p1"].load().focus.model_dump() == focus
    assert (
        chat.services["p1"].load().messages == []
    )  # New chats never flatten into legacy history.


def test_creation_idempotence_conflict_and_metadata_revision(chat):
    cid = create(chat, request_id="create-once")
    assert create(chat, request_id="create-once") == cid
    before = tree(chat.root)
    r = chat.client.post(
        "/api/projects/p1/conversations",
        json={"title": "different", "request_id": "create-once"},
    )
    assert r.status_code == 409 and tree(chat.root) == before
    revision = chat.services["p1"].load().revision
    focus = chat.services["p1"].load().focus
    r = chat.client.patch(
        f"/api/projects/p1/conversations/{cid}",
        json={
            "title": "renamed",
            "discussion_goal": "new goal",
            "guidance_mode": "beginner",
            "entry_stage": "question",
            "case_id": None,
        },
    )
    assert r.status_code == 200 and r.json()["conversation"]["title"] == "renamed"
    assert (
        chat.services["p1"].load().revision == revision
        and chat.services["p1"].load().focus == focus
    )
    assert not chat.calls


def test_duplicate_send_is_local_idempotent_even_with_old_revision(chat):
    cid = create(chat)
    a = send(chat, cid)
    b = send(chat, cid, expected_revision=0)
    assert a.status_code == b.status_code == 200
    assert a.json()["messages"] == b.json()["messages"] and len(chat.calls) == 1
    assert len(messages(chat, cid)) == 2
    before = tree(chat.root)
    assert send(chat, cid, "different content").status_code == 409
    assert tree(chat.root) == before


@pytest.mark.parametrize(
    "invalid",
    [
        {"selected_item_id": "foreign"},
        {"selected_paper_ids": ["p2-paper"]},
        {"area": "writing"},
        {"expected_revision": 0},
        {"content": "   "},
    ],
)
def test_invalid_send_does_not_write(chat, invalid):
    cid = create(chat)
    before = tree(chat.root)
    response = (
        send(chat, cid, **invalid)
        if "content" not in invalid
        else chat.client.post(
            f"/api/projects/p1/conversations/{cid}/messages",
            json={"request_id": "r1", **invalid},
        )
    )
    assert response.status_code in (409, 422), response.text
    assert tree(chat.root) == before and not chat.calls


def test_cross_project_conversation_record_and_suggestion_ownership(chat):
    a = create(chat)
    b = create(chat, "p2")
    first = send(chat, a).json()
    foreign_sid = first["suggestions"][0]["suggestion_id"]
    q2 = chat.services["p2"].load().items[0].item_id
    before = tree(chat.root)
    assert send(chat, a, pid="p2").status_code == 404
    assert send(chat, b, pid="p1").status_code == 404
    assert send(chat, a, rid="bad-ref", selected_item_id=q2).status_code == 422
    assert (
        chat.client.get(f"/api/projects/p2/conversations/{a}/messages").status_code
        == 404
    )
    assert (
        chat.client.post(
            f"/api/projects/p2/conversations/{b}/suggestions/{foreign_sid}/apply",
            json={},
        ).status_code
        == 404
    )
    assert (
        chat.client.post("/api/projects/foreign/conversations", json={}).status_code
        == 404
    )
    assert tree(chat.root) == before


def test_failure_keeps_user_and_explicit_retry_does_not_duplicate(chat):
    cid = create(chat)

    def broken(*a):
        raise RuntimeError("offline failure")

    chat.control["generate"] = broken
    first = send(chat, cid)
    assert first.status_code == 502 and first.json()["status"] == "failed"
    assert (
        len(messages(chat, cid)) == 1
        and messages(chat, cid)[0]["request_status"] == "failed"
    )
    assert send(chat, cid).status_code == 502 and len(chat.calls) == 1
    chat.control.clear()
    retried = chat.client.post(
        f"/api/projects/p1/conversations/{cid}/requests/r1/retry", json={}
    )
    assert retried.status_code == 200 and retried.json()["attempt"] == 2
    assert [m["role"] for m in messages(chat, cid)] == ["user", "assistant"]
    assert (
        chat.client.post(
            f"/api/projects/p1/conversations/{cid}/requests/r1/retry", json={}
        ).status_code
        == 200
    )
    assert len(chat.calls) == 2


def orphan(chat, cid):
    service = ConversationService("p1", chat.config.paths.project_db("p1"))

    def crash(*a):
        raise SystemExit("software fixture interruption")

    with pytest.raises(SystemExit):
        service.send(
            cid,
            ConversationSend(content="interrupted", request_id="crash"),
            "offline",
            crash,
        )
    return service


def test_restart_status_is_derived_without_get_writes_or_automatic_calls(chat):
    cid = create(chat)
    service = orphan(chat, cid)
    before = tree(chat.root)
    for _ in range(3):
        state = chat.client.get(
            f"/api/projects/p1/conversations/{cid}/requests/crash"
        ).json()
        assert state["status"] == "interrupted"
    assert tree(chat.root) == before and not chat.calls
    with service._connection() as conn:
        assert service._request(conn, cid, "crash")["status"] == "processing"
    reply = chat.client.post(
        f"/api/projects/p1/conversations/{cid}/requests/crash/retry", json={}
    )
    assert reply.status_code == 200 and reply.json()["attempt"] == 2
    assert len(messages(chat, cid)) == 2 and len(chat.calls) == 1


def test_another_live_process_is_not_treated_as_interrupted(chat):
    cid = create(chat)
    orphan(chat, cid)
    code = "import sys;from pathlib import Path;from core.workspace.conversation_lock import hold\nwith hold(Path(sys.argv[1]),sys.argv[2]) as acquired:\n print('READY' if acquired else 'FAILED',flush=True)\n sys.stdin.readline()"
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(chat.config.paths.project_db("p1")), cid],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "READY"
        state = chat.client.get(
            f"/api/projects/p1/conversations/{cid}/requests/crash"
        ).json()
        assert state["status"] == "processing"
        assert send(chat, cid, "new while live", rid="new").status_code == 409
        assert (
            chat.client.post(
                f"/api/projects/p1/conversations/{cid}/requests/crash/retry", json={}
            ).status_code
            == 202
        )
        assert not chat.calls
    finally:
        child.terminate()
        child.wait(timeout=15)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()
    assert (
        chat.client.get(f"/api/projects/p1/conversations/{cid}/requests/crash").json()[
            "status"
        ]
        == "interrupted"
    )
    assert (
        chat.client.post(
            f"/api/projects/p1/conversations/{cid}/requests/crash/retry", json={}
        ).status_code
        == 200
    )


def test_concurrent_requests_isolate_conversations_and_no_sql_transaction_waits(chat, monkeypatch):
    # Exercise independent conversations within an explicitly configured cap.
    # The new account-wide cap is separately checked through the real auth app.
    from dataclasses import replace
    from core.resource_limits import RESOURCES
    monkeypatch.setattr(RESOURCES, "limits", replace(RESOURCES.limits, heavy_user=2))
    a = create(chat)
    b = create(chat)
    entered = threading.Event()
    release = threading.Event()

    def blocked(area, text, context):
        if text == "blocking":
            entered.set()
            assert release.wait(15)
        return GuideDraft(reply="offline response")

    chat.control["generate"] = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(send, chat, a, "blocking")
        try:
            assert entered.wait(10)
            assert send(chat, a, "another", rid="other").status_code == 409
            duplicate = send(chat, a, "blocking")
            assert duplicate.status_code == 202
            assert send(chat, b, "independent").status_code == 200
            # A formal write completes while the model is still blocked.
            shared = chat.services["p1"]
            shared.save_brief(Brief(goal="new material"), shared.load().revision)
        finally:
            release.set()
        assert running.result(timeout=20).status_code == 200
    assert len(messages(chat, a)) == 2 and len(messages(chat, b)) == 2
    assert len(chat.calls) == 2


def test_plain_chats_and_rename_do_not_invalidate_other_drafts_but_materials_do(chat):
    a = create(chat)
    b = create(chat)
    initial = send(chat, a).json()
    sid = initial["suggestions"][0]["suggestion_id"]
    revision = chat.services["p1"].load().revision
    assert send(chat, b).status_code == 200
    assert (
        chat.client.patch(
            f"/api/projects/p1/conversations/{b}", json={"title": "new title"}
        ).status_code
        == 200
    )
    old = chat.client.post(
        "/api/projects/p1/workspace/guide",
        json={
            "area": "overview",
            "message": "old client chatting",
            "expected_revision": revision,
        },
    )
    assert old.status_code == 200 and old.json()["workspace"]["revision"] == revision
    assert apply(chat, a, sid, expected_revision=revision).status_code == 200
    other = send(chat, b, "another draft", rid="r2").json()["suggestions"][0][
        "suggestion_id"
    ]
    shared = chat.services["p1"]
    shared.save_brief(Brief(goal="changed resources"), shared.load().revision)
    assert apply(chat, b, other).status_code == 409
    assert any(
        s["suggestion_id"] == other
        for s in chat.client.get(
            f"/api/projects/p1/conversations/{b}/suggestions"
        ).json()["suggestions"]
    )


def test_material_changes_while_generating_keep_stale_reply_and_draft(chat):
    cid = create(chat)
    shared = chat.services["p1"]

    def changed(*a):
        shared.save_brief(
            Brief(goal="changed during generation"), shared.load().revision
        )
        return GuideDraft(
            reply="保存为待复核的回复",
            suggestions=[SuggestedItem(title="stale draft", content="needs review")],
        )

    chat.control["generate"] = changed
    result = send(chat, cid).json()
    assert result["status"] == "completed" and result["basis_changed"]
    assert result["suggestions"][0]["basis_changed"] and len(messages(chat, cid)) == 2
    assert (
        apply(chat, cid, result["suggestions"][0]["suggestion_id"]).status_code == 409
    )


def test_external_source_and_paper_changes_block_old_adoption_without_revision_bump(
    chat,
):
    cid = create(chat)
    shared = chat.services["p1"]
    result = send(chat, cid).json()
    revision = shared.load().revision
    paper = shared.store.get_paper("p1-paper")
    paper.abstract = "source changed"
    shared.store.save_paper(paper)
    assert shared.load().revision == revision
    assert (
        apply(chat, cid, result["suggestions"][0]["suggestion_id"]).status_code == 409
    )


def test_duplicate_and_concurrent_adoption_are_atomic(chat):
    cid = create(chat)
    result = send(chat, cid).json()
    sid = result["suggestions"][0]["suggestion_id"]
    revision = chat.services["p1"].load().revision
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: apply(chat, cid, sid, expected_revision=revision), range(2)
            )
        )
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json()["item"]["item_id"] == results[1].json()["item"]["item_id"]
    assert sorted(r.json()["already_applied"] for r in results) == [False, True]
    assert len(chat.services["p1"].load().items) == 3
    assert chat.services["p1"].load().revision == revision + 1


def test_failed_adoption_transaction_does_not_leave_half_record(chat):
    cid = create(chat)
    sid = send(chat, cid).json()["suggestions"][0]["suggestion_id"]
    db = chat.config.paths.project_db("p1")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            "CREATE TRIGGER fixture_fail_apply BEFORE UPDATE ON sra_chat_suggestions BEGIN SELECT RAISE(ABORT,'offline injected failure'); END"
        )
    revision = chat.services["p1"].load().revision
    failed = apply(chat, cid, sid)
    assert failed.status_code == 503 and "detail" in failed.json()
    assert (
        chat.services["p1"].load().revision == revision
        and len(chat.services["p1"].load().items) == 2
    )
    assert not chat.client.get(
        f"/api/projects/p1/conversations/{cid}/suggestions"
    ).json()["suggestions"][0]["applied_item_id"]
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TRIGGER fixture_fail_apply")
    assert apply(chat, cid, sid).status_code == 200


def test_completion_failure_rolls_back_reply_and_can_retry_without_duplicate_user(
    chat, monkeypatch
):
    cid = create(chat)
    original = ConversationService._insert_message

    def broken(self, conn, cid, data, rid=None):
        if data["role"] == "assistant":
            raise RuntimeError("offline disk fixture")
        return original(self, conn, cid, data, rid)

    monkeypatch.setattr(ConversationService, "_insert_message", broken)
    result = send(chat, cid)
    assert result.status_code == 502 and result.json()["status"] == "failed"
    assert len(messages(chat, cid)) == 1
    assert (
        chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions").json()[
            "suggestions"
        ]
        == []
    )
    monkeypatch.setattr(ConversationService, "_insert_message", original)
    assert (
        chat.client.post(
            f"/api/projects/p1/conversations/{cid}/requests/r1/retry", json={}
        ).status_code
        == 200
    )
    assert len(messages(chat, cid)) == 2


def seed_history(chat, writing=False):
    s = chat.services["p1"]
    w = s.load(include_chat=False)
    w.messages = [
        Message(
            message_id="old-user",
            role="user",
            content="old question",
            area="writing" if writing else "question",
            mode="user",
        ),
        Message(
            message_id="old-assistant",
            role="assistant",
            content="old reply",
            area="writing" if writing else "question",
            mode="model",
            cited_paper_ids=["p1-paper"],
            suggestion_ids=["old-suggestion"],
        ),
    ]
    w.suggestions = [
        Suggestion(
            suggestion_id="old-suggestion",
            based_on_revision=w.revision,
            kind="outline" if writing else "note",
            title="old proposal",
            content="retained history",
        )
    ]
    s.store.save_kv(KEY, w.model_dump(mode="json"))
    return w


def test_history_read_is_virtual_no_repeat_import_and_old_api_stays_default(chat):
    old = seed_history(chat)
    before = tree(chat.root)
    for _ in range(3):
        listing = chat.client.get("/api/projects/p1/conversations").json()[
            "conversations"
        ]
        assert len(listing) == 1 and listing[0]["title"] == "历史对话"
        cid = listing[0]["conversation_id"]
        history = messages(chat, cid)
        assert [m["message_id"] for m in history] == ["old-user", "old-assistant"]
        assert history[1]["cited_paper_ids"] == ["p1-paper"] and history[1][
            "suggestion_ids"
        ] == ["old-suggestion"]
    assert tree(chat.root) == before
    new = create(chat)
    assert send(chat, new, "not legacy history").status_code == 200
    assert [m["message_id"] for m in messages(chat, cid)] == [
        "old-user",
        "old-assistant",
    ]
    assert send(chat, cid, "continue old", rid="old-next").status_code == 200
    assert all(
        "not legacy history" not in m["content"]
        for m in chat.calls[-1]["context"]["history"]
    )
    workspace = chat.client.get("/api/projects/p1/workspace").json()["workspace"]
    assert (
        len(workspace["messages"]) == 4
        and workspace["messages"][0]["message_id"] == "old-user"
    )
    # Old IDs/content remain readable, but a missing dependency snapshot must
    # not be silently manufactured from current materials during conversion.
    assert apply(chat, cid, "old-suggestion").status_code == 409
    refreshed = send(chat, cid, "review old proposal", rid="review-old").json()
    fresh_sid = refreshed["suggestions"][0]["suggestion_id"]
    assert apply(chat, cid, fresh_sid).status_code == 200
    with closing(sqlite3.connect(chat.config.paths.project_db("p1"))) as conn:
        assert (
            conn.execute(
                "SELECT value FROM sra_chat_meta WHERE key='migration_version'"
            ).fetchone()[0]
            == "1"
        )


def test_empty_projects_do_not_get_fake_history_on_get(chat):
    before = tree(chat.root)
    assert (
        chat.client.get("/api/projects/p1/conversations").json()["conversations"] == []
    )
    assert tree(chat.root) == before
    create(chat)
    assert (
        len(chat.client.get("/api/projects/p1/conversations").json()["conversations"])
        == 1
    )


def test_history_conversion_is_transactional_and_reads_remain_read_only(
    chat, monkeypatch
):
    seed_history(chat)
    before = tree(chat.root)
    original = ConversationService._insert_message

    def interrupted(self, conn, cid, data, rid=None):
        if data["message_id"] == "old-assistant":
            raise RuntimeError("offline migration failure")
        return original(self, conn, cid, data, rid)

    monkeypatch.setattr(ConversationService, "_insert_message", interrupted)
    assert (
        chat.client.post("/api/projects/p1/conversations", json={}).status_code == 500
    )
    assert tree(chat.root) == before
    monkeypatch.setattr(ConversationService, "_insert_message", original)
    create(chat)
    after = tree(chat.root)
    cid = ConversationService("p1", chat.config.paths.project_db("p1")).legacy_id
    for _ in range(3):
        assert len(messages(chat, cid)) == 2
        assert chat.client.get("/api/projects/p1/conversations").status_code == 200
    assert tree(chat.root) == after


def test_new_invalid_requests_before_first_write_do_not_create_schema(chat):
    before = tree(chat.root)
    for request in (
        {"title": "   "},
        {"entry_stage": "writing"},
        {"guidance_mode": "unknown"},
        {"force_writing": True},
    ):
        assert (
            chat.client.post("/api/projects/p1/conversations", json=request).status_code
            == 422
        )
    assert send(chat, "foreign", selected_paper_ids=["p2-paper"]).status_code == 404
    assert (
        chat.client.post(
            "/api/projects/p1/workspace/guide",
            json={
                "area": "study",
                "message": "hello",
                "request_id": "   ",
                "expected_revision": 2,
            },
        ).status_code
        == 422
    )
    assert tree(chat.root) == before


def test_rules_mode_no_model_and_shared_material_view_excludes_history(chat):
    chat.config.dry_run = True
    cid = create(chat, guidance_mode="beginner", entry_stage="design")
    result = send(chat, cid).json()
    assert result["status"] == "completed" and result["messages"][1]["mode"] == "rules"
    assert not chat.calls
    shared = chat.client.get(
        "/api/projects/p1/workspace", params={"include_chat": "false"}
    ).json()
    assert (
        shared["workspace"]["messages"] == []
        and shared["workspace"]["suggestions"] == []
    )
    assert shared["workflow"]["flow"]["planReady"]
    assert shared["product_boundary"]["max_stage"] == "design"
    assert len(shared["workspace"]["items"]) == 2


def test_no_suggestion_from_another_conversation_can_be_adopted(chat):
    a = create(chat)
    b = create(chat)
    suggestion = send(chat, a).json()["suggestions"][0]["suggestion_id"]
    before = tree(chat.root)
    assert apply(chat, b, suggestion).status_code == 404
    assert tree(chat.root) == before


@pytest.mark.parametrize("_race", range(5))
def test_same_create_and_send_request_race_is_idempotent(chat, _race):
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(
            pool.map(lambda _: create(chat, request_id="parallel-create"), range(2))
        )
    assert ids[0] == ids[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: send(chat, ids[0], "parallel message"), range(2))
        )
    assert all(r.status_code in (200, 202) for r in results)
    assert len(chat.calls) == 1 and len(messages(chat, ids[0])) == 2


def test_old_interface_does_not_see_new_unadopted_proposals(chat):
    cid = create(chat)
    send(chat, cid, "private draft")
    revision = chat.services["p1"].load().revision
    response = chat.client.post(
        "/api/projects/p1/workspace/guide",
        json={
            "area": "overview",
            "message": "default context",
            "expected_revision": revision,
            "request_id": "legacy-r1",
        },
    )
    assert response.status_code == 200
    assert chat.calls[-1]["context"]["history"] == []
    assert all(
        "private draft" not in item["content"]
        for item in chat.calls[-1]["context"]["records"]
    )
    assert len(response.json()["workspace"]["messages"]) == 2


def test_legacy_and_new_views_share_current_suggestion_dependency_state(chat):
    initial = chat.client.post(
        "/api/projects/p1/workspace/guide",
        json={
            "area": "overview",
            "message": "default proposal",
            "expected_revision": 2,
        },
    ).json()
    cid = initial["conversation_id"]
    shared = chat.services["p1"]
    paper = shared.store.get_paper("p1-paper")
    paper.abstract = "updated after generation"
    shared.store.save_paper(paper)
    old = chat.client.get("/api/projects/p1/workspace").json()["workspace"][
        "suggestions"
    ][0]
    new = chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions").json()[
        "suggestions"
    ][0]
    for field in (
        "suggestion_id",
        "conversation_id",
        "based_on_revision",
        "material_fingerprint",
        "basis_changed",
        "applied_item_id",
    ):
        assert old[field] == new[field]
    assert old["basis_changed"] and shared.load().revision == 2


def test_writing_history_remains_readable_but_cannot_be_adopted(chat):
    seed_history(chat, True)
    cid = chat.client.get("/api/projects/p1/conversations").json()["conversations"][0][
        "conversation_id"
    ]
    before = tree(chat.root)
    assert len(messages(chat, cid)) == 2
    assert apply(chat, cid, "old-suggestion").status_code == 422
    assert send(chat, cid, area="writing").status_code == 422
    assert tree(chat.root) == before


def test_model_cannot_inject_another_projects_records_or_post_plan_suggestions(chat):
    cid = create(chat)
    chat.control["generate"] = lambda *a: GuideDraft(
        reply="blocked",
        suggestions=[SuggestedItem(kind="outline", title="blocked", content="no")],
    )
    assert send(chat, cid).status_code == 422
    chat.control["generate"] = lambda *a: GuideDraft(
        reply="foreign",
        suggestions=[
            SuggestedItem(
                title="foreign",
                content="no",
                related_ids=[chat.services["p2"].load().items[0].item_id],
            )
        ],
    )
    assert send(chat, cid, rid="r2").status_code == 422
    assert len(chat.services["p1"].load().items) == 2
    assert (
        len(messages(chat, cid)) == 2
    )  # Failed requests retain their own user messages only.


def test_messages_have_bounded_stable_pagination_and_conversations_sort_recent(chat):
    a = create(chat)
    b = create(chat)
    for i in range(4):
        assert send(chat, a, str(i), rid=str(i)).status_code == 200
    ids = []
    after = 0
    while True:
        page = chat.client.get(
            f"/api/projects/p1/conversations/{a}/messages",
            params={"limit": 3, "after": after},
        ).json()
        ids.extend(m["message_id"] for m in page["messages"])
        after = page["next_after"]
        if after is None:
            break
    assert len(ids) == len(set(ids)) == 8
    listing = chat.client.get(
        "/api/projects/p1/conversations", params={"limit": 1}
    ).json()
    assert (
        listing["conversations"][0]["conversation_id"] == a
        and listing["next_offset"] == 1
    )
    assert (
        chat.client.get(
            "/api/projects/p1/conversations", params={"limit": 101}
        ).status_code
        == 422
    )
    assert (
        chat.client.get(
            f"/api/projects/p1/conversations/{a}/messages", params={"limit": 0}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("converted", [False, True])
def test_reads_and_rejected_requests_never_initialize_old_knowledge_schema(
    chat, converted
):
    seed_history(chat)
    cid = ConversationService("p1", chat.config.paths.project_db("p1")).legacy_id
    if converted:
        assert send(chat, cid).status_code == 200
    db = chat.config.paths.project_db("p1")
    # These unrelated legacy knowledge tables must not be repaired on browsing.
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TABLE evidence_log")
        conn.execute("DROP TABLE materials")
    before = tree(chat.root)
    for path in (
        "/api/projects/p1/conversations",
        f"/api/projects/p1/conversations/{cid}",
        f"/api/projects/p1/conversations/{cid}/messages",
        f"/api/projects/p1/conversations/{cid}/suggestions",
        "/api/projects/p1/workspace",
        "/api/projects/p1/workspace/export?format=json",
        "/api/projects/p1/research-materials",
    ):
        response = chat.client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert tree(chat.root) == before, path
    if converted:
        assert (
            chat.client.get(
                f"/api/projects/p1/conversations/{cid}/requests/r1"
            ).status_code
            == 200
        )
    assert (
        send(
            chat, cid, rid="foreign-paper", selected_paper_ids=["p2-paper"]
        ).status_code
        == 422
    )
    assert (
        chat.client.post(
            "/api/projects/p1/workspace/guide",
            json={"area": "overview", "message": "   ", "expected_revision": 2},
        ).status_code
        == 422
    )
    assert (
        chat.client.post(
            "/api/projects/p1/workspace/suggestions/foreign-id/apply",
            json={"expected_revision": 2},
        ).status_code
        == 404
    )
    assert tree(chat.root) == before


@pytest.mark.parametrize("during_generation", [False, True])
def test_paper_excerpt_changes_invalidate_suggestions_without_material_revision_bump(
    chat, during_generation
):
    shared = chat.services["p1"]
    chunk = PaperChunk(
        chunk_id="offline-excerpt",
        paper_id="p1-paper",
        chunk_index=0,
        text="Original offline excerpt",
        page=1,
    )
    shared.store.save_paper_chunks([chunk])
    cid = create(chat)
    revision = shared.load().revision

    def change_excerpt():
        chunk.text = "Corrected offline excerpt; no scientific conclusion"
        chunk.page = 3
        shared.store.save_paper_chunks([chunk])

    if during_generation:

        def generate(area, text, context):
            assert (
                context["papers"][0]["excerpts"][0]["text"]
                == "Original offline excerpt"
            )
            change_excerpt()
            return GuideDraft(
                reply="Reply based on previous excerpt",
                suggestions=[
                    SuggestedItem(title="excerpt draft", content="review source")
                ],
            )

        chat.control["generate"] = generate
    result = send(chat, cid, selected_paper_ids=["p1-paper"]).json()
    if not during_generation:
        assert not result["basis_changed"]
        change_excerpt()
    request = chat.client.get(
        f"/api/projects/p1/conversations/{cid}/requests/r1"
    ).json()
    assert request["status"] == "completed" and request["basis_changed"]
    sid = result["suggestions"][0]["suggestion_id"]
    assert request["suggestions"][0]["basis_changed"]
    before = tree(chat.root)
    assert apply(chat, cid, sid, expected_revision=revision).status_code == 409
    assert tree(chat.root) == before
    assert shared.load().revision == revision and len(shared.load().items) == 2
    assert len(messages(chat, cid)) == 2


def test_concurrent_explicit_retries_share_original_request_and_single_reply(chat):
    cid = create(chat)

    def failed(*args):
        raise RuntimeError("offline provider failure")

    chat.control["generate"] = failed
    assert send(chat, cid).status_code == 502
    entered = threading.Event()
    release = threading.Event()

    def waiting(*args):
        entered.set()
        assert release.wait(15)
        return GuideDraft(reply="single successful retry")

    chat.control["generate"] = waiting
    path = f"/api/projects/p1/conversations/{cid}/requests/r1/retry"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(chat.client.post, path, json={})
        try:
            assert entered.wait(10)
            duplicate = chat.client.post(path, json={})
            assert duplicate.status_code == 202
            assert duplicate.json()["attempt"] == 2
            assert len(messages(chat, cid)) == 1
        finally:
            release.set()
        assert first.result(timeout=20).status_code == 200
    assert len(chat.calls) == 2  # Initial failure + one retry, not two retries.
    assert [m["role"] for m in messages(chat, cid)] == ["user", "assistant"]
    assert chat.client.post(path, json={}).json()["attempt"] == 2


def test_legacy_missing_dependency_snapshot_cannot_be_renewed_by_import(chat):
    seed_history(chat)
    shared = chat.services["p1"]
    paper = shared.store.get_paper("p1-paper")
    paper.abstract = "Source corrected after old reply, before conversion"
    shared.store.save_paper(paper)
    cid = ConversationService("p1", shared.db_path).legacy_id
    before = tree(chat.root)
    old = chat.client.get("/api/projects/p1/workspace").json()["workspace"][
        "suggestions"
    ][0]
    new = chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions").json()[
        "suggestions"
    ][0]
    assert old["basis_changed"] and new["basis_changed"]
    assert old["material_fingerprint"] is None and new["material_fingerprint"] is None
    assert apply(chat, cid, "old-suggestion").status_code == 409
    assert tree(chat.root) == before  # Even first-write conversion rolls back.
    create(chat)
    state = chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions").json()[
        "suggestions"
    ][0]
    assert state["suggestion_id"] == "old-suggestion"
    assert state["basis_changed"] and state["material_fingerprint"] is None
    before = tree(chat.root)
    response = chat.client.post(
        "/api/projects/p1/workspace/suggestions/old-suggestion/apply",
        json={"expected_revision": 2},
    )
    assert response.status_code == 409 and "快照" in response.json()["detail"]
    assert tree(chat.root) == before


def test_legacy_dependency_snapshot_is_preserved_and_valid_old_api_can_adopt(chat):
    history = seed_history(chat)
    shared = chat.services["p1"]
    dependency = ConversationService("p1", shared.db_path).material_state()[
        "fingerprint"
    ]
    history.suggestions[0].material_fingerprint = dependency
    shared.store.save_kv(KEY, history.model_dump(mode="json"))
    response = chat.client.post(
        "/api/projects/p1/workspace/suggestions/old-suggestion/apply",
        json={"expected_revision": 2},
    )
    assert response.status_code == 200
    suggestion = response.json()["workspace"]["suggestions"][0]
    assert suggestion["material_fingerprint"] == dependency
    assert suggestion["applied_item_id"]
    repeat = chat.client.post(
        "/api/projects/p1/workspace/suggestions/old-suggestion/apply",
        json={"expected_revision": 2},
    )
    assert repeat.status_code == 200
    assert len(repeat.json()["workspace"]["items"]) == 3


def test_missing_project_database_returns_not_found_without_creating_empty_history(
    chat,
):
    db = chat.config.paths.project_db("p2")
    assert db.resolve().is_relative_to(chat.root.resolve())  # Fixture database only.
    db.unlink()
    before = tree(chat.root)
    for path in (
        "workspace",
        "workspace/export",
        "research-materials",
        "conversations",
    ):
        response = chat.client.get(f"/api/projects/p2/{path}")
        assert response.status_code == 404, response.text
    for path, body in (
        (
            "workspace/guide",
            {"area": "overview", "message": "hello", "expected_revision": 2},
        ),
        ("workspace/suggestions/unknown/apply", {"expected_revision": 2}),
        ("conversations", {"title": "do not create an empty project"}),
    ):
        assert (
            chat.client.post(f"/api/projects/p2/{path}", json=body).status_code == 404
        )
    assert tree(chat.root) == before and not chat.calls
