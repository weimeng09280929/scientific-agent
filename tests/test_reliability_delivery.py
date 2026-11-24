"""Actual project routes/SQLite: background plans and durable upload receipts."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest
from tests.test_local_product_acceptance import (
    product,
    home,
    no_network,
    begin,
    ok,
    shared,
)
from tests.test_conversation_home import tree
from core.workspace.uploads import UploadService


@pytest.fixture
def delivery(product):
    from web import api

    product.client.app.add_api_route(
        "/api/projects/{project_id}/upload-requests/{request_id}",
        api.upload_request_state,
        methods=["GET"],
    )
    return product


def upload(
    h,
    pid,
    rid="upload-1",
    body=b"Public fixture text",
    title="Fixture",
    filename="same.md",
):
    return h.client.post(
        f"/api/projects/{pid}/upload-paper",
        data={"request_id": rid, "title": title},
        files={"file": (filename, body, "application/octet-stream")},
    )


def test_upload_replay_conflict_deliberate_duplicate_and_get_readonly(delivery):
    h = delivery
    pid = begin(h)["project_id"]
    first = ok(upload(h, pid))
    before = tree(h.root)
    again = ok(upload(h, pid))
    assert again["already_uploaded"] and again["paper_id"] == first["paper_id"]
    assert tree(h.root) == before
    for changes in (
        {"body": b"different"},
        {"title": "different"},
        {"filename": "different.md"},
    ):
        assert upload(h, pid, **changes).status_code == 409
        assert tree(h.root) == before
    state = ok(h.client.get(f"/api/projects/{pid}/upload-requests/upload-1"))
    assert state["status"] == "completed" and tree(h.root) == before
    assert upload(h, pid, rid="deliberate-new").status_code == 200
    assert len(shared(h, pid)["papers"]) == 2
    other = begin(h, rid="other")["project_id"]
    other_before = tree(h.root)
    assert (
        h.client.get(f"/api/projects/{other}/upload-requests/upload-1").status_code
        == 404
    )
    assert tree(h.root) == other_before


def test_concurrent_upload_uses_one_parse_and_atomic_receipt(delivery, monkeypatch):
    from web import api

    h = delivery
    pid = begin(h)["project_id"]
    entered = threading.Event()
    release = threading.Event()
    calls = []
    original = api._prepare_upload

    def block(*args):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(api, "_prepare_upload", block)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(upload, h, pid)
        try:
            assert entered.wait(5)
            repeat = upload(h, pid)
            assert repeat.status_code == 202 and repeat.json()["status"] == "processing"
            assert not shared(h, pid)["papers"]
            assert upload(h, pid, body=b"foreign bytes").status_code == 409
        finally:
            release.set()
        saved = ok(first.result(timeout=10))
    assert calls == [1]
    assert ok(upload(h, pid))["paper_id"] == saved["paper_id"]
    assert len(shared(h, pid)["papers"]) == 1


def test_prepared_upload_survives_publish_failure_and_explicit_retry(
    delivery, monkeypatch
):
    from web import api

    h = delivery
    pid = begin(h)["project_id"]
    calls = []
    parse = api._prepare_upload
    publish = UploadService._publish
    monkeypatch.setattr(
        api, "_prepare_upload", lambda *args: calls.append(1) or parse(*args)
    )

    def broken(self, conn, prepared):
        publish(self, conn, prepared)
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(UploadService, "_publish", broken)
    assert upload(h, pid).status_code == 503
    assert not shared(h, pid)["papers"]
    state = ok(h.client.get(f"/api/projects/{pid}/upload-requests/upload-1"))
    assert state["status"] == "failed"
    monkeypatch.setattr(UploadService, "_publish", publish)
    # Simulate a restart after preparation: no live lock; GET derives interruption
    # without mutating DB and never invokes parser. Explicit POST resumes checkpoint.
    service = UploadService(h.config.paths.project_db(pid), pid)
    with service.connection(True) as conn:
        conn.execute(
            "UPDATE sra_upload_requests SET status='processing' WHERE request_id='upload-1'"
        )
    before = tree(h.root)
    assert (
        ok(h.client.get(f"/api/projects/{pid}/upload-requests/upload-1"))["status"]
        == "interrupted"
    )
    assert tree(h.root) == before and calls == [1]
    retried = ok(upload(h, pid))
    assert retried["status"] == "completed" and calls == [1]
    assert len(shared(h, pid)["papers"]) == 1


def prepare_question(h, pid):
    w = shared(h, pid)["workspace"]
    response = ok(
        h.client.post(
            f"/api/projects/{pid}/workspace/items",
            json={
                "expected_revision": w["revision"],
                "item": {
                    "kind": "question",
                    "title": "公平比较问题",
                    "content": "怎样设计同一任务下的方法比较？",
                },
            },
        )
    )
    return response


def test_upload_actual_process_death_preserves_prepared_receipt(tmp_path):
    import subprocess
    import sys
    from core.knowledge.store import KnowledgeStore

    db = tmp_path / "knowledge.db"
    KnowledgeStore(db)
    marker = tmp_path / "prepared"
    code = """
import sys,time
from pathlib import Path
from core.workspace.uploads import UploadService
from core.knowledge.schema import Paper
db,marker=map(Path,sys.argv[1:])
service=UploadService(db,'p')
def prepare(store,paper_id):
    store.save_paper(Paper(paper_id=paper_id,title='Restart fixture'))
    return {'paper_id':paper_id,'title':'Restart fixture'}
def paused_publish(conn,data):
    marker.touch()
    time.sleep(60)
service._publish=paused_publish
service.process('restart-upload','fixture.md',b'public fixture','Restart fixture',prepare)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(db), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while (
            not marker.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert marker.exists(), "Child did not reach prepared checkpoint"
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)
    service = UploadService(db, "p")
    before = tree(tmp_path)
    assert service.status("restart-upload")["status"] == "interrupted"
    assert tree(tmp_path) == before
    assert KnowledgeStore(db, read_only=True).list_papers() == []

    def no_reparse(*args):
        pytest.fail("Durable prepared upload must not be parsed twice")

    result = service.process(
        "restart-upload", "fixture.md", b"public fixture", "Restart fixture", no_reparse
    )
    assert result["status"] == "completed"
    papers = KnowledgeStore(db, read_only=True).list_papers()
    assert len(papers) == 1 and papers[0].paper_id == result["paper_id"]
    assert service.status("restart-upload")["attempt"] == 2


def terminal(h, base, rid):
    for _ in range(100):
        value = ok(h.client.get(base + "/requests/" + rid))
        if value["status"] != "processing":
            return value
        time.sleep(0.05)
    pytest.fail("Background request did not reach a terminal state")


@pytest.mark.parametrize("change_materials", [False, True])
def test_background_plan_persists_before_generation_and_deduplicates(
    delivery, monkeypatch, change_materials
):
    from web import workspace_api
    from core.workspace.plans import rule_plan

    h = delivery
    r = begin(h)
    pid = r["project_id"]
    cid = r["conversation_id"]
    base = f"/api/projects/{pid}/conversations/{cid}"
    prepare_question(h, pid)
    original = workspace_api.rule_guidance
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow(area, text, context):
        if not context.get("plan_generation", {}).get("allowed"):
            return original(area, text, context)
        calls.append(context)
        entered.set()
        assert release.wait(10)
        return rule_plan(context)

    monkeypatch.setattr(workspace_api, "rule_guidance", slow)
    payload = {
        "request_id": "background-plan",
        "content": "请生成计算研究方案",
        "plan_request": {"method": "computational"},
        "wait_for_completion": False,
    }
    try:
        accepted = h.client.post(base + "/messages", json=payload)
        assert accepted.status_code == 202 and entered.wait(5)
        assert (
            len(accepted.json()["messages"]) == 1
            and accepted.json()["messages"][0]["role"] == "user"
        )
        assert not shared(h, pid)["workflow"]["flow"]["planReady"]
        replay = h.client.post(
            base + "/messages", json={**payload, "wait_for_completion": True}
        )
        assert replay.status_code == 202 and len(calls) == 1
        if change_materials:
            w = shared(h, pid)["workspace"]
            ok(
                h.client.put(
                    f"/api/projects/{pid}/workspace/brief",
                    json={
                        "expected_revision": w["revision"],
                        "brief": {"resources": "用户新确认只有 CPU"},
                    },
                )
            )
    finally:
        release.set()
    result = terminal(h, base, "background-plan")
    assert result["status"] == "completed" and len(calls) == 1
    assert len([m for m in result["messages"] if m["role"] == "user"]) == 1
    assert result["basis_changed"] is change_materials
    phases = [e for e in result["call_audit"]["entries"] if e["event"] == "phase"]
    assert {e["name"] for e in phases} == {
        "context",
        "generation",
        "validation",
        "saving",
        "display_validated",
        "display_normalization",
    }
    assert all(e["elapsed_ms"] is not None for e in phases)
    assert not shared(h, pid)["workflow"]["flow"]["planReady"]
    suggestion = result["suggestions"][0]
    adopted = h.client.post(
        base + f"/suggestions/{suggestion['suggestion_id']}/apply",
        json={"expected_revision": shared(h, pid)["workspace"]["revision"]},
    )
    assert adopted.status_code == (409 if change_materials else 200)
    if not change_materials:
        assert shared(h, pid)["workflow"]["flow"]["planReady"]
        file = h.client.get(
            f"/api/projects/{pid}/exports/items/{adopted.json()['item']['item_id']}"
        )
        assert file.status_code == 200 and "待确认" in file.text


def test_background_failure_retry_keeps_user_and_only_saves_valid_plan(
    delivery, monkeypatch
):
    from web import workspace_api
    from core.workspace.plans import rule_plan

    h = delivery
    r = begin(h)
    pid = r["project_id"]
    cid = r["conversation_id"]
    base = f"/api/projects/{pid}/conversations/{cid}"
    prepare_question(h, pid)
    calls = []

    def generate(area, text, context):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("offline timeout")
        return rule_plan(context)

    monkeypatch.setattr(workspace_api, "rule_guidance", generate)
    body = {
        "request_id": "failed-plan",
        "content": "请生成计算研究方案",
        "plan_request": {"method": "computational"},
        "wait_for_completion": False,
    }
    assert h.client.post(base + "/messages", json=body).status_code in (202, 502)
    failed = terminal(h, base, "failed-plan")
    assert (
        failed["status"] == "failed"
        and not failed["suggestions"]
        and len(failed["messages"]) == 1
    )
    before = len(calls)
    for _ in range(3):
        ok(h.client.get(base + "/requests/failed-plan"))
    assert len(calls) == before
    assert h.client.post(
        base + "/requests/failed-plan/retry", json={"wait_for_completion": False}
    ).status_code in (200, 202)
    result = terminal(h, base, "failed-plan")
    assert (
        result["status"] == "completed" and result["attempt"] == 2 and calls == [1, 1]
    )
    assert (
        len(result["messages"]) == 2
        and not shared(h, pid)["workflow"]["flow"]["planReady"]
    )
