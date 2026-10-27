"""Model diagnostics keep their category, never raw exceptions or historical secrets."""

import json
import logging
import sqlite3
import time
from itertools import count
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.config import GlobalConfig, ProjectPaths
from core.llm.base import LLMError, LLMTruncatedError
from core.llm.public_errors import public_error
from core.llm.task_router import TaskNotFoundError
from core.research_tasks import sources
from core.research_tasks.service import ResearchTaskService
from core.workspace.service import WorkspaceService
from web.workspace_api import build_workspace_router


MARKER = "FAKE-CREDENTIAL-MARKER"
PRIVATE_ERROR = f"gateway https://user:{MARKER}@example.invalid/private?key={MARKER} C:/private/{MARKER}"


class GatewayError(RuntimeError):
    status_code = 401


@pytest.mark.parametrize(
    "error,category",
    [
        (GatewayError(PRIVATE_ERROR), "authentication"),
        (TimeoutError(PRIVATE_ERROR), "timeout"),
        (ConnectionError(PRIVATE_ERROR), "network"),
        (TaskNotFoundError(PRIVATE_ERROR), "missing_configuration"),
        (LLMTruncatedError(PRIVATE_ERROR), "truncated_output"),
        (ValueError(PRIVATE_ERROR), "invalid_response"),
        (RuntimeError(PRIVATE_ERROR), "unexpected_error"),
        (sqlite3.OperationalError(PRIVATE_ERROR), "storage"),
    ],
)
def test_public_error_retains_only_actionable_classification(error, category):
    summary = public_error(error)
    assert f"[{category}]" in summary
    assert MARKER not in summary
    assert "example.invalid" not in summary
    assert any(action in summary for action in ("核对", "等待", "缩小"))


def test_wrapped_sdk_status_and_cycle_are_handled_without_exception_text():
    outer = LLMError(PRIVATE_ERROR)
    inner = GatewayError(PRIVATE_ERROR)
    outer.__cause__ = inner
    inner.__cause__ = outer
    assert "[authentication]" in public_error(outer)
    outer.__cause__ = outer
    assert "[unexpected_error]" in public_error(outer)


@pytest.mark.parametrize("status,category", [(401, "authentication"), (403, "access_denied"), (429, "rate_limited"), (503, "server_error"), (404, "request_rejected")])
def test_provider_response_http_status_is_a_safe_category(status, category):
    error = RuntimeError(PRIVATE_ERROR)
    error.response = SimpleNamespace(status_code=status)
    summary = public_error(error)
    assert f"[{category}]" in summary
    assert MARKER not in summary


@pytest.fixture
def research_api(tmp_path, monkeypatch):
    config = GlobalConfig(paths=ProjectPaths.from_root(tmp_path), dry_run=False)
    service = WorkspaceService("p1", config.paths.project_db("p1"))
    project = SimpleNamespace(project_id="p1", topic="material study", domain="materials", notes=[])
    sequence = count()

    def source(query, **kwargs):
        i = next(sequence)
        return [{"title": f"Material paper {i}", "doi": f"10.1000/privacy{i}", "year": 2024, "abstract": "An offline material abstract."}]

    monkeypatch.setattr(sources, "SOURCE_FUNCS", {"openalex": source, "arxiv": source})
    responder = SimpleNamespace(call=lambda task_type: (_ for _ in ()).throw(GatewayError(PRIVATE_ERROR)))

    class Registry:
        def complete(self, task_type, **kwargs):
            return SimpleNamespace(text=responder.call(task_type))

    def require(pid):
        if pid != "p1":
            raise HTTPException(404, "项目不存在")
        return project

    app = FastAPI()
    app.include_router(build_workspace_router(config, require, registry_factory=Registry))
    with TestClient(app) as client:
        cid = client.post("/api/projects/p1/conversations", json={"title": "privacy test"}).json()["conversation"]["conversation_id"]
        yield SimpleNamespace(client=client, cid=cid, service=service, db=config.paths.project_db("p1"), responder=responder)


def run_task(api):
    response = api.client.post(
        f"/api/projects/p1/conversations/{api.cid}/research-tasks",
        json={"topic": "sulfide electrolyte conductivity", "content": "find material papers", "request_id": "privacy-search"},
    )
    assert response.status_code == 200, response.text
    tid = response.json()["task_id"]
    for _ in range(200):
        state = api.client.get(f"/api/projects/p1/conversations/{api.cid}/research-tasks/{tid}").json()
        if state["status"] not in ("queued", "running", "interrupted"):
            return tid, state
        time.sleep(0.02)
    pytest.fail("offline research fixture did not finish")


def test_fresh_model_failures_are_safe_in_api_export_storage_and_logs(research_api, caplog):
    caplog.set_level(logging.WARNING)
    tid, state = run_task(research_api)
    assert state["status"] == "completed"
    assert state["candidates_count"] > 0
    assert state["report"]["mode"] == "rules"
    for key in ("planning_note", "ranking_note", "synthesis_note"):
        assert "[authentication]" in state[key]
        assert "回退" in state[key]
    export = research_api.client.get(f"/api/projects/p1/research-tasks/{tid}/export")
    assert export.status_code == 200
    with sqlite3.connect(research_api.db) as conn:
        persisted = conn.execute("SELECT planning_note,ranking_note,synthesis_note,execution_log,error FROM sra_research_tasks WHERE task_id=?", (tid,)).fetchone()
    assert MARKER not in json.dumps(state)
    assert MARKER not in export.text
    assert MARKER not in str(persisted)
    assert MARKER not in caplog.text
    assert "[authentication]" in caplog.text


def test_historical_raw_notes_are_hidden_on_read_without_rewriting_database(research_api):
    tid, _ = run_task(research_api)
    with sqlite3.connect(research_api.db) as conn:
        conn.execute("UPDATE sra_research_tasks SET planning_note=?,ranking_note=?,synthesis_note=?,error=? WHERE task_id=?", (PRIVATE_ERROR, PRIVATE_ERROR, PRIVATE_ERROR, PRIVATE_ERROR, tid))
    response = research_api.client.get(f"/api/projects/p1/conversations/{research_api.cid}/research-tasks/{tid}")
    state = response.json()
    for key in ("planning_note", "ranking_note", "synthesis_note", "error"):
        assert "[historical_diagnostic]" in state[key]
    listing = research_api.client.get(f"/api/projects/p1/conversations/{research_api.cid}/research-tasks")
    export = research_api.client.get(f"/api/projects/p1/research-tasks/{tid}/export")
    historical_export = research_api.client.get(f"/api/projects/p1/research-tasks/{tid}/export?version=1")
    assert MARKER not in response.text + listing.text + export.text + historical_export.text
    with sqlite3.connect(research_api.db) as conn:
        stored = conn.execute("SELECT planning_note,error FROM sra_research_tasks WHERE task_id=?", (tid,)).fetchone()
    assert stored == (PRIVATE_ERROR, PRIVATE_ERROR)


def test_rejected_reference_keeps_candidates_without_exposing_model_content(research_api, caplog):
    answers = {
        "research_plan": '{"queries": [{"query": "sulfide electrolyte", "rationale": "user topic"}]}',
        "research_rank": '{"rankings": []}',
        "research_synthesize": json.dumps({"summary": "test", "representative": [{"paper_id": MARKER, "point": "not a source"}]}),
    }
    research_api.responder.call = answers.__getitem__
    tid, state = run_task(research_api)
    assert state["status"] == "failed"
    assert state["candidates_count"] > 0
    assert "[invalid_evidence]" in state["error"]
    assert "只重试整理" in state["error"]
    assert MARKER not in json.dumps(state)
    export = research_api.client.get(f"/api/projects/p1/research-tasks/{tid}/export")
    assert MARKER not in export.text + caplog.text


def test_pipeline_last_resort_error_is_safe_in_response_and_logs(research_api, monkeypatch, caplog):
    def fail(*args):
        raise GatewayError(PRIVATE_ERROR)

    monkeypatch.setattr(ResearchTaskService, "_run_pipeline", fail)
    caplog.set_level(logging.ERROR)
    _, state = run_task(research_api)
    assert state["status"] == "failed"
    assert "[authentication]" in state["error"]
    assert MARKER not in json.dumps(state) + caplog.text
    assert "[authentication]" in caplog.text
