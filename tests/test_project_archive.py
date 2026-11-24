"""Recoverable batch deletion: temporary projects only, no real research data."""
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.config import GlobalConfig, ProjectPaths
from core.knowledge import KnowledgeStore
from core.project_archive import archive_project_directories


@pytest.fixture
def archive_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD", "1")
    from web import api
    monkeypatch.setattr(api, "_CONFIG", GlobalConfig(paths=ProjectPaths.from_root(tmp_path), dry_run=True))
    monkeypatch.setattr(api, "_PROJECTS", {})
    monkeypatch.setattr(api, "_METHOD_REGEN_JOBS", {})
    app = FastAPI()
    app.add_api_route("/api/projects/batch-delete", api.delete_projects_batch, methods=["POST"])
    app.add_api_route("/api/projects/{project_id}", api.delete_project, methods=["DELETE"])
    for pid in ["archive-a", "archive-b", "archive-c"]:
        api._PROJECTS[pid] = api.ProjectState(project_id=pid, topic="软件夹具", domain="ai", created_at="2026-10-17")
        store = KnowledgeStore(api._CONFIG.paths.project_db(pid))
        store.save_kv("research.topic", f"preserved {pid}")
    return api, TestClient(app)


def test_batch_moves_only_selected_and_retains_contents(archive_api):
    api, client = archive_api
    response = client.post("/api/projects/batch-delete", json={"project_ids": ["archive-a", "archive-b"]})
    assert response.status_code == 200
    result = response.json()
    assert result["recoverable"] is True
    target = api._CONFIG.paths.root / ".project-trash" / result["archive_id"]
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "archived"
    assert manifest["project_ids"] == ["archive-a", "archive-b"]
    for pid in manifest["project_ids"]:
        assert not api._CONFIG.paths.project_dir(pid).exists()
        assert KnowledgeStore(target / pid / "knowledge.db").get_kv("research.topic") == f"preserved {pid}"
    assert set(api._PROJECTS) == {"archive-c"}
    assert api._CONFIG.paths.project_db("archive-c").exists()


@pytest.mark.parametrize("ids,code", [([], 422), (["archive-a"]*2, 422), (["../archive-a"], 422), (["archive-a", "missing"], 404), (["a"]*201, 422)])
def test_invalid_selection_never_partially_deletes(archive_api, ids, code):
    api, client = archive_api
    assert client.post("/api/projects/batch-delete", json={"project_ids": ids}).status_code == code
    assert len(api._PROJECTS) == 3
    assert all(api._CONFIG.paths.project_db(pid).exists() for pid in api._PROJECTS)
    assert not (api._CONFIG.paths.root / ".project-trash").exists()


@pytest.mark.parametrize("busy", ["running", "pending_human", "thread", "regenerate"])
def test_busy_project_blocks_entire_selection(archive_api, busy):
    api, client = archive_api
    state = api._PROJECTS["archive-b"]
    if busy == "thread":
        state.thread = SimpleNamespace(is_alive=lambda: True)
    elif busy == "regenerate":
        api._METHOD_REGEN_JOBS["archive-b"] = {"status": "running"}
    else:
        state.status = busy
    assert client.post("/api/projects/batch-delete", json={"project_ids": ["archive-a", "archive-b"]}).status_code == 409
    assert len(api._PROJECTS) == 3
    assert all(api._CONFIG.paths.project_db(pid).exists() for pid in api._PROJECTS)


def test_move_failure_rolls_back_all_entries(archive_api, monkeypatch):
    api, client = archive_api
    import core.project_archive as module
    original = module.os.replace
    calls = []
    def move(source, target):
        calls.append(source)
        if len(calls) == 2:
            raise OSError("injected move failure")
        return original(source, target)
    monkeypatch.setattr(module.os, "replace", move)
    result = client.post("/api/projects/batch-delete", json={"project_ids": ["archive-a", "archive-b"]})
    assert result.status_code == 500
    assert "已移回" in result.json()["detail"]
    assert len(api._PROJECTS) == 3
    assert all(api._CONFIG.paths.project_db(pid).exists() for pid in api._PROJECTS)


def test_single_delete_is_also_recoverable(archive_api):
    api, client = archive_api
    response = client.delete("/api/projects/archive-a")
    assert response.status_code == 200 and response.json()["recoverable"]
    assert (api._CONFIG.paths.root / ".project-trash" / response.json()["archive_id"] / "archive-a/knowledge.db").exists()


def test_helper_checks_entire_batch_before_moving(tmp_path):
    paths = ProjectPaths.from_root(tmp_path)
    paths.project_dir("valid").mkdir(parents=True)
    with pytest.raises(ValueError):
        archive_project_directories(paths, ["valid", "missing"])
    assert paths.project_dir("valid").exists()
