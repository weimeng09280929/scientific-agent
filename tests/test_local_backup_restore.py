"""Backup/restore acceptance with real FastAPI, SQLite and disposable files."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from core.local_backup import BackupError, backup, verify, restore, inventory, digest, write_json, precheck
from core.instance_state import MARKER, data_lease, running_instance
from core.storage_paths import project_file
from tests.local_backup_fixture import seed, app_at, login, expect


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    base = tmp_path_factory.mktemp("sra-backup-sample")
    source = base / "source"
    data = seed(source)
    out = base / "backup"
    result = backup(source, out, "accounts", confirm_stopped=True)
    assert result["ok"] and result["projects"] == 2
    manifest = verify(out)
    assert manifest["summary"]["file_references"] >= 6
    return source, out, data, manifest


def copy_restore(sample, tmp_path):
    source, out, data, manifest = sample
    target = tmp_path / "restored"
    assert restore(out, target)["old_sessions_removed"]
    return target, data, manifest


def test_restore_preserves_identity_bytes_materials_idempotency_and_no_replay(sample, tmp_path):
    source, out, data, manifest = sample
    source_before = inventory(source, manifest["layout"])
    target, _, _ = copy_restore(sample, tmp_path)
    # Every project DB/file is bit-identical, not reconstructed from rendered text.
    for record in manifest["files"]:
        if record["path"] != manifest["layout"]["accounts_db"]:
            assert digest(target / record["path"]) == record["sha256"]
    with closing(sqlite3.connect(source / "accounts.db")) as a, closing(sqlite3.connect(target / "accounts.db")) as b:
        def identities(conn):
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
        assert identities(a) == identities(b)
        assert a.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
        assert b.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert a.execute("SELECT * FROM project_owners ORDER BY project_id").fetchall() == b.execute("SELECT * FROM project_owners ORDER BY project_id").fetchall()
    with app_at(target) as (api, state):
        with TestClient(api.app) as a, TestClient(api.app) as b:
            for i, client in enumerate((a, b)):
                client.cookies.set("sra_session", data["_old_cookies"][i].get_secret_value())
                assert client.get("/api/projects").status_code == 401
                client.cookies.clear()
                assert login(client, i)["id"] == data["user_ids"][i]
                info = data["users"][i]; pid = info["project_id"]; base = f"/api/projects/{pid}"
                assert [r["project_id"] for r in expect(client.get("/api/projects"))["projects"]] == [pid]
                assert expect(client.post("/api/conversations/start", json=info["start_body"]))["project_id"] == pid
                assert len(expect(client.get(base + "/conversations"))["conversations"]) == 4
                workspace = expect(client.get(base + "/workspace"))["workspace"]
                plans = {x["item_id"]: x for x in workspace["items"] if x["kind"] == "plan"}
                assert plans[info["plan_ids"][0]]["status"] == "superseded"
                assert plans[info["plan_ids"][1]]["status"] == "active"
                assert len(plans) == 2 and plans[info["plan_ids"][1]]["plan_version"] == 2
                from core.workspace.conversations import ConversationService
                service = ConversationService(pid, target / "projects" / pid / "knowledge.db")
                assert service.material_state()["fingerprint"] == info["material_fingerprint"]
                drafts = expect(client.get(base + f"/conversations/{info['conversation_id']}/suggestions"))["suggestions"]
                draft = next(x for x in drafts if x["suggestion_id"] == info["draft_id"])
                assert draft["applied_item_id"] is None
                req = base + f"/conversations/{info['second_conversation_id']}/requests/"
                interrupted = expect(client.get(req + "interrupted-chat"))
                assert interrupted["status"] == "interrupted" and interrupted["attempt"] == 1
                assert len(interrupted["messages"]) == 1
                bad = client.post(req + "failed-structure/retry", json={})
                assert bad.status_code == 422 and not bad.json()["failure"]["can_retry"]
                budget_url = base + f"/conversations/{info['budget_conversation_id']}/requests/upper-exhausted"
                limited = expect(client.get(budget_url))
                assert limited["failure"]["model_attempts_limit"] == 1 and limited["failure"]["model_attempts_remaining"] == 0
                assert not limited["failure"]["can_retry"] and not limited["failure"]["can_restart"]
                rejected = client.post(budget_url + "/retry", json={})
                assert rejected.status_code >= 400 and rejected.json()["attempt"] == 1
                assert client.post(budget_url + "/restart", json={"acknowledge_model_cost": True}).status_code >= 400
                pending_task = expect(client.get(base + f"/conversations/{info['task_conversation_id']}/research-tasks/{info['interrupted_task_id']}"))
                assert pending_task["status"] == "interrupted" and pending_task["attempts"] == 1
                assert "恢复后仍属于本账号" in client.get(base + "/notes").text
                for suffix in (f"/papers/{info['paper_id']}/pdf", f"/exports/artifacts/{info['artifact_id']}?format=md", f"/exports/items/{info['plan_ids'][0]}?format=md", f"/research-tasks/{info['task_id']}/export?version=1"):
                    response = client.get(base + suffix)
                    assert response.status_code == 200
                with closing(sqlite3.connect(target / "projects" / pid / "knowledge.db")) as conn:
                    raw = json.loads(conn.execute("SELECT content FROM papers WHERE paper_id=?", (info["paper_id"],)).fetchone()[0])
                    resolved = project_file(raw["pdf_path"], target / "projects" / pid)
                    assert resolved.is_relative_to(target)
                    assert client.get(base + f"/papers/{info['paper_id']}/pdf").content == resolved.read_bytes()
                # No model/source execution was caused by login, reads, exports,
                # idempotent start or forbidden structure retry.
                assert state["guidance_calls"] == state["model_calls"] == state["source_calls"] == 0
            # Continue only through an explicit action in the restored copy.
            info = data["users"][0]; base = f"/api/projects/{info['project_id']}"
            assert a.post(base + "/notes", json={"text": "只新增到恢复副本"}).status_code == 200
            recovered = expect(a.post(base + f"/conversations/{info['second_conversation_id']}/requests/interrupted-chat/retry", json={"wait_for_completion": True}))
            assert recovered["attempt"] == 2 and recovered["status"] == "completed"
            assert [m["role"] for m in recovered["messages"]] == ["user", "assistant"]
            assert state["guidance_calls"] == 1
    assert inventory(source, manifest["layout"]) == source_before


@pytest.mark.parametrize("operation", ["list", "read", "write", "cancel", "retry", "adopt", "attachment", "report", "artifact", "mixed"])
def test_restored_non_owner_cannot_access_known_resource_ids(sample, tmp_path, operation):
    target, data, _ = copy_restore(sample, tmp_path)
    a, b = data["users"]
    base = f"/api/projects/{a['project_id']}"
    with app_at(target) as (api, state), TestClient(api.app) as client:
        login(client, 1)
        if operation == "list":
            listing = expect(client.get("/api/projects", params={"q": a["project_id"]}))
            assert listing["projects"] == [] and listing["matched_total"] == 0
            assert a["project_id"] not in client.get("/api/conversations/recent").text
        else:
            routes = {
                "read": ("GET", base + "/workspace", None),
                "write": ("POST", base + "/notes", {"text": "must not write"}),
                "cancel": ("POST", base + f"/conversations/{a['task_conversation_id']}/research-tasks/{a['interrupted_task_id']}/cancel", {}),
                "retry": ("POST", base + f"/conversations/{a['second_conversation_id']}/requests/interrupted-chat/retry", {}),
                "adopt": ("POST", base + f"/conversations/{a['conversation_id']}/suggestions/{a['draft_id']}/apply", {}),
                "attachment": ("GET", base + f"/papers/{a['paper_id']}/pdf", None),
                "report": ("GET", base + f"/research-tasks/{a['task_id']}/export?version=1", None),
                "artifact": ("GET", base + f"/exports/artifacts/{a['artifact_id']}?format=md", None),
                "mixed": ("GET", f"/api/projects/{b['project_id']}/conversations/{a['conversation_id']}", None),
            }
            method, url, body = routes[operation]
            response = client.request(method, url, **({"json": body} if body is not None else {}))
            assert response.status_code == 404
        assert state["guidance_calls"] == state["model_calls"] == state["source_calls"] == 0


@pytest.mark.parametrize("damage", ["missing", "changed", "format", "contract", "traversal", "duplicate", "sqlite", "sqlite-version", "sidecar"])
def test_damaged_backup_rejected_before_any_target_publication(sample, tmp_path, damage):
    out = tmp_path / "broken"; shutil.copytree(sample[1], out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    if damage == "missing": (out / "payload" / manifest["files"][0]["path"]).unlink()
    elif damage == "changed": (out / "payload" / manifest["files"][0]["path"]).write_bytes(b"damaged")
    elif damage == "format": manifest["format"] = 999
    elif damage == "contract": manifest["storage_contract"] = 999
    elif damage == "traversal": manifest["files"][0]["path"] = "../outside"
    elif damage == "duplicate": manifest["files"].append(manifest["files"][0])
    elif damage == "sidecar":
        p = out / "payload/accounts.db-wal"; p.write_bytes(b"not a stopped snapshot")
        manifest["files"].append({"path": "accounts.db-wal", "size": p.stat().st_size, "sha256": digest(p)})
    elif damage in ("sqlite", "sqlite-version"):
        record = next(x for x in manifest["files"] if x["path"] == "accounts.db")
        p = out / "payload/accounts.db"
        if damage == "sqlite": p.write_bytes(b"Not SQLite")
        else:
            with closing(sqlite3.connect(p)) as conn: conn.execute("PRAGMA user_version=99")
        record.update(size=p.stat().st_size, sha256=digest(p))
    write_json(out / "manifest.json", manifest)
    (out / "COMPLETE").write_text(digest(out / "manifest.json"), encoding="ascii")
    with pytest.raises((BackupError, sqlite3.Error)):
        restore(out, tmp_path / "target")
    assert not (tmp_path / "target").exists() and not (tmp_path / "outside").exists()


def test_nonempty_target_and_nested_backup_do_not_overwrite(sample, tmp_path):
    target = tmp_path / "existing"; target.mkdir(); (target / "keep").write_bytes(b"keep")
    with pytest.raises(BackupError): restore(sample[1], target)
    assert (target / "keep").read_bytes() == b"keep"
    with pytest.raises(BackupError): backup(sample[0], sample[0] / "bad", "accounts", confirm_stopped=True)


def test_running_actual_app_and_cross_process_lease_refuse_backup(sample, tmp_path):
    target, _, _ = copy_restore(sample, tmp_path)
    with app_at(target) as (api, state), TestClient(api.app) as client:
        with pytest.raises(RuntimeError): backup(target, tmp_path / "no-backup", "accounts", confirm_stopped=True)
        result = subprocess.run([sys.executable, "-m", "tools.local_backup", "backup", "--root", str(target), "--mode", "accounts", "--confirm-stopped", "--destination", str(tmp_path / "other")], capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert result.returncode == 2 and not (tmp_path / "other").exists()
    assert backup(target, tmp_path / "yes-backup", "accounts", confirm_stopped=True)["ok"]


@pytest.mark.parametrize("state", ["running", "unclean", "unknown", "unconfirmed", "wal", "work-lock"])
def test_stop_preconditions_are_not_guessed(sample, tmp_path, state):
    target, data, manifest = copy_restore(sample, tmp_path)
    if state in {"running", "unclean", "unknown"}:
        marker = json.loads((target / MARKER).read_text())
        marker["state"] = state; write_json(target / MARKER, marker)
    if state == "wal": (target / "accounts.db-wal").write_bytes(b"pending")
    if state == "work-lock":
        from core.workspace.conversation_lock import hold
        info = data["users"][0]
        with hold(target / "projects" / info["project_id"] / "knowledge.db", "held-work") as acquired:
            assert acquired
            with pytest.raises(BackupError): backup(target, tmp_path / "out", "accounts", confirm_stopped=True)
        return
    with pytest.raises(BackupError): backup(target, tmp_path / "out", "accounts", confirm_stopped=state != "unconfirmed")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("defect", ["owner", "conversation", "workspace", "missing-file", "artifact-parent", "audit", "bootstrap", "other-user-file"])
def test_structural_associations_checked_without_semantic_revalidation(sample, tmp_path, defect):
    target, data, manifest = copy_restore(sample, tmp_path)
    info = data["users"][0]; db = target / "projects" / info["project_id"] / "knowledge.db"
    if defect == "owner":
        db = target / "accounts.db"
        sql = "UPDATE project_owners SET project_id='missing' WHERE project_id=?"; args = (info["project_id"],)
    elif defect == "conversation": sql, args = "UPDATE sra_chat_messages SET project_id='another'", ()
    elif defect == "workspace":
        with closing(sqlite3.connect(db)) as conn:
            w = json.loads(conn.execute("SELECT value FROM kv_store WHERE key='research_workspace.v1'").fetchone()[0]); w["schema_version"] = 99
        sql, args = "UPDATE kv_store SET value=? WHERE key='research_workspace.v1'", (json.dumps(w),)
    elif defect == "missing-file":
        file = next(r for r in manifest["files"] if r["path"].endswith(".pdf")); (target / file["path"]).unlink()
        sql, args = "SELECT 1", ()
    elif defect == "artifact-parent":
        with closing(sqlite3.connect(db)) as conn:
            a = json.loads(conn.execute("SELECT content FROM artifacts LIMIT 1").fetchone()[0]); a["parent_version_id"] = "missing"
        sql, args = "UPDATE artifacts SET content=? WHERE artifact_id=?", (json.dumps(a), a["artifact_id"])
    elif defect == "audit": sql, args = "UPDATE sra_call_audit SET conversation_id='missing'", ()
    elif defect == "other-user-file":
        with closing(sqlite3.connect(db)) as conn:
            paper = json.loads(conn.execute("SELECT content FROM papers WHERE paper_id=?", (info["paper_id"],)).fetchone()[0])
        other = data["users"][1]
        paper["pdf_path"] = str(target / "projects" / other["project_id"] / "uploads" / (other["paper_id"] + "_same.pdf"))
        sql, args = "UPDATE papers SET content=? WHERE paper_id=?", (json.dumps(paper), info["paper_id"])
    else: db, sql, args = target / "projects/.conversation-starts.db", "UPDATE starts SET phase='reserved'", ()
    with closing(sqlite3.connect(db)) as conn, conn: conn.execute(sql, args)
    with pytest.raises(BackupError): backup(target, tmp_path / "bad", "accounts", confirm_stopped=True)
    assert not (tmp_path / "bad").exists()


def test_failed_copy_keeps_only_incomplete_stage_and_preserves_source(sample, tmp_path, monkeypatch):
    source, out, data, manifest = sample
    before = inventory(source, manifest["layout"])
    def fail(*a, **kw): raise OSError("controlled copy failure")
    monkeypatch.setattr(shutil, "copyfile", fail)
    with pytest.raises(OSError): backup(source, tmp_path / "failed", "accounts", confirm_stopped=True)
    assert not (tmp_path / "failed").exists()
    stages = list(tmp_path.glob(".sra-b-*")); assert len(stages) == 1
    assert (stages[0] / "INCOMPLETE").is_file()
    with pytest.raises(RuntimeError), running_instance(stages[0], stages[0] / "projects", None, "local"):
        pass
    (stages[0] / "INCOMPLETE").unlink()
    with pytest.raises(RuntimeError), running_instance(stages[0], stages[0] / "projects", None, "local"):
        pass  # Even a lost marker cannot turn a staging directory into an app.
    assert inventory(source, manifest["layout"]) == before


def test_hardlink_is_rejected_and_repeated_restore_keeps_original_paths(sample, tmp_path):
    target, data, manifest = copy_restore(sample, tmp_path)
    linked = target / "projects/hardlink.bin"; os.link(target / "accounts.db", linked)
    with pytest.raises(BackupError): backup(target, tmp_path / "bad", "accounts", confirm_stopped=True)
    linked.unlink()
    second_backup = tmp_path / "second-backup"
    assert backup(target, second_backup, "accounts", confirm_stopped=True)["ok"]
    third = tmp_path / "third"; assert restore(second_backup, third)["ok"]
    info = data["users"][0]
    db = third / "projects" / info["project_id"] / "knowledge.db"
    assert digest(db) == digest(sample[0] / "projects" / info["project_id"] / "knowledge.db")
    with closing(sqlite3.connect(db)) as conn:
        paper = json.loads(conn.execute("SELECT content FROM papers WHERE paper_id=?", (info["paper_id"],)).fetchone()[0])
    assert project_file(paper["pdf_path"], db.parent).is_relative_to(third)


def test_demo_backup_separate_unowned_and_archived_data(sample, tmp_path):
    from core.knowledge import KnowledgeStore
    from core.config import ProjectPaths
    from core.project_archive import archive_project_directories
    root = tmp_path / "demo"; paths = ProjectPaths.from_root(root)
    KnowledgeStore(paths.project_db("old_demo"))
    KnowledgeStore(paths.project_db("archived_demo"))
    archive_project_directories(paths, ["archived_demo"])
    assert backup(root, tmp_path / "demo-backup", "demo", confirm_stopped=True)["projects"] == 2
    target = tmp_path / "demo-restored"; assert restore(tmp_path / "demo-backup", target)["mode"] == "demo"
    assert not (target / "accounts.db").exists() and list((target / ".project-trash").glob("*/archived_demo/knowledge.db"))
    with pytest.raises(BackupError): backup(sample[0], tmp_path / "mixed", "demo", confirm_stopped=True)


def test_source_change_during_copy_and_failed_restore_are_never_published(sample, tmp_path, monkeypatch):
    target, data, _ = copy_restore(sample, tmp_path)
    original = shutil.copyfile; changed = []
    def changing(source, destination):
        result = original(source, destination)
        if not changed:
            changed.append(True)
            db = target / "projects" / data["users"][0]["project_id"] / "knowledge.db"
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("UPDATE kv_store SET value='[]' WHERE key='user.notes'")
        return result
    with monkeypatch.context() as fault:
        fault.setattr(shutil, "copyfile", changing)
        with pytest.raises(BackupError): backup(target, tmp_path / "changed", "accounts", confirm_stopped=True)
    assert not (tmp_path / "changed").exists()
    with monkeypatch.context() as fault:
        fault.setattr(shutil, "copyfile", lambda *a: (_ for _ in ()).throw(OSError("offline restore write fault")))
        with pytest.raises(OSError): restore(sample[1], tmp_path / "failed-restore")
    assert not (tmp_path / "failed-restore").exists()
    stages = list(tmp_path.glob(".sra-r-*"))
    assert len(stages) == 1 and (stages[0] / "INCOMPLETE").is_file()


def test_unclean_shutdown_holds_lease_until_process_exit_and_needs_normal_stop(sample, tmp_path):
    target, _, _ = copy_restore(sample, tmp_path)
    code = """import sys
from pathlib import Path
from core.instance_state import running_instance,data_lease
p=Path(sys.argv[1])
with running_instance(p,p/'projects',p/'accounts.db','accounts',clean_shutdown=lambda:False): pass
try:
 with data_lease(p,p/'projects',p/'accounts.db'): raise AssertionError('lease released early')
except RuntimeError: pass
"""
    child = subprocess.run([sys.executable, "-c", code, str(target)], capture_output=True, text=True)
    assert child.returncode == 0
    with pytest.raises(BackupError): backup(target, tmp_path / "unclean", "accounts", confirm_stopped=True)
    with app_at(target) as (api, state), TestClient(api.app): pass
    assert backup(target, tmp_path / "clean", "accounts", confirm_stopped=True)["ok"]


def test_archived_attachment_and_unowned_history_remain_private(sample, tmp_path):
    from core.config import ProjectPaths
    from core.project_archive import archive_project_directories
    from core.knowledge import KnowledgeStore
    target, data, _ = copy_restore(sample, tmp_path)
    paths = ProjectPaths.from_root(target)
    KnowledgeStore(paths.project_db("unowned_history"))
    pid = data["users"][0]["project_id"]
    batch = archive_project_directories(paths, [pid])
    packed = tmp_path / "archived-backup"
    assert backup(target, packed, "accounts", confirm_stopped=True)["projects"] == 3
    restored = tmp_path / "archived-restored"; restore(packed, restored)
    archived_db = restored / ".project-trash" / batch / pid / "knowledge.db"
    with closing(sqlite3.connect(archived_db)) as conn:
        paper = json.loads(conn.execute("SELECT content FROM papers WHERE paper_id=?", (data["users"][0]["paper_id"],)).fetchone()[0])
    mapped = project_file(paper["pdf_path"], archived_db.parent, data_root=restored)
    assert mapped.is_relative_to(archived_db.parent) and mapped.is_file()
    with app_at(restored) as (api, state), TestClient(api.app) as a, TestClient(api.app) as b:
        login(a, 0); login(b, 1)
        assert expect(a.get("/api/projects"))["projects"] == []
        assert [r["project_id"] for r in expect(b.get("/api/projects"))["projects"]] == [data["users"][1]["project_id"]]
        assert a.get("/api/projects/unowned_history/workspace").status_code == 404
        assert b.get(f"/api/projects/{pid}/workspace").status_code == 404


def test_windows_output_path_preflight_rejects_before_copy():
    from core.local_backup import output_paths_fit
    with pytest.raises(BackupError, match="路径过长"):
        output_paths_fit(Path("C:/") / ("x" * 230), [{"path": "projects/long-directory/knowledge.db"}], windows=True)
    output_paths_fit(Path("C:/short"), [{"path": "projects/normal/knowledge.db"}], windows=True)


def test_restored_launcher_uses_saved_registry_and_refuses_mode_change(sample, tmp_path):
    from unittest.mock import patch
    from tools.serve_local import configure
    target, _, _ = copy_restore(sample, tmp_path)
    (target / "accounts.db").rename(target / "projects/.local-accounts.db")
    marker = json.loads((target / MARKER).read_text())
    marker["accounts_db"] = str(target / "projects/.local-accounts.db"); write_json(target / MARKER, marker)
    package = tmp_path / "alternate-backup"
    backup(target, package, "accounts", confirm_stopped=True)
    restored = tmp_path / "alternate-restored"; restore(package, restored)
    with patch.dict(os.environ):
        configure("accounts", 8094, restored, False)
        assert Path(os.environ["SRA_ACCOUNTS_DB"]) == restored / "projects/.local-accounts.db"
        assert not (restored / "accounts.db").exists()
        with pytest.raises(ValueError): configure("demo", 8095, restored, False)
    with pytest.raises(RuntimeError), running_instance(restored, restored / "projects", None, "local"):
        pass


def test_external_project_layout_is_not_silently_omitted(tmp_path):
    from core.knowledge import KnowledgeStore
    root = tmp_path / "configured-root"
    external = tmp_path / "external-projects"
    KnowledgeStore(external / "one" / "knowledge.db")
    with running_instance(root, external, None, "local"):
        pass
    with pytest.raises(BackupError, match="实际项目"):
        backup(root, tmp_path / "wrong-empty-backup", "demo", confirm_stopped=True)
    assert not (tmp_path / "wrong-empty-backup").exists()
