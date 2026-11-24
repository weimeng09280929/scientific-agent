"""Step 4A: real HTTP routes + temporary SQLite; all reads must stay read-only."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import sqlite3
import threading

import pytest

from core.knowledge import KnowledgeStore, ArtifactType
from core.knowledge.schema import Artifact
from core.workspace.conversation_schema import ConversationCreate, ConversationSend
from core.workspace.conversations import ConversationService
from core.workspace.schema import ItemInput, ResearchFocus, Message, GuideDraft
from core.workspace.service import WorkspaceService, KEY
from tests.test_conversation_home import home, tree
from web.project_exports_api import build_exports_router


@pytest.fixture
def navigation(home):
    api, app = home.api, home.client.app
    app.add_api_route(
        "/api/conversations/recent", api.list_recent_conversations, methods=["GET"]
    )
    app.include_router(
        build_exports_router(
            lambda: api._CONFIG,
            api._require_project,
            lambda state: api._workspace_workflow(state, include_materials=True),
            api.download_artifact,
            api._make_download,
            api._md_to_docx_response,
            api._md_to_pdf_response,
        )
    )
    return home


def seed(n, pid="saved-project", topic="离线项目", stamp="2026-09-02T10:00:00"):
    api = n.api
    api._PROJECTS[pid] = api.ProjectState(
        project_id=pid, topic=topic, domain="ai", created_at="2099-01-01"
    )
    store = KnowledgeStore(n.config.paths.project_db(pid))
    store.save_kv("research.topic", topic)
    store.save_kv("project.domain", "ai")
    with (
        closing(
            sqlite3.connect(
                store._db_path
                if hasattr(store, "_db_path")
                else n.config.paths.project_db(pid)
            )
        ) as conn,
        conn,
    ):
        conn.execute("UPDATE kv_store SET updated_at=?", (stamp,))
    return ConversationService(pid, n.config.paths.project_db(pid))


def set_chat_time(service, stamp):
    with service._connection(True) as conn:
        conn.execute(
            "UPDATE sra_conversations SET created_at=?,updated_at=?", (stamp, stamp)
        )
        conn.execute("UPDATE kv_store SET updated_at=?", (stamp,))


def test_actual_regression_scan_time_never_makes_legacy_projects_recent(navigation):
    n = navigation
    seed(n, "validate_resume_28548", "热电历史项目", "2026-10-16T13:23:11")
    recent = seed(n, "proj_20261014_181948_2e33e5", "联邦学习", "2026-10-14T10:19:48")
    KnowledgeStore(recent.db_path).save_artifact(
        Artifact(
            artifact_id="actual-newer-doc",
            artifact_group="m",
            version=3,
            artifact_type=ArtifactType.METHOD_DOC,
            title="方法 v3",
            content="离线回归夹具",
            created_at=datetime(2026, 10, 17, 14, 24, 50),
        )
    )
    before = tree(n.root)
    data = n.client.get("/api/projects?limit=6").json()
    assert [p["project_id"] for p in data["projects"]] == [
        recent.project_id,
        "validate_resume_28548",
    ]
    assert data["projects"][0]["created_at"] == "2026-10-14T10:19:48+00:00"
    assert data["projects"][1]["activity_at"].startswith("2026-10-16")
    assert tree(n.root) == before
    # Empty registry startup/recovery yields identical identities and timestamps.
    n.api._PROJECTS.clear()
    again = n.client.get("/api/projects?limit=6").json()["projects"]
    assert [(p["project_id"], p["created_at"], p["activity_at"]) for p in again] == [
        (p["project_id"], p["created_at"], p["activity_at"]) for p in data["projects"]
    ]
    assert tree(n.root) == before


def test_153_projects_bounded_pages_search_and_current_pin(navigation):
    n = navigation
    for i in range(153):
        seed(n, f"catalog-{i:03d}", f"原主题 {i:03d}")
    first = n.client.get("/api/projects?limit=6&include_project_id=catalog-000").json()
    assert first["total"] == first["matched_total"] == 153
    assert len(first["projects"]) == 6 and first["next_offset"] == 6
    assert first["current_project"]["project_id"] == "catalog-000"
    found, offset = [], 0
    while offset is not None:
        data = n.client.get(f"/api/projects?limit=10&offset={offset}").json()
        assert len(data["projects"]) <= 10
        found.extend(p["project_id"] for p in data["projects"])
        offset = data["next_offset"]
    assert len(set(found)) == len(found) == 153
    assert len(n.client.get("/api/projects").json()["projects"]) == 153  # legacy shape
    n.client.patch(
        "/api/projects/catalog-010/identity", json={"display_name": "可搜索的显示名"}
    )
    for query in ("可搜索的显示名", "原主题 010", "catalog-010"):
        data = n.client.get("/api/projects", params={"limit": 10, "q": query}).json()
        assert [p["project_id"] for p in data["projects"]] == ["catalog-010"]
    assert n.client.get("/api/projects?limit=101").status_code == 422
    assert n.client.get("/api/projects?offset=-1").status_code == 422


def test_activity_changes_on_saved_chat_or_rename_not_reads(navigation):
    n = navigation
    a, b = seed(n, "activity-a"), seed(n, "activity-b")
    cid = a.create(ConversationCreate(title="对话", request_id="create"))[
        "conversation_id"
    ]
    set_chat_time(a, "2026-09-03T00:00:00")
    before = tree(n.root)
    for suffix in (
        "/api/projects?limit=6",
        "/api/conversations/recent?limit=8",
        f"/api/projects/{a.project_id}/conversations",
    ):
        assert n.client.get(suffix).status_code == 200
    assert tree(n.root) == before
    a.send(
        cid,
        ConversationSend(request_id="one", content="离线消息"),
        "offline",
        lambda *args: (GuideDraft(reply="离线回复"), "model"),
    )
    assert (
        n.client.get("/api/projects?limit=6").json()["projects"][0]["project_id"]
        == a.project_id
    )
    assert (
        n.client.patch(
            f"/api/projects/{b.project_id}/identity",
            json={"display_name": "新的展示名"},
        ).status_code
        == 200
    )
    assert (
        n.client.get("/api/projects?limit=6").json()["projects"][0]["project_id"]
        == b.project_id
    )
    assert a.shared.load().revision == b.shared.load().revision == 0


def test_recent_conversations_are_cross_project_metadata_only_and_paginate(navigation):
    n = navigation
    a, b = seed(n, "same-title-a"), seed(n, "same-title-b")
    expected = set()
    for service, count in [(a, 105), (b, 2)]:
        for i in range(count):
            c = service.create(
                ConversationCreate(request_id=f"c-{i}", title="同名对话")
            )
            expected.add((service.project_id, c["conversation_id"]))
    before = tree(n.root)
    seen, offset = [], 0
    while offset is not None:
        data = n.client.get(f"/api/conversations/recent?limit=8&offset={offset}").json()
        assert len(data["conversations"]) <= 8
        for c in data["conversations"]:
            assert c["project_name"] and c["project_id"] in (a.project_id, b.project_id)
            assert "messages" not in c
            seen.append((c["project_id"], c["conversation_id"]))
        offset = data["next_offset"]
    assert set(seen) == expected and len(seen) == len(expected)
    assert tree(n.root) == before


def test_recent_pin_checks_project_ownership_without_changing_activity(navigation):
    n = navigation
    a = seed(n, "pin-a")
    b = seed(n, "pin-b")
    cid = a.create(ConversationCreate(request_id="pin"))["conversation_id"]
    before = tree(n.root)
    response = n.client.get(
        "/api/conversations/recent",
        params={"include_project_id": a.project_id, "include_conversation_id": cid},
    ).json()
    assert response["current_conversation"]["conversation_id"] == cid
    for other in (b.project_id, "unknown"):
        data = n.client.get(
            "/api/conversations/recent",
            params={"include_project_id": other, "include_conversation_id": cid},
        ).json()
        assert data["current_conversation"] is None
    assert tree(n.root) == before


def test_recent_search_filters_before_pagination_and_keeps_reads_immutable(navigation):
    n = navigation
    a = seed(n, "search-project", topic="材料比较")
    wanted = a.create(ConversationCreate(title="目标 100%_ 讨论"))["conversation_id"]
    for i in range(12):
        a.create(ConversationCreate(title=f"较新普通会话 {i}"))
    b = seed(n, "another-project", topic="算法设计")
    b.create(ConversationCreate(title="别的项目"))
    before = tree(n.root)
    for query in ["目标", "100%_"]:
        found = n.client.get(
            "/api/conversations/recent", params={"q": query, "limit": 5}
        ).json()
        assert [c["conversation_id"] for c in found["conversations"]] == [wanted]
        assert found["next_offset"] is None
    pages = [
        n.client.get(
            "/api/conversations/recent",
            params={"q": "材料比较", "limit": 5, "offset": i},
        ).json()["conversations"]
        for i in [0, 5, 10]
    ]
    assert len({c["conversation_id"] for page in pages for c in page}) == 13
    assert all(c["project_id"] == "search-project" for page in pages for c in page)
    assert (
        n.client.get("/api/conversations/recent", params={"q": "不存在的名称"}).json()[
            "conversations"
        ]
        == []
    )
    assert tree(n.root) == before


def test_metadata_cache_only_invalidates_on_saved_db_changes(navigation, monkeypatch):
    from core import project_catalog as catalog

    n = navigation
    c = seed(n, "cache-check")
    first = catalog.project_metadata(c.db_path, c.project_id)
    original = catalog.sqlite3.connect
    calls = []

    def read(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog.sqlite3, "connect", read)
    assert catalog.project_metadata(c.db_path, c.project_id) == first and calls == []
    with closing(original(c.db_path)) as conn, conn:
        conn.execute("UPDATE kv_store SET updated_at=?", ("2026-10-20T10:11:12",))
    updated = catalog.project_metadata(c.db_path, c.project_id)
    assert updated["activity_at"] == "2026-10-20T10:11:12+00:00" and calls
    assert "mode=ro" in calls[0][0]


def test_unregistered_corrupt_database_stays_visible_without_repair(navigation):
    n = navigation
    c = seed(n, "unreadable-catalog")
    c.db_path.write_bytes(b"corrupt offline fixture")
    n.api._PROJECTS.clear()
    before = tree(n.root)
    data = n.client.get("/api/projects?limit=6").json()
    assert len(data["projects"]) == 1
    assert data["projects"][0]["data_status"] == "unreadable_database"
    assert data["projects"][0]["status"] == "unavailable"
    assert tree(n.root) == before


def test_catalog_legacy_missing_corrupt_reads_never_initialize_or_convert(navigation):
    n = navigation
    legacy = seed(n, "legacy")
    w = legacy.shared.load(include_chat=False)
    w.messages = [
        Message(
            message_id="original",
            role="user",
            content="历史",
            area="writing",
            mode="user",
            created_at="2026-08-01T00:00:00",
        )
    ]
    KnowledgeStore(legacy.db_path).save_kv(KEY, w.model_dump(mode="json"))
    missing = seed(n, "missing")
    missing.db_path.unlink()
    corrupt = seed(n, "corrupt")
    corrupt.db_path.write_bytes(b"not SQLite (test fixture)")
    before = tree(n.root)
    for _ in range(2):
        projects = n.client.get("/api/projects?limit=10").json()["projects"]
        assert {p["project_id"]: p["data_status"] for p in projects}[
            "missing"
        ] == "missing_database"
        data = n.client.get("/api/conversations/recent").json()
        assert [(c["conversation_id"], c["title"]) for c in data["conversations"]] == [
            (legacy.legacy_id, "历史对话")
        ]
        assert set(data["unavailable_projects"]) == {"missing", "corrupt"}
        assert n.client.get("/api/projects/missing/exports").status_code == 404
    assert tree(n.root) == before
    with closing(sqlite3.connect(legacy.db_path)) as conn:
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name='sra_conversations'"
        ).fetchall()


def test_exports_are_actual_versions_current_plan_and_history_with_markers(navigation):
    n = navigation
    c = seed(n)
    s = WorkspaceService(c.project_id, c.db_path)
    q = s.add_item(
        ItemInput(kind="question", title="当前问题", content="待研究"), 0
    ).items[-1]
    p = s.add_item(
        ItemInput(
            kind="plan",
            title="正式方案",
            content="拟验证 $N=2$；不是实测",
            related_ids=[q.item_id],
        ),
        1,
    ).items[-1]
    s.save_focus(ResearchFocus(question=q.item_id, plan=p.item_id), 2)
    for key, version, content, kind in [
        ("v1", 1, "旧方法", ArtifactType.METHOD_DOC),
        ("v2", 2, "新方法", ArtifactType.METHOD_DOC),
        ("empty", 3, "", ArtifactType.METHOD_DOC),
        ("draft", 1, "预期数据展示，非实测", ArtifactType.PAPER_DRAFT),
    ]:
        s.store.save_artifact(
            Artifact(
                artifact_id=key,
                artifact_group="method" if key != "draft" else "draft",
                version=version,
                artifact_type=kind,
                title=key,
                content=content,
            )
        )
    before = tree(n.root)
    response = n.client.get("/api/projects/saved-project/exports")
    assert response.status_code == 200, response.text
    data = response.json()
    entries = {e["key"]: e for e in data["entries"]}
    assert "artifact:empty" not in entries
    assert entries["artifact:draft"]["group"] == "history"
    assert entries["plan:" + p.item_id]["recommended"] is True
    assert entries["artifact:v1"]["version"].startswith("v1")
    assert entries["artifact:v2"]["version"].startswith("v2")
    doc = n.client.get(entries["artifact:draft"]["url"])
    assert (
        doc.status_code == 200
        and "预期数据展示，非实测" in doc.text
        and "历史产物" in doc.text
    )
    assert "旧方法" in n.client.get(entries["artifact:v1"]["url"]).text
    assert "新方法" in n.client.get(entries["artifact:v2"]["url"]).text
    plan = n.client.get(entries["plan:" + p.item_id]["url"])
    assert plan.status_code == 200 and "尚未验证" in plan.text and "$N=2$" in plan.text
    assert tree(n.root) == before
    w = s.load(include_chat=False)
    w.items[0].needs_review = True
    s.store.save_kv(KEY, w.model_dump(mode="json"))
    stale = n.client.get("/api/projects/saved-project/exports").json()["entries"][0]
    assert not stale["recommended"] and "复核" in stale["status_label"]
    assert "待复核" in n.client.get(stale["url"]).text


def test_no_outputs_no_placeholder_and_scoped_artifact_downloads(navigation):
    n = navigation
    a = seed(n, "exports-a")
    b = seed(n, "exports-b")
    assert n.client.get("/api/projects/exports-a/exports").json()["entries"] == []
    private = n.root / "private.md"
    private.write_text("不得读取", encoding="utf-8")
    KnowledgeStore(a.db_path).save_artifact(
        Artifact(
            artifact_id="foreign",
            artifact_group="f",
            version=1,
            artifact_type=ArtifactType.METHOD_DOC,
            title="foreign",
            content="自己的内容",
        )
    )
    KnowledgeStore(a.db_path).save_artifact(
        Artifact(
            artifact_id="outside",
            artifact_group="o",
            version=1,
            artifact_type=ArtifactType.METHOD_DOC,
            title="outside",
            content_path=str(private),
        )
    )
    before = tree(n.root)
    for path in (
        "/api/projects/exports-b/exports/artifacts/foreign",
        "/api/projects/exports-a/exports/artifacts/outside",
        "/api/projects/exports-a/exports/items/foreign",
    ):
        assert n.client.get(path).status_code == 404
    assert "outside" not in json.dumps(
        n.client.get("/api/projects/exports-a/exports").json()
    )
    assert (
        n.client.get(
            "/api/projects/exports-a/exports/artifacts/foreign?format=exe"
        ).status_code
        == 422
    )
    assert tree(n.root) == before


def test_original_relative_artifact_file_is_readable_but_stays_project_scoped(
    navigation,
):
    n = navigation
    c = seed(n, "relative-artifact")
    store = KnowledgeStore(c.db_path)
    target = n.config.paths.project_artifacts(c.project_id) / "group" / "v1.txt"
    target.parent.mkdir(parents=True)
    target.write_text("旧相对路径文档 · 非实测", encoding="utf-8")
    for key, path in (
        ("repo-relative", str(target.relative_to(n.root))),
        ("group-relative", "group/v1.txt"),
    ):
        store.save_artifact(
            Artifact(
                artifact_id=key,
                artifact_group=key,
                version=1,
                artifact_type=ArtifactType.METHOD_DOC,
                title=key,
                content_path=path,
            )
        )
    before = tree(n.root)
    entries = {
        e["key"]: e
        for e in n.client.get("/api/projects/relative-artifact/exports").json()[
            "entries"
        ]
    }
    for key in ("repo-relative", "group-relative"):
        response = n.client.get(entries["artifact:" + key]["url"])
        assert (
            response.status_code == 200 and "旧相对路径文档 · 非实测" in response.text
        )
    assert tree(n.root) == before


def test_method_group_link_selects_actual_version_and_upstream_review(navigation):
    n = navigation
    c = seed(n, "method-link")
    s = WorkspaceService(c.project_id, c.db_path)
    q = s.add_item(ItemInput(kind="question", title="问题", content="拟研究"), 0).items[
        -1
    ]
    for v in (1, 2):
        s.store.save_artifact(
            Artifact(
                artifact_id=f"method-{v}",
                artifact_group="stable-group",
                version=v,
                artifact_type=ArtifactType.METHOD_DOC,
                title="已有关联方法",
                content=f"原始方法 v{v}",
                created_by="rules_demo_fixture",
            )
        )
    source = next(a for a in s.catalog() if a["kind"] == "method")
    plan = s.link_material(
        "method", "stable-group", source["fingerprint"], q.item_id, s.load().revision
    ).items[-1]
    s.save_focus(
        ResearchFocus(question=q.item_id, plan=plan.item_id), s.load().revision
    )
    before = tree(n.root)
    data = n.client.get("/api/projects/method-link/exports").json()
    entries = {e["key"]: e for e in data["entries"]}
    assert entries["artifact:method-2"]["recommended"] is True
    assert not entries["artifact:method-1"]["recommended"]
    text = n.client.get(entries["artifact:method-2"]["url"]).text
    assert (
        "原始方法 v2" in text
        and "rules_demo_fixture" in text
        and "已关联当前方案" in text
    )
    assert tree(n.root) == before
    s.store.save_artifact(
        Artifact(
            artifact_id="method-3",
            artifact_group="stable-group",
            version=3,
            artifact_type=ArtifactType.METHOD_DOC,
            title="新方法待复核",
            content="尚未复核 v3",
        )
    )
    data = n.client.get("/api/projects/method-link/exports").json()
    entry = next(e for e in data["entries"] if e["key"] == "artifact:method-3")
    assert not entry["recommended"] and "待复核" in entry["status_label"]


def test_summary_exports_preserve_demo_review_and_history_markers(navigation):
    n = navigation
    c = seed(n, "summary-markers")
    store = KnowledgeStore(c.db_path)
    store.save_kv(
        "cross_validation_report",
        {
            "topic": "离线演示报告",
            "mode": "rules_demo",
            "needs_review": True,
            "is_teaching": True,
            "gaps": [],
        },
    )
    before = tree(n.root)
    entry = next(
        e
        for e in n.client.get("/api/projects/summary-markers/exports").json()["entries"]
        if e["key"] == "research-report"
    )
    assert "preserve_status=true" in entry["url"]
    result = n.client.get(entry["url"] + "&format=md")
    assert result.status_code == 200
    for marker in (
        "rules_demo",
        '"needs_review": true',
        '"is_teaching": true',
        "不表示已执行实验",
    ):
        assert marker in result.text
    assert tree(n.root) == before


def test_archive_blocks_live_chat_but_not_an_interrupted_request(navigation):
    n = navigation
    c = seed(n)
    cid = c.create(ConversationCreate(request_id="create"))["conversation_id"]
    entered, release = threading.Event(), threading.Event()

    def generation(*args):
        entered.set()
        assert release.wait(10)
        return GuideDraft(reply="离线回复"), "model"

    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(
            c.send,
            cid,
            ConversationSend(request_id="active", content="离线等待"),
            "offline",
            generation,
        )
        assert entered.wait(5)
        response = n.client.post(
            "/api/projects/batch-delete", json={"project_ids": [c.project_id]}
        )
        assert response.status_code == 409 and c.db_path.exists()
        release.set()
        task.result()
    orphan = c.create(ConversationCreate(request_id="orphan-create"))["conversation_id"]

    def crash(*args):
        raise SystemExit("offline interrupted fixture")

    with pytest.raises(SystemExit):
        c.send(
            orphan,
            ConversationSend(request_id="orphan", content="中断"),
            "offline",
            crash,
        )
    assert c.request_view(orphan, "orphan")["status"] == "interrupted"
    result = n.client.post(
        "/api/projects/batch-delete", json={"project_ids": [c.project_id]}
    )
    assert result.status_code == 200
    archived = n.root / ".project-trash" / result.json()["archive_id"] / c.project_id
    assert (archived / "knowledge.db").exists()
    assert not c.db_path.exists()
    # Manual restore documented in the UI: exact directory, no overwrite.
    archived.rename(n.config.paths.project_dir(c.project_id))
    assert (
        n.client.get("/api/projects?limit=6").json()["projects"][0]["project_id"]
        == c.project_id
    )
    assert (
        ConversationService(c.project_id, c.db_path).request_view(cid, "active")[
            "status"
        ]
        == "completed"
    )
    assert (
        ConversationService(c.project_id, c.db_path).request_view(orphan, "orphan")[
            "status"
        ]
        == "interrupted"
    )
