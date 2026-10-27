"""Bounded coordination exercises task retry handoff without timing guesses."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from core.research_tasks.service import ResearchTaskService, SearchTaskRequest, TaskConflict, TaskError
from core.workspace.conversations import ConversationService
from tests.test_research_tasks import rt, create


def queued_task(rt, runner):
    cid = create(rt)
    service = ResearchTaskService("p1", rt.config.paths.project_db("p1"), ConversationService("p1", rt.config.paths.project_db("p1")), runner=runner)
    task = service.start(cid, SearchTaskRequest(content="find literature", topic="material", request_id="handoff"))
    return service, task["task_id"]


def test_new_queued_task_is_not_a_stale_task_before_worker_claims_lock(rt):
    pending = []
    service, tid = queued_task(rt, pending.append)
    assert service.get(tid)["status"] == "queued"
    with pytest.raises(TaskConflict):
        service.retry(tid)
    assert service.get(tid)["attempts"] == 1
    assert len(pending) == 1


def test_restart_queued_task_is_readonly_interrupted_and_explicitly_recoverable(rt):
    pending = []
    service, tid = queued_task(rt, pending.append)
    service._update(tid, owner_process="previous-process")
    assert service.get(tid)["status"] == "interrupted"
    assert service._read(tid)["status"] == "queued"
    pending.clear()  # A callback owned by the old process no longer exists.
    view = service.retry(tid)
    assert view["status"] == "queued"
    assert view["attempts"] == 2
    assert len(pending) == 1
    pending.pop()()
    assert service.get(tid)["status"] in ("completed", "partial")


def test_worker_start_failure_is_explicit_and_can_be_retried(rt):
    pending = []
    service, tid = queued_task(rt, pending.append)
    service._update(tid, status="failed")

    def fail(fn):
        raise RuntimeError("FAKE-WORKER-START-PRIVATE-DIAGNOSTIC")

    service._runner = fail
    with pytest.raises(TaskError):
        service.retry(tid)
    failed = service.get(tid)
    assert failed["status"] == "failed"
    assert "FAKE-WORKER" not in failed["error"]
    service._runner = pending.append
    pending.clear()
    service.retry(tid)
    assert len(pending) == 1
    pending.pop()()
    assert service.get(tid)["status"] in ("completed", "partial")


def test_two_retries_from_same_attempt_cannot_claim_a_new_partial_result(rt, monkeypatch):
    pending = []
    service, tid = queued_task(rt, pending.append)
    service._update(tid, status="partial", candidates="[]", source_calls="[]")
    barrier = threading.Barrier(2)
    seen = set()
    guard = threading.Lock()
    original_read = service._read

    def read(task_id):
        row = original_read(task_id)
        with guard:
            first_read = threading.get_ident() not in seen
            seen.add(threading.get_ident())
        if first_read:
            barrier.wait(timeout=5)
        return row

    monkeypatch.setattr(service, "_read", read)
    # Finish immediately as partial, so the next transaction sees a retryable
    # terminal state but must still reject its old attempt snapshot.
    service._runner = lambda fn: service._update(tid, status="partial")

    def retry():
        try:
            service.retry(tid, resume=True)
            return "started"
        except TaskConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(), range(2)))
    assert sorted(results) == ["conflict", "started"]
    assert original_read(tid)["attempts"] == 2
    monkeypatch.setattr(service, "_read", original_read)
    # A later explicit retry based on the new terminal state remains allowed.
    assert service.retry(tid, resume=True)["attempts"] == 3
