"""Live HTTP handlers + temporary SQLite. No production project writes or models."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.config import GlobalConfig, ProjectPaths
from core import config
from core.workspace.conversations import ConversationService
from core.workspace.schema import Message
from core.workspace.service import WorkspaceService
from web.conversation_home_api import build_home_router
from web.workspace_api import build_workspace_router


def tree(root):
    return {
        str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_AUTH_MODE", "local")
    monkeypatch.setenv("SRA_ACCOUNTS_DB", str(tmp_path / "unused-accounts.db"))
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD", "1")
    monkeypatch.setenv("SRA_WEB_SKIP_PROJECT_SCAN", "1")
    cfg = GlobalConfig(paths=ProjectPaths.from_root(tmp_path), dry_run=True)
    monkeypatch.setattr(config, "_default_config", cfg)
    # Load the real handlers in a private module so other fixtures can keep
    # their own web.api module, account mode and router dependencies.
    source = Path(__file__).resolve().parents[1] / "web" / "api.py"
    spec = spec_from_file_location("web._conversation_home_test_api", source)
    api = module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, api)
    spec.loader.exec_module(api)
    app = FastAPI()
    app.include_router(
        build_home_router(cfg, api._require_project, api._register_conversation_project)
    )
    app.include_router(
        build_workspace_router(
            cfg,
            api._require_project,
            workflow_provider=lambda s: api._workspace_workflow(
                s, include_materials=True
            ),
        )
    )
    for path, handler, methods in [
        ("/", api.index, ["GET"]),
        ("/workspace", api.research_workspace, ["GET"]),
        ("/api/projects", api.list_projects, ["GET"]),
        ("/api/projects", api.create_project, ["POST"]),
        ("/api/projects/batch-delete", api.delete_projects_batch, ["POST"]),
        ("/api/projects/{project_id}/status", api.get_status, ["GET"]),
        ("/api/projects/{project_id}/papers", api.list_papers, ["GET"]),
        ("/api/projects/{project_id}/notes", api.list_notes, ["GET"]),
        ("/api/projects/{project_id}/experiments", api.list_experiments, ["GET"]),
        (
            "/api/projects/{project_id}/download/{artifact_type}",
            api.download_artifact,
            ["GET"],
        ),
    ]:
        app.add_api_route(path, handler, methods=methods)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, api=api, config=cfg, root=tmp_path)


def start(home, **extra):
    return home.client.post(
        "/api/conversations/start",
        json={
            "request_id": "first-stable",
            "title": "离线软件流程夹具",
            "domain": "ai",
            **extra,
        },
    )


def test_landing_and_capabilities_do_not_create_any_data(home):
    before = tree(home.root)
    for path in ("/", "/workspace", "/api/conversation-capabilities", "/api/projects"):
        assert home.client.get(path).status_code == 200
    assert tree(home.root) == before
    assert home.client.get("/").text == home.client.get("/workspace").text
    assert 'src="/static/conversation-home.js"' in home.client.get("/").text
    assert not home.config.paths.projects.exists()


def test_start_and_message_use_separate_durable_idempotency_keys(home):
    a = start(home)
    assert a.status_code == 200, a.text
    data = a.json()
    pid, cid = data["project_id"], data["conversation_id"]
    assert start(home).json()["conversation_id"] == cid
    assert (
        home.client.get(f"/api/projects/{pid}/conversations/{cid}/messages").json()[
            "messages"
        ]
        == []
    )
    # Simulated restart of the registry and starter, not of the real main service.
    home.api._PROJECTS.clear()
    assert start(home).json()["project_id"] == pid
    assert len(home.client.get("/api/projects").json()["projects"]) == 1
    payload = {
        "request_id": "first-message",
        "content": "离线测试，帮我理解怎样整理研究问题",
    }
    url = f"/api/projects/{pid}/conversations/{cid}/messages"
    reply = home.client.post(url, json=payload)
    assert reply.status_code == 200, reply.text
    assert (
        home.client.post(url, json=payload).json()["messages"]
        == reply.json()["messages"]
    )
    messages = home.client.get(url).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert [m["seq"] for m in messages] == [m["seq"] for m in reply.json()["messages"]]
    assert (
        len(
            home.client.get(f"/api/projects/{pid}/conversations").json()[
                "conversations"
            ]
        )
        == 1
    )
    workspace = home.client.get(
        f"/api/projects/{pid}/workspace?include_chat=false"
    ).json()
    assert (
        workspace["workspace"]["revision"] == 0
        and workspace["workspace"]["items"] == []
    )
    assert workspace["product_boundary"]["max_stage"] == "design"


def test_conflicting_start_and_invalid_inputs_do_not_mutate_data(home):
    assert start(home).status_code == 200
    before = tree(home.root)
    assert start(home, title="different").status_code == 409
    assert start(home, project_id="different-project").status_code == 409
    assert (
        start(home, request_id="missing", project_id="not-a-project").status_code == 404
    )
    assert start(home, request_id="domain", domain="medicine").status_code == 422
    assert start(home, request_id="stage", entry_stage="experiment").status_code == 422
    assert tree(home.root) == before


@pytest.mark.parametrize("after_conversation", [False, True])
def test_partial_start_is_recoverable_without_rollback_or_duplicates(
    home, monkeypatch, after_conversation
):
    original = ConversationService.create
    attempts = []

    def interrupted(self, *args):
        attempts.append(self.project_id)
        if after_conversation:
            original(self, *args)
        raise RuntimeError("offline injected lost start response")

    monkeypatch.setattr(ConversationService, "create", interrupted)
    assert start(home).status_code == 500
    assert len(home.client.get("/api/projects").json()["projects"]) == 1
    db = home.config.paths.project_db(attempts[0])
    assert db.is_file()
    monkeypatch.setattr(ConversationService, "create", original)
    home.api._PROJECTS.clear()
    resumed = start(home)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["project_id"] == attempts[0]
    assert len(home.client.get("/api/projects").json()["projects"]) == 1
    assert (
        len(
            home.client.get(f"/api/projects/{attempts[0]}/conversations").json()[
                "conversations"
            ]
        )
        == 1
    )


def test_same_start_race_is_controlled_and_eventually_returns_one_identity(home):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start(home), range(2)))
    assert all(r.status_code in (200, 409) for r in responses)
    good = start(home).json()
    assert all(
        r.status_code == 409 or r.json()["project_id"] == good["project_id"]
        for r in responses
    )
    assert len(home.client.get("/api/projects").json()["projects"]) == 1
    assert (
        len(
            home.client.get(f"/api/projects/{good['project_id']}/conversations").json()[
                "conversations"
            ]
        )
        == 1
    )


def test_existing_project_new_conversation_and_display_name_are_not_research_state(
    home,
):
    one = start(home).json()
    pid = one["project_id"]
    two = start(
        home,
        request_id="second",
        project_id=pid,
        entry_stage="design",
        guidance_mode="beginner",
    ).json()
    assert two["project_id"] == pid and two["conversation_id"] != one["conversation_id"]
    before = WorkspaceService(pid, home.config.paths.project_db(pid)).load()
    renamed = home.client.patch(
        f"/api/projects/{pid}/identity", json={"display_name": "更易识别的项目名"}
    )
    assert renamed.status_code == 200
    after = WorkspaceService(pid, home.config.paths.project_db(pid)).load()
    assert after.revision == before.revision and after.focus == before.focus
    project = home.client.get("/api/projects").json()["projects"][0]
    assert (
        project["display_name"] == "更易识别的项目名"
        and project["topic"] == "离线软件流程夹具"
    )
    home.api._PROJECTS.clear()
    start(home)
    assert (
        home.client.get("/api/projects").json()["projects"][0]["display_name"]
        == "更易识别的项目名"
    )


@pytest.mark.parametrize("archived", [False, True])
def test_missing_or_archived_database_cannot_be_recreated_by_old_start_key(
    home, archived
):
    data = start(home).json()
    pid = data["project_id"]
    if archived:
        response = home.client.post(
            "/api/projects/batch-delete", json={"project_ids": [pid]}
        )
        assert response.status_code == 200
    else:
        db = home.config.paths.project_db(pid)
        assert db.resolve().is_relative_to(home.root.resolve())
        db.unlink()
    before = tree(home.root)
    assert start(home).status_code == 404
    assert (
        home.client.get(f"/api/projects/{pid}/workspace?include_chat=false").status_code
        == 404
    )
    assert tree(home.root) == before


def test_tail_and_backwards_pages_are_stable_and_compatible(home):
    data = start(home).json()
    pid, cid = data["project_id"], data["conversation_id"]
    service = ConversationService(pid, home.config.paths.project_db(pid))
    with service._connection(True) as conn:
        for i in range(77):
            service._insert_message(
                conn,
                cid,
                Message(
                    role="user",
                    content=f"offline history {i}",
                    area="overview",
                    mode="user",
                ).model_dump(mode="json"),
            )
    base = f"/api/projects/{pid}/conversations/{cid}/messages"
    tail = home.client.get(base, params={"tail": True, "limit": 17}).json()
    assert (
        len(tail["messages"]) == 17
        and tail["messages"][-1]["content"] == "offline history 76"
    )
    received = list(tail["messages"])
    before = tail["next_before"]
    while before is not None:
        page = home.client.get(base, params={"before": before, "limit": 17}).json()
        received = page["messages"] + received
        before = page["next_before"]
    assert [m["content"] for m in received] == [
        f"offline history {i}" for i in range(77)
    ]
    assert len({m["message_id"] for m in received}) == 77
    forward = home.client.get(base, params={"after": 0, "limit": 17}).json()
    assert (
        forward["messages"] == received[:17]
        and forward["next_after"] == received[16]["seq"]
    )
    assert home.client.get(base, params={"tail": True, "after": 1}).status_code == 422


def test_browsing_real_handlers_does_not_migrate_unrelated_legacy_tables(home):
    data = start(home).json()
    pid = data["project_id"]
    db = home.config.paths.project_db(pid)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TABLE evidence_log")
    before = tree(home.root)
    for suffix in (
        "status",
        "workspace?include_chat=false",
        "conversations",
        "papers",
        "notes",
        "experiments",
        "research-materials",
        "workspace/export?format=json",
    ):
        response = home.client.get(f"/api/projects/{pid}/{suffix}")
        assert response.status_code == 200, (suffix, response.text)
        assert tree(home.root) == before, suffix


def test_virtual_legacy_tail_is_read_only_and_cursor_survives_conversion(home):
    from core.workspace.service import KEY

    pid = home.client.post(
        "/api/projects", json={"topic": "旧 JSON 聊天分页夹具", "domain": "ai"}
    ).json()["project_id"]
    db = home.config.paths.project_db(pid)
    shared = WorkspaceService(pid, db)
    w = shared.load(include_chat=False)
    w.messages = [
        Message(
            message_id=f"virtual-{i}",
            role="user",
            content=f"legacy {i}",
            area="question",
            mode="user",
        )
        for i in range(37)
    ]
    shared.store.save_kv(KEY, w.model_dump(mode="json"))
    cid = home.client.get(f"/api/projects/{pid}/conversations").json()["conversations"][
        0
    ]["conversation_id"]
    url = f"/api/projects/{pid}/conversations/{cid}/messages"
    before = tree(home.root)
    tail = home.client.get(url, params={"tail": True, "limit": 7}).json()
    assert [m["message_id"] for m in tail["messages"]] == [
        f"virtual-{i}" for i in range(30, 37)
    ]
    earlier = home.client.get(
        url, params={"before": tail["next_before"], "limit": 7}
    ).json()
    assert [m["message_id"] for m in earlier["messages"]] == [
        f"virtual-{i}" for i in range(23, 30)
    ]
    assert tree(home.root) == before
    assert start(home, project_id=pid).status_code == 200
    converted = home.client.get(url, params={"tail": True, "limit": 7}).json()
    assert [(m["message_id"], m["seq"]) for m in converted["messages"]] == [
        (m["message_id"], m["seq"]) for m in tail["messages"]
    ]


@pytest.mark.parametrize("after_metadata", [False, True])
def test_partial_project_bootstrap_can_resume_in_place(
    home, monkeypatch, after_metadata
):
    from core.workspace import start as starting

    original = starting.ConversationStarter._bootstrap
    paths = []

    def incomplete(self, row, request):
        paths.append(self.paths.project_db(row["project_id"]))
        if after_metadata:
            original(self, row, request)
        else:
            starting.KnowledgeStore(paths[-1])
        raise RuntimeError("offline crash between project initialization steps")

    monkeypatch.setattr(starting.ConversationStarter, "_bootstrap", incomplete)
    assert start(home).status_code == 500
    db = paths[0]
    assert db.is_file()
    identity = (db.stat().st_dev, db.stat().st_ino)
    monkeypatch.setattr(starting.ConversationStarter, "_bootstrap", original)
    recovered = start(home)
    assert recovered.status_code == 200, recovered.text
    assert home.config.paths.project_db(recovered.json()["project_id"]) == db
    assert (db.stat().st_dev, db.stat().st_ino) == identity
    assert len(home.client.get("/api/projects").json()["projects"]) == 1
