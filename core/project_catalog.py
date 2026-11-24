"""Read-only project identity/activity projection; never uses scan time as data.

No schema initialization, file mtime heuristics, or persisted history repair here.
Naive database timestamps are UTC (the existing storage contract); timestamped
legacy project IDs were created in local Asia/Shanghai time.
"""

from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import sqlite3
from collections import OrderedDict
import threading

from core.workspace.conversations import ConversationService
from core.workspace import conversation_lock

_METADATA_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


def metadata_signature(db):
    # mtime is ONLY a cache invalidator, never an activity/creation timestamp.
    values = []
    for path in (db, Path(str(db) + "-wal")):
        try:
            stat = path.stat()
            values.append((stat.st_mtime_ns, stat.st_size, stat.st_ino))
        except OSError:
            values.append(None)
    return tuple(values)


def timestamp(value):
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def id_created_at(project_id):
    match = re.fullmatch(r"proj_(\d{8}_\d{6})_[A-Za-z0-9]+", project_id)
    if match:
        try:
            return (
                datetime.strptime(match[1], "%Y%m%d_%H%M%S")
                .replace(tzinfo=timezone(timedelta(hours=8)))
                .astimezone(timezone.utc)
                .isoformat()
            )
        except ValueError:
            pass
    return None


def project_metadata(db: Path, project_id: str):
    cache_key = (str(db.resolve()), project_id)
    signature = metadata_signature(db)
    with _CACHE_LOCK:
        cached = _METADATA_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            _METADATA_CACHE.move_to_end(cache_key)
            return dict(cached[1])
    result = {
        "project_id": project_id,
        "created_at": id_created_at(project_id),
        "activity_at": None,
        "activity_source": "unknown",
        "data_status": "available",
    }
    if not db.is_file():
        return {**result, "data_status": "missing_database"}
    try:
        with closing(
            sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "kv_store" not in tables:
                raise ValueError("project metadata table missing; no schema repair")
            times, kv = [], {}
            # Only storage timestamps, never directory enumeration/GET times.
            for table in sorted(tables):
                if not re.fullmatch(r"[A-Za-z0-9_]+", table):
                    continue
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                for column in ("created_at", "updated_at"):
                    if column in columns:
                        row = conn.execute(
                            f'SELECT MIN("{column}"),MAX("{column}") FROM "{table}"'
                        ).fetchone()
                        times.extend(
                            (stamp, table + "." + column)
                            for v in row
                            if (stamp := timestamp(v))
                        )
            if "kv_store" in tables:
                for key, value in conn.execute(
                    "SELECT key,value FROM kv_store WHERE key IN ('project.created_at','project.bootstrap_request','project.display_name','research.topic','project.domain','research_workspace.v1')"
                ):
                    kv[key] = json.loads(value)
            workspace = kv.get("research_workspace.v1") or {}
            if workspace.get("project_id") not in (None, project_id):
                raise ValueError("workspace project mismatch")
            for entry in workspace.get("messages", []) + workspace.get("items", []):
                if stamp := timestamp(entry.get("created_at")):
                    times.append((stamp, "workspace.content.created_at"))
            result["created_at"] = (
                timestamp(kv.get("project.created_at"))
                or result["created_at"]
                or (min(times)[0] if times else None)
            )
            if times:
                result["activity_at"], result["activity_source"] = max(times)
            result["activity_at"] = result["activity_at"] or result["created_at"]
            if result["activity_source"] == "unknown" and result["created_at"]:
                result["activity_source"] = "creation_fallback"
            for source, target in (
                ("project.display_name", "display_name"),
                ("research.topic", "topic"),
                ("project.domain", "domain"),
            ):
                if isinstance(kv.get(source), str):
                    result[target] = kv[source]
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        result["data_status"] = "unreadable_database"
    if signature == metadata_signature(db):
        with _CACHE_LOCK:
            _METADATA_CACHE[cache_key] = (signature, dict(result))
            while len(_METADATA_CACHE) > 512:
                _METADATA_CACHE.popitem(last=False)
    return result


def project_rows(paths, states):
    rows = []
    for state in states:
        pid = state.project_id
        directory = paths.project_dir(pid)
        if (
            directory.is_symlink()
            or directory.resolve().parent != paths.projects.resolve()
        ):
            continue
        metadata = project_metadata(paths.project_db(pid), pid)
        rows.append(
            {
                "project_id": pid,
                "topic": state.topic,
                "display_name": state.display_name or state.topic,
                "status": state.status,
                "summary": state.summary,
                "domain": state.domain,
                **metadata,
            }
        )
        rows[-1]["display_name"] = (
            rows[-1].get("display_name") or rows[-1]["topic"] or "未命名研究"
        )
    rows.sort(
        key=lambda p: (
            p["activity_at"] or p["created_at"] or "",
            p["created_at"] or "",
            p["project_id"],
        ),
        reverse=True,
    )
    return rows


def project_page(rows, *, limit=None, offset=0, q="", include_project_id=None):
    needle = q.strip().casefold()
    matches = [
        p
        for p in rows
        if needle
        in " ".join(
            str(p.get(k) or "") for k in ("display_name", "topic", "project_id")
        ).casefold()
    ]
    page = matches[offset : offset + limit] if limit else matches[offset:]
    current = next((p for p in rows if p["project_id"] == include_project_id), None)
    return {
        "projects": page,
        "total": len(rows),
        "matched_total": len(matches),
        "next_offset": (
            offset + limit if limit and offset + limit < len(matches) else None
        ),
        "current_project": current,
    }


def recent_conversations(paths, projects, *, limit=8, offset=0, q=""):
    # Bounded metadata only per project; never N HTTP requests or message pages.
    rows, unavailable = [], []
    term = q.strip().casefold()
    pattern = (
        "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )
    for p in projects:
        if p["data_status"] != "available":
            unavailable.append(p["project_id"])
            continue
        try:
            service = ConversationService(
                p["project_id"], paths.project_db(p["project_id"])
            )
            with service._connection() as conn:
                if service._has_tables(conn):
                    candidates = [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM sra_conversations WHERE project_id=? AND (title LIKE ? ESCAPE '\\' OR ?=1) ORDER BY updated_at DESC,conversation_id DESC LIMIT ?",
                            (
                                p["project_id"],
                                pattern,
                                int(
                                    not term
                                    or any(
                                        term in str(p.get(k, "")).casefold()
                                        for k in ("display_name", "topic", "project_id")
                                    )
                                ),
                                offset + limit + 1,
                            ),
                        )
                    ]
                else:
                    w = service._workspace(conn)
                    candidates = [service._default(w)] if w.messages else []
            for c in candidates:
                if c["project_id"] != p["project_id"]:
                    continue
                if term and not any(
                    term in str(value).casefold()
                    for value in (
                        c["title"],
                        p["display_name"],
                        p["topic"],
                        p["project_id"],
                    )
                ):
                    continue
                rows.append(
                    {
                        **c,
                        "project_name": p["display_name"],
                        "project_topic": p["topic"],
                    }
                )
        except (ValueError, sqlite3.Error):
            unavailable.append(p["project_id"])
    rows.sort(
        key=lambda c: (
            timestamp(c["updated_at"]) or "",
            c["project_id"],
            c["conversation_id"],
        ),
        reverse=True,
    )
    return {
        "conversations": rows[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "unavailable_projects": unavailable,
        "sort_basis": "conversation.updated_at (messages and conversation settings)",
    }


def has_active_chat(db, project_id):
    if not db.is_file():
        return False
    service = ConversationService(project_id, db)
    with service._connection() as conn:
        if not service._has_tables(conn):
            return False
        return any(
            conversation_lock.is_held(db, row[0])
            for row in conn.execute(
                "SELECT conversation_id FROM sra_chat_requests WHERE project_id=? AND status='processing'",
                (project_id,),
            )
        )
