"""Stopped-instance backups. Never imports the app, migrates a DB or runs work."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
from uuid import uuid4

from core.instance_state import MARKER, data_lease, safe_directory, is_link
from core.storage_paths import RELOCATION, logical, project_file
from core.workspace.conversation_lock import _lock, _unlock

FORMAT = 1
CONTRACT = 2  # v2 understands conversation relocation receipts; v1 inputs remain readable.


class BackupError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BackupError(message)


def relative(value):
    require(isinstance(value, str) and bool(value), "清单路径无效")
    p = PurePosixPath(value)
    require(bool(p.parts) and not p.is_absolute() and p.as_posix() == value and
            not any(c in value for c in ("\\", ":", "\0")) and
            all(part not in (".", "..") and not part.endswith((".", " ")) for part in p.parts), "清单路径越界或不兼容")
    return p


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    require(not is_link(path) and path.is_file() and path.stat().st_nlink == 1, "元数据文件边界不安全")
    require(path.stat().st_size <= 16 * 1024 * 1024, "元数据过大，不能验证")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError):
        raise BackupError("元数据无法解析") from None


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def files_under(directory, *, skip_cache=True):
    """Reject links/reparse points/hard links, including ignored entries."""
    safe_directory(directory)
    for entry in sorted(directory.iterdir()):
        require(not is_link(entry), "数据树包含链接，拒绝跟随")
        if entry.is_dir():
            if not skip_cache or entry.name not in {"vectors", "__pycache__"}:
                yield from files_under(entry, skip_cache=skip_cache)
        else:
            require(entry.is_file() and entry.stat().st_nlink == 1, "数据树含特殊文件或硬链接")
            yield entry


def layout(root, mode, accounts_db=None):
    root = safe_directory(root)
    require(root.is_dir(), "源数据目录不存在")
    require(mode in ("accounts", "demo"), "必须明确选择 accounts 或 demo")
    db = None
    if mode == "accounts":
        if accounts_db is None:
            choices = [p for p in (root / "accounts.db", root / "projects/.local-accounts.db") if p.is_file()]
            require(len(choices) == 1, "请用 --accounts-db 明确指定根目录内的唯一账号库")
            db = choices[0]
        else:
            db = root.joinpath(*relative(accounts_db).parts)
        require(db.is_file() and db.resolve().is_relative_to(root), "账号库缺失或超出指定根目录")
        require(db.parent in (root, root / "projects"), "账号库仅支持数据根或 projects 直属文件")
    else:
        require(not any(p.exists() for p in (root / "accounts.db", root / "projects/.local-accounts.db")), "演示包不能包含账号库，请分开处理")
    return root, {"mode": mode, "projects": "projects", "accounts_db": db.relative_to(root).as_posix() if db else None}


def inventory(root, spec, *, locks=False):
    paths = []
    for directory in (root / "projects", root / ".project-trash"):
        if not directory.exists():
            continue
        for path in files_under(directory):
            name = path.name
            if name.endswith(("-wal", "-journal")):
                require(path.stat().st_size == 0, "发现非空 SQLite WAL／journal；请正常关闭所有连接后重新预检查，不支持热备")
                continue
            if name.endswith("-shm") or name.endswith(".part"):
                continue
            if name.endswith(".lock"):
                if locks and name.startswith((".chat-", ".research-")):
                    with path.open("r+b") as stream:
                        try:
                            _lock(stream)
                        except OSError:
                            raise BackupError("仍有会话、上传或检索工作持锁，请正常结束工作后备份") from None
                        _unlock(stream)
                continue
            paths.append(path)
    if spec["accounts_db"] and root / spec["accounts_db"] not in paths:
        paths.append(root / spec["accounts_db"])
    for name in (spec["accounts_db"],):
        if name:
            for suffix in ("-wal", "-journal"):
                side = root / (name + suffix)
                require(not side.exists() or side.stat().st_size == 0, "账号库仍有 SQLite 事务日志，请正常关闭实例")
    if (root / RELOCATION).exists():
        paths.append(root / RELOCATION)
    result = []
    seen = set()
    for path in sorted(set(paths)):
        safe_directory(path.parent)
        require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1, "备份文件边界无法确认")
        rel = path.relative_to(root).as_posix(); relative(rel)
        require(rel.casefold() not in seen, "文件名存在大小写冲突，无法安全迁移")
        seen.add(rel.casefold())
        result.append({"path": rel, "size": path.stat().st_size, "sha256": digest(path)})
    return result


def db_open(path):
    # WAL/journal must already have been rejected. Immutable mode avoids even
    # creating SHM or checkpointing/migrating the source while inspecting it.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)


def check_db(path, kind):
    with closing(db_open(path)) as conn:
        require(conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite 完整性检查失败")
        require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "SQLite 外键关联检查失败")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        require(version == (1 if kind == "accounts" else 0), "SQLite 存储版本不兼容，未执行升级")
        schema = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        tables = {r[0] for r in schema}
        required = {"users", "sessions", "project_owners", "login_attempts"} if kind == "accounts" else {"starts"} if kind == "starts" else {"papers", "kv_store"}
        require(required <= tables, "SQLite 必要表缺失")
        return {"kind": kind, "user_version": version,
                "schema_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()}


def project_databases(root):
    projects = {}
    base = root / "projects"
    if base.exists():
        for directory in base.iterdir():
            if directory.is_dir():
                require(re.fullmatch(r"[A-Za-z0-9_-]{1,120}", directory.name), "项目目录编号不兼容")
                require((directory / "knowledge.db").is_file(), "项目目录缺少数据库，不能生成完整备份")
                projects[directory.name] = directory / "knowledge.db"
    trash = root / ".project-trash"
    if trash.exists():
        for batch in trash.iterdir():
            require(batch.is_dir() and (batch / "manifest.json").is_file(), "归档批次缺少清单")
            record = read_json(batch / "manifest.json")
            require(record.get("status") == "archived", "存在尚未完成的项目归档")
            ids = record.get("project_ids")
            require(isinstance(ids, list) and len(set(ids)) == len(ids), "归档项目清单无效")
            actual = {p.name for p in batch.iterdir() if p.is_dir()}
            require(actual == set(ids), "归档项目与清单不一致")
            for pid in ids:
                require(isinstance(pid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,120}", pid), "归档项目编号无效")
                require(pid not in projects and (batch / pid / "knowledge.db").is_file(), "项目重复或归档数据库缺失")
                projects[pid] = batch / pid / "knowledge.db"
    return projects


def _json(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        raise BackupError("研究记录 JSON 不完整") from None


def inspect_data(root, spec, records, *, origins=None):
    indexed = {r["path"] for r in records}
    projects = project_databases(root)
    databases = {}
    references = 0
    conversations = {}
    move_records={}
    move_locations={}
    for pid, db in projects.items():
        databases[db.relative_to(root).as_posix()] = check_db(db, "project")
        with closing(db_open(db)) as conn:
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            papers = {r[0] for r in conn.execute("SELECT paper_id FROM papers")}
            for table, id_key, field in (("papers", "paper_id", "pdf_path"), ("artifacts", "artifact_id", "content_path")):
                if table not in tables:
                    continue
                entities = {r[id_key] for r in conn.execute(f'SELECT "{id_key}" FROM "{table}"')}
                for row in conn.execute(f'SELECT "{id_key}",content FROM "{table}"'):
                    data = _json(row["content"])
                    require(data.get(id_key) == row[id_key], "研究实体身份不一致")
                    if data.get(field):
                        target = project_file(data[field], db.parent, data_root=root, origins=origins)
                        require(target.is_relative_to(db.parent) and target.is_file(), "附件或产物引用缺失／越出项目")
                        require(target.relative_to(root).as_posix() in indexed, "附件或产物未纳入备份")
                        references += 1
                    if table == "artifacts" and data.get("parent_version_id"):
                        require(data["parent_version_id"] in entities, "产物父版本缺失")
            if "paper_chunks" in tables:
                require(all(r[0] in papers for r in conn.execute("SELECT paper_id FROM paper_chunks")), "正文片段的文献关联缺失")
            row = conn.execute("SELECT value FROM kv_store WHERE key='research_workspace.v1'").fetchone()
            items = set()
            if row:
                workspace = _json(row[0])
                require(workspace.get("schema_version", 1) == 1 and workspace.get("project_id") == pid, "工作台版本或项目归属不一致")
                values = workspace.get("items", [])
                items = {i["item_id"] for i in values}
                require(len(items) == len(values), "正式材料身份重复")
                for item in values:
                    require(set(item.get("paper_ids", [])) <= papers, "正式材料的文献关联缺失")
                    require(set(item.get("related_ids", [])) <= items, "正式材料的关联目标缺失")
            cids = set()
            if "sra_conversations" in tables:
                rows = conn.execute("SELECT project_id,conversation_id FROM sra_conversations").fetchall()
                require(all(r["project_id"] == pid for r in rows), "会话与项目不匹配")
                cids = {r["conversation_id"] for r in rows}
            conversations[pid] = cids
            if 'sra_conversation_moves' in tables:
                local=[]
                for row in conn.execute('SELECT * FROM sra_conversation_moves'):
                    data=_json(row['data'])
                    require(all(data.get(k)==row[k] for k in ('move_id','request_id','conversation_id','source_project_id','target_project_id','generation')), '移动回执字段不一致')
                    require(isinstance(data.get('message_origins'),dict) and isinstance(data.get('request_origins'),dict) and data['source_project_id']!=data['target_project_id'], '移动来源记录不完整')
                    require(row['move_id'] not in move_records or move_records[row['move_id']]==data,'移动历史副本不一致')
                    move_records[row['move_id']]=data;local.append(data)
                move_locations[pid]=local
            if "sra_chat_meta" in tables:
                v = conn.execute("SELECT value FROM sra_chat_meta WHERE key='migration_version'").fetchone()
                require(v is None or v[0] == "1", "会话存储版本不兼容")
            for table in ("sra_chat_requests", "sra_chat_messages", "sra_chat_suggestions", "sra_research_tasks"):
                if table not in tables:
                    continue
                for row in conn.execute(f'SELECT * FROM "{table}"'):
                    require(row["project_id"] == pid and row["conversation_id"] in cids, "嵌套研究记录归属不一致")
                    if table == "sra_chat_suggestions":
                        data = _json(row["data"])
                        require(not data.get("applied_item_id") or data["applied_item_id"] in items, "草案采纳目标缺失")
            if "sra_upload_requests" in tables:
                for row in conn.execute("SELECT * FROM sra_upload_requests"):
                    require(row["project_id"] == pid, "附件回执与项目不匹配")
                    require(row["status"] != "completed" or row["paper_id"] in papers, "已完成回执缺少文献")
                    if row["prepared"]:
                        paper = _json(row["prepared"]).get("data", {}).get("paper") or {}
                        if paper.get("pdf_path"):
                            target = project_file(paper["pdf_path"], db.parent, data_root=root, origins=origins)
                            require(target.is_relative_to(db.parent) and target.relative_to(root).as_posix() in indexed, "准备中的附件文件缺失或越界")
            if "sra_call_audit" in tables:
                tids = {r[0] for r in conn.execute("SELECT task_id FROM sra_research_tasks")} if "sra_research_tasks" in tables else set()
                for row in conn.execute("SELECT project_id,conversation_id,task_id FROM sra_call_audit"):
                    require(row[0] in (None, pid) and (row[1] is None or row[1] in cids) and (row[2] is None or row[2] in tids), "调用审计归属不一致")
    owned = set()
    if spec["accounts_db"]:
        path = spec["accounts_db"]
        databases[path] = check_db(root / path, "accounts")
        with closing(db_open(root / path)) as conn:
            owners = conn.execute("SELECT project_id FROM project_owners").fetchall()
            owned = {r[0] for r in owners}
            require(owned <= projects.keys(), "项目归属记录指向缺失项目")
            owners_by_id={r[0]:r[1] for r in conn.execute('SELECT project_id,user_id FROM project_owners')}
    def relocated(pid,cid):
        visited=set()
        for _ in range(32):
            if pid in visited or pid not in projects:return False
            visited.add(pid)
            if cid in conversations[pid]:return True
            records=[r for r in move_locations.get(pid,[]) if r['source_project_id']==pid and r['conversation_id']==cid]
            if not records:return False
            pid=max(records,key=lambda r:r['generation'])['target_project_id']
        return False
    for record in move_records.values():
        source,target=record['source_project_id'],record['target_project_id']
        require(spec['mode']=='accounts' and source in owned and target in owned and owners_by_id[source]==owners_by_id[target], '移动记录缺少同账号项目归属')
        require(relocated(source,record['conversation_id']), '移动记录不能解析到有效会话')
    actual=[cid for ids in conversations.values() for cid in ids]
    require(len(actual)==len(set(actual)), '同一会话存在多份可写副本')
    starts = root / "projects/.conversation-starts.db"
    if starts.exists():
        databases[starts.relative_to(root).as_posix()] = check_db(starts, "starts")
        with closing(db_open(starts)) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute("SELECT * FROM starts"):
                require(row["phase"] == "ready" and relocated(row['project_id'],row['conversation_id']), "首发幂等记录尚未完成或关联缺失，请先按原请求核对恢复")
    known_dbs = set(databases)
    require(all(r["path"] in known_dbs for r in records if Path(r["path"]).suffix.lower() in (".db", ".sqlite", ".sqlite3")), "存在尚未支持的数据库，不能确认完整性")
    return {"databases": databases, "projects": len(projects), "unowned_projects": len(projects.keys() - owned) if spec["mode"] == "accounts" else len(projects), "file_references": references}


def precheck(root, mode, accounts_db=None, *, confirm_stopped=False):
    require(confirm_stopped, "必须先正常停止实例及 CLI／后台任务，并显式传入 --confirm-stopped；不支持在线备份")
    root, spec = layout(root, mode, accounts_db)
    marker = root / MARKER
    if marker.exists():
        state = read_json(marker)
        require(state.get("version") == 1 and state.get("state") == "stopped", "实例状态不是已正常停止；请核对工作并正常启动／关闭实例，不支持未确认快照")
        require(state.get("mode") == ("local" if mode == "demo" else "accounts"), "实例模式与备份模式不匹配")
        require(state.get("projects") == str((root / "projects").resolve()) and
                state.get("accounts_db") == (str((root / spec["accounts_db"]).resolve()) if spec["accounts_db"] else None),
                "实际项目／账号库路径与备份范围不一致或尚未确认；请核对配置并正常启动／关闭，外置布局暂不支持")
    records = inventory(root, spec, locks=True)
    return root, spec, records, inspect_data(root, spec, records)


def empty_destination(path):
    path = safe_directory(path)
    require(not path.name.startswith((".sra-b-", ".sra-r-")), "目标不能使用工具的暂存目录名前缀")
    require(path.parent.is_dir(), "目标的父目录须已存在")
    require(not path.exists() or (path.is_dir() and not any(path.iterdir())), "目标必须是明确指定的新空目录，不能覆盖已有内容")
    return path


def publish(stage, target):
    if target.exists():
        require(target.is_dir() and not any(target.iterdir()), "目标在操作期间变为非空，未发布")
        target.rmdir()  # Only the checked empty directory; never recursive.
    stage.rename(target)


def output_paths_fit(base, records, *, windows=os.name == "nt"):
    if windows:
        require(all(len(str(base / r["path"])) < 248 for r in records), "Windows 目标路径过长；请选择更短的备份／恢复父目录，未开始复制")


def backup(root, destination, mode, accounts_db=None, *, confirm_stopped=False):
    require(confirm_stopped, "必须先正常停止实例并传入 --confirm-stopped；不支持热备")
    root, spec = layout(root, mode, accounts_db)
    target = empty_destination(destination)
    require(not target.is_relative_to(root) and not root.is_relative_to(target), "备份目标与源目录不能互相包含")
    with data_lease(root, root / "projects", root / spec["accounts_db"] if spec["accounts_db"] else None):
        root, spec, before, summary = precheck(root, mode, spec["accounts_db"], confirm_stopped=confirm_stopped)
        stage = target.with_name(".sra-b-" + uuid4().hex[:10])
        output_paths_fit(stage / "payload", before)
        output_paths_fit(target / "payload", before)
        stage.mkdir(); (stage / "INCOMPLETE").write_text("Not a usable backup", encoding="utf-8")
        payload = stage / "payload"; payload.mkdir()
        try:
            for record in before:
                out = payload / record["path"]; out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / record["path"], out)
                require(digest(out) == record["sha256"], "复制期间内容变化，未生成可用备份")
            require(inventory(root, spec, locks=True) == before, "源数据在复制期间发生变化，未生成可用备份")
            # Validate relocated references without changing any payload bytes.
            origins = [str(root)]
            if (root / RELOCATION).exists():
                origins += read_json(root / RELOCATION)["origins"]
            copied = inspect_data(payload, spec, before, origins=origins)
            require(copied == summary, "复制后的关联检查与源不一致")
            manifest = {"format": FORMAT, "storage_contract": CONTRACT, "app_version": "0.1.0", "sqlite_version": sqlite3.sqlite_version,
                        "created_at": datetime.now(timezone.utc).isoformat(), "source_root": str(root), "origins": list(dict.fromkeys(origins)),
                        "layout": spec, "files": before, "summary": summary}
            write_json(stage / "manifest.json", manifest)
            (stage / "COMPLETE").write_text(digest(stage / "manifest.json"), encoding="ascii")
            (stage / "INCOMPLETE").unlink()
            verify(stage)
            publish(stage, target)
            return {"ok": True, "mode": mode, "files": len(before), "projects": summary["projects"]}
        except BaseException:
            (stage / "INCOMPLETE").write_text("Failed; not a usable backup", encoding="utf-8")
            raise


def verify(directory):
    directory = safe_directory(directory)
    require(directory.is_dir(), "备份目录不存在")
    require({p.name for p in directory.iterdir()} == {"manifest.json", "COMPLETE", "payload"}, "备份不完整或包含未确认文件")
    for p in directory.iterdir():
        require(not is_link(p), "备份入口含链接")
    seal = directory / "COMPLETE"
    require(seal.is_file() and seal.stat().st_nlink == 1 and seal.stat().st_size == 64, "备份完成标记无效")
    require(seal.read_text(encoding="ascii") == digest(directory / "manifest.json"), "备份清单校验失败")
    manifest = read_json(directory / "manifest.json")
    require(isinstance(manifest, dict), "备份清单格式无效")
    require(manifest.get("format") == FORMAT and manifest.get("storage_contract") in (1,CONTRACT), "备份格式或数据契约不兼容")
    spec = manifest["layout"]
    require(isinstance(spec, dict), "备份布局格式无效")
    require(spec.get("mode") in ("demo", "accounts") and spec.get("projects") == "projects", "备份布局不兼容")
    require(bool(spec.get("accounts_db")) == (spec["mode"] == "accounts"), "备份账号模式不一致")
    if spec["accounts_db"]: relative(spec["accounts_db"])
    records = manifest["files"]
    require(isinstance(records, list) and len(records) <= 50000, "备份文件清单无效或超过本地工具上限")
    names = set(); folded = set()
    for record in records:
        require(isinstance(record, dict), "备份文件描述无效")
        p = relative(record["path"])
        require(not p.name.endswith(("-wal", "-shm", "-journal", ".lock", ".part")), "停机备份不能夹带事务日志、运行锁或临时文件")
        require(str(p) not in names and str(p).casefold() not in folded, "备份文件路径重复")
        require(isinstance(record["size"], int) and record["size"] >= 0 and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]), "备份文件描述无效")
        names.add(str(p)); folded.add(str(p).casefold())
    payload = directory / "payload"
    actual = {p.relative_to(payload).as_posix(): p for p in files_under(payload, skip_cache=False)}
    require(set(actual) == names, "备份文件缺失或多出未记录文件")
    for record in records:
        p = actual[record["path"]]
        require(p.stat().st_size == record["size"] and digest(p) == record["sha256"], "备份文件内容校验失败")
    # Check source-shaped paths against payload without changing backup bytes.
    require(isinstance(manifest.get("origins"), list) and manifest["source_root"] in manifest["origins"], "备份路径来源无效")
    require(all(isinstance(p, str) and logical(p).is_absolute() for p in manifest["origins"]), "备份路径来源不完整")
    for name in names:
        require(relative(name).parts[0] in {"projects", ".project-trash"} or name in {spec["accounts_db"], RELOCATION}, "备份包含范围外文件")
    require(inspect_data(payload, spec, records, origins=manifest["origins"]) == manifest["summary"], "备份数据库／关联与清单不一致")
    if manifest['storage_contract']==1:
        for db in project_databases(payload).values():
            with closing(db_open(db)) as conn:
                require(not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_conversation_moves'").fetchone(),'移动记录要求 v2 数据契约，不能伪装为旧版备份')
    return manifest


def restore(directory, destination):
    manifest = verify(directory)
    target = empty_destination(destination)
    directory = safe_directory(directory)
    require(not target.is_relative_to(directory) and not directory.is_relative_to(target), "恢复目标与备份目录不能互相包含")
    stage = target.with_name(".sra-r-" + uuid4().hex[:10])
    output_paths_fit(stage, manifest["files"])
    output_paths_fit(target, manifest["files"])
    stage.mkdir()
    (stage / "INCOMPLETE").write_text("Not a usable instance", encoding="utf-8")
    try:
        for record in manifest["files"]:
            out = stage / record["path"]; out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / "payload" / record["path"], out)
            require(digest(out) == record["sha256"], "备份在恢复过程中变化，未发布实例")
        write_json(stage / RELOCATION, {"version": 1, "origins": manifest["origins"]})
        summary = inspect_data(stage, manifest["layout"], manifest["files"])
        require(summary == manifest["summary"], "备份数据库／关联与清单不一致")
        accounts = manifest["layout"]["accounts_db"]
        if accounts:
            with closing(sqlite3.connect(stage / accounts)) as conn, conn:
                conn.execute("DELETE FROM sessions")
            check_db(stage / accounts, "accounts")
        write_json(stage / MARKER, {"version": 1, "mode": "accounts" if accounts else "local", "state": "stopped", "pid": None,
                                   "projects": str(target / "projects"), "accounts_db": str(target / accounts) if accounts else None})
        write_json(stage / ".sra-restore.json", {"version": 1, "layout": manifest["layout"], "restored_at": datetime.now(timezone.utc).isoformat(), "backup_manifest_sha256": digest(directory / "manifest.json"), "old_sessions_removed": True})
        (stage / "INCOMPLETE").unlink()
        publish(stage, target)
        return {"ok": True, "mode": manifest["layout"]["mode"], "projects": summary["projects"], "old_sessions_removed": True}
    except BaseException:
        (stage / "INCOMPLETE").write_text("Failed; not a usable instance", encoding="utf-8")
        raise
