"""Project-local chat persistence. GETs never migrate; models run outside transactions.

An OS lock owns each synchronous generation attempt, not a time-based lease.
Local persistence is idempotent; external provider billing is not exactly-once.
"""

from contextlib import closing, contextmanager, ExitStack, nullcontext
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from contextvars import copy_context
from uuid import uuid4, uuid5, NAMESPACE_URL

from core.product_boundary import NOTICE, POST_PLAN_KINDS
from core.workspace import conversation_lock
from core.workspace.conversation_schema import (
    ConversationCreate,
    ConversationPatch,
    ConversationSend,
)
from core.workspace.materials import fingerprint, mark_changed_sources
from core.workspace.reading_session import GUIDE_GOALS, passage_answer, resolve_passage, source_key
from core.workspace.schema import (
    Workspace,
    Message,
    Suggestion,
    ItemInput,
    GuideDraft,
    now,
)
from core.workspace.service import (
    KEY,
    WorkspaceService,
    WorkspaceError,
    RevisionConflict,
)

PROCESS_IDENTITY = f"{os.getpid()}:{uuid4().hex}"
STAGE_AREAS = {
    "orientation": "overview",
    "literature": "literature",
    "question": "question",
    "design": "study",
}
DDL = [
    """CREATE TABLE IF NOT EXISTS sra_conversations (
       conversation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
       discussion_goal TEXT NOT NULL, guidance_mode TEXT NOT NULL, entry_stage TEXT NOT NULL,
       case_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, chat_revision INTEGER NOT NULL DEFAULT 0,
       create_request_id TEXT, create_hash TEXT, is_legacy INTEGER NOT NULL DEFAULT 0,
       UNIQUE(project_id,create_request_id))""",
    """CREATE TABLE IF NOT EXISTS sra_chat_requests (
       conversation_id TEXT NOT NULL, request_id TEXT NOT NULL, project_id TEXT NOT NULL,
       payload TEXT NOT NULL, payload_hash TEXT NOT NULL, status TEXT NOT NULL,
       owner_token TEXT, owner_process TEXT, attempt INTEGER NOT NULL, created_at TEXT NOT NULL,
       updated_at TEXT NOT NULL, error TEXT, error_code INTEGER, material_fingerprint TEXT NOT NULL,
       based_on_revision INTEGER NOT NULL, PRIMARY KEY(conversation_id,request_id))""",
    "CREATE INDEX IF NOT EXISTS sra_chat_recent ON sra_conversations(project_id,updated_at DESC,conversation_id DESC)",
    """CREATE UNIQUE INDEX IF NOT EXISTS sra_chat_one_active ON sra_chat_requests(conversation_id) WHERE status='processing' """,
    """CREATE TABLE IF NOT EXISTS sra_chat_messages (
       seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
       project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, request_id TEXT, data TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS sra_chat_message_order ON sra_chat_messages(conversation_id,seq)",
    """CREATE TABLE IF NOT EXISTS sra_chat_suggestions (
       suggestion_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
       request_id TEXT, material_fingerprint TEXT, data TEXT NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS sra_chat_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS sra_chat_failures (
       conversation_id TEXT NOT NULL, request_id TEXT NOT NULL, reason TEXT NOT NULL,
       retry_at REAL, PRIMARY KEY(conversation_id,request_id))""",
]


def failure_reason(exc):
    """Classify the original transport/validation cause, not its LLM wrapper."""
    from core.invocation_audit import CallLimitError
    from core.llm.base import LLMStructureError, LLMTruncatedError
    seen = set()
    current = exc
    while current and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CallLimitError):
            return current.reason, current.retry_after_seconds
        name = type(current).__name__
        status = getattr(current, "status_code", None)
        if status in (401, 403):
            return "authentication", None
        if status == 429:
            from core.tools.search_result import retry_delay
            response = getattr(current, "response", None)
            return "rate_limited", retry_delay(response.headers.get("Retry-After")) if response is not None else None
        if name in ("APITimeoutError", "TimeoutError", "ConnectTimeout", "ReadTimeout"):
            return "timeout", None
        if name in ("APIConnectionError", "ConnectError", "RemoteProtocolError"):
            return "connection_error", None
        if isinstance(current, LLMTruncatedError):
            return "output_truncated", None
        if isinstance(current, LLMStructureError):
            return "structure_invalid", None
        if name in ("ValidationError", "JSONDecodeError", "SynthesisValidationError"):
            return "structure_invalid", None
        if name == "LLMError" and any(mark in str(current) for mark in
                                       ("不符合 schema", "非合法 JSON", "schema 定义本身")):
            return "structure_invalid", None
        current = current.__cause__ or current.__context__
    if isinstance(exc, WorkspaceError) or "Validation" in type(exc).__name__:
        return "structure_invalid", None
    return "generation_failed", None


class ConversationNotFound(WorkspaceError):
    pass


class ConversationStorageError(WorkspaceError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class ConversationService:
    def __init__(self, project_id: str, db_path: Path):
        self.project_id, self.db_path = project_id, Path(db_path)
        self.legacy_id = uuid5(
            NAMESPACE_URL, "scientific-agent:" + project_id + ":history"
        ).hex
        self._workspace_service = None

    @property
    def shared(self):
        if self._workspace_service is None:
            if not self.db_path.is_file():
                raise ConversationNotFound("项目数据库不存在")
            self._workspace_service = WorkspaceService(
                self.project_id, self.db_path, initialize_store=False
            )
        return self._workspace_service

    @contextmanager
    def _connection(self, write=False):
        if not self.db_path.is_file():
            raise ConversationNotFound("项目数据库不存在")
        try:
            with closing(
                sqlite3.connect(
                    self.db_path.resolve().as_uri()
                    + "?mode="
                    + ("rw" if write else "ro"),
                    uri=True,
                    timeout=15,
                )
            ) as conn:
                conn.row_factory = sqlite3.Row
                if write:
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        yield conn
                else:
                    yield conn
        except sqlite3.Error as exc:
            raise ConversationStorageError(
                "会话存储暂时不可用，本次事务未完成；请稍后读取状态再重试"
            ) from exc

    def _has_tables(self, conn):
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sra_chat_meta'"
            ).fetchone()
        )

    def _workspace(self, conn):
        row = conn.execute("SELECT value FROM kv_store WHERE key=?", (KEY,)).fetchone()
        w = (
            Workspace.model_validate_json(row[0])
            if row
            else Workspace(project_id=self.project_id)
        )
        if w.project_id != self.project_id:
            raise WorkspaceError("工作台与项目不匹配")
        return w

    def material_state(self):
        try:
            return self._material_state()
        except sqlite3.Error as exc:
            raise ConversationStorageError(
                "共享研究材料暂时不可读取，请核对项目数据库；未自动修复或改写历史数据"
            ) from exc

    def _material_state(self):
        w = self.shared.load(include_chat=False)
        # Selected full-text excerpts are model evidence too. Corrections to
        # text, page numbers or ordering must invalidate drafts even if paper
        # metadata and Workspace.revision have not changed.
        with self._connection() as conn:
            excerpts = [
                dict(row)
                for row in conn.execute(
                    "SELECT c.chunk_id,c.paper_id,c.chunk_index,c.text,c.page "
                    "FROM paper_chunks c JOIN papers p ON p.paper_id=c.paper_id "
                    "ORDER BY c.paper_id,c.chunk_index,c.chunk_id"
                )
            ]
        payload = {
            "revision": w.revision,
            "brief": w.brief.model_dump(),
            "focus": w.focus.model_dump(),
            # New optional metadata must not expire old fingerprints just by
            # loading the new schema (no material changed).
            "items": [
                {
                    k: v
                    for k, v in i.model_dump(mode="json").items()
                    if k not in ("plan", "plan_version") or v is not None
                }
                for i in w.items
            ],
            "sources": self.shared.catalog(),
            "papers": [
                p.model_dump(mode="json") for p in self.shared.store.list_papers()
            ],
            "paper_excerpts": fingerprint(excerpts),
            "topic": self.shared.store.get_kv("research.topic"),
            "domain": self.shared.store.get_kv("project.domain"),
            "claims": [
                c.model_dump(mode="json") for c in self.shared.store.list_claims()
            ],
            "research_report": self.shared.store.get_kv("cross_validation_report"),
            "notes": self.shared.store.get_kv("user.notes") or [],
        }
        return {
            "revision": w.revision,
            "fingerprint": fingerprint(payload),
            "workspace": w,
        }

    def _default(self, w):
        stamp = w.messages[0].created_at if w.messages else w.updated_at
        return {
            "conversation_id": self.legacy_id,
            "project_id": self.project_id,
            "title": "历史对话" if w.messages else "默认对话",
            "discussion_goal": "",
            "guidance_mode": "direct",
            "entry_stage": "orientation",
            "case_id": None,
            "created_at": stamp,
            "updated_at": w.messages[-1].created_at if w.messages else stamp,
            "chat_revision": w.chat_revision,
            "create_request_id": None,
            "create_hash": None,
            "is_legacy": 1,
        }

    def _row(self, conn, cid):
        row = (
            conn.execute(
                "SELECT * FROM sra_conversations WHERE conversation_id=? AND project_id=?",
                (cid, self.project_id),
            ).fetchone()
            if self._has_tables(conn)
            else None
        )
        if row:
            return self._discussion_meta(conn, dict(row))
        from core.workspace.conversation_moves import moved_target, ConversationMoved
        moved=moved_target(conn,self.project_id,cid)
        if moved:raise ConversationMoved(moved['target_project_id'],cid)
        w = self._workspace(conn)
        if cid == self.legacy_id and w.messages and not self._has_tables(conn):
            return self._default(w)
        raise ConversationNotFound("对话不存在或不属于当前项目")

    def _discussion_meta(self, conn, data):
        # A discussion focus is not research completion. Reuse the chat metadata
        # store; GET never creates tables or upgrades old conversations.
        if self._has_tables(conn):
            row = conn.execute("SELECT value FROM sra_chat_meta WHERE key=?",
                               ("discussion_step:" + data["conversation_id"],)).fetchone()
            if row:
                data["discussion_step"] = row[0]
        from core.workspace.conversation_moves import history
        moves=history(conn,data['conversation_id'])
        if moves:data['movement_history']=[{k:m[k] for k in ('move_id','generation','source_project_id','target_project_id','source_name','target_name','created_at')} for m in moves]
        return data

    def research_route(self, cid, topic):
        from core.workspace.guide import build_guide_context
        from core.workspace.research_coach import build_research_route, saved_reading_context

        meta = self.get(cid)
        context = build_guide_context(self.shared, topic, "overview", None, [], history=[])
        context["conversation"] = meta
        workflow = self.shared.workflow(self.shared.load(include_chat=False))
        context["current_plan"] = workflow["flow"].get("plan")
        route = build_research_route(context, "")
        route["saved_reports"] = saved_reading_context(self, route["readable_paper_ids"])
        route["product_stage"] = workflow.get("product_stage")
        route["product_boundary"] = workflow.get("product_boundary")
        route["planReady"] = workflow["flow"].get("planReady", False)
        return route

    def get(self, cid):
        with self._connection() as conn:
            return self._row(conn, cid)

    def list(self, limit=30, offset=0):
        self._page(limit, offset)
        with self._connection() as conn:
            rows = (
                [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM sra_conversations WHERE project_id=? ORDER BY updated_at DESC,conversation_id DESC LIMIT ? OFFSET ?",
                        (self.project_id, limit + 1, offset),
                    )
                ]
                if self._has_tables(conn)
                else []
            )
            if not self._has_tables(conn):
                w = self._workspace(conn)
                rows = [self._default(w)] if w.messages else []
                rows = rows[offset : offset + limit + 1]
        return {
            "conversations": rows[:limit],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    @staticmethod
    def _page(limit, offset):
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise WorkspaceError("分页范围无效（limit 为 1–100）")

    def _insert_conversation(self, conn, data):
        keys = list(data)
        conn.execute(
            "INSERT INTO sra_conversations ("
            + ",".join(keys)
            + ") VALUES ("
            + ",".join("?" for _ in keys)
            + ")",
            [data[k] for k in keys],
        )

    def _import_default(self, conn, w, basis):
        if conn.execute(
            "SELECT 1 FROM sra_conversations WHERE conversation_id=?", (self.legacy_id,)
        ).fetchone():
            return
        self._insert_conversation(conn, self._default(w))
        for m in w.messages:
            self._insert_message(
                conn, self.legacy_id, m.model_dump(mode="json"), m.request_id
            )
        for s in w.suggestions:
            # A matching revision cannot prove that old paper/source content is
            # unchanged. Preserve an existing dependency snapshot; never invent
            # one from today's materials while importing historical suggestions.
            dep = s.material_fingerprint
            self._insert_suggestion(
                conn, self.legacy_id, s.model_dump(mode="json"), None, dep
            )

    def _ensure(self, conn, basis, default=False):
        # execute individually: executescript would implicitly commit a migration.
        for sql in DDL:
            conn.execute(sql)
        if not conn.execute(
            "SELECT 1 FROM sra_chat_meta WHERE key='migration_version'"
        ).fetchone():
            w = self._workspace(conn)
            if w.messages:
                self._import_default(conn, w, basis)
            conn.execute("INSERT INTO sra_chat_meta VALUES ('migration_version','1')")
        if (
            default
            and not conn.execute(
                "SELECT 1 FROM sra_conversations WHERE conversation_id=?",
                (self.legacy_id,),
            ).fetchone()
        ):
            self._import_default(conn, self._workspace(conn), basis)

    def create(self, request: ConversationCreate):
        values = request.model_dump(exclude={"request_id"})
        if values.get("discussion_step") is None:
            values.pop("discussion_step", None)  # Preserve old create request hashes.
        signature = fingerprint(values)
        step = values.pop("discussion_step", None)
        with self._connection() as conn:
            self._check_moved_create(conn,request.request_id)
            existing = (
                conn.execute(
                    "SELECT * FROM sra_conversations WHERE project_id=? AND create_request_id=?",
                    (self.project_id, request.request_id),
                ).fetchone()
                if self._has_tables(conn)
                else None
            )
        if existing:
            if existing["create_hash"] != signature:
                raise RevisionConflict("request_id 已用于不同的会话内容")
            with self._connection() as conn:
                return self._discussion_meta(conn, dict(existing))
        basis = self.material_state()
        with self._connection(True) as conn:
            self._ensure(conn, basis)
            self._check_moved_create(conn,request.request_id)
            existing = conn.execute(
                "SELECT * FROM sra_conversations WHERE project_id=? AND create_request_id=?",
                (self.project_id, request.request_id),
            ).fetchone()
            if existing:
                if existing["create_hash"] != signature:
                    raise RevisionConflict("request_id 已用于不同的会话内容")
                return self._discussion_meta(conn, dict(existing))
            stamp = now()
            data = {
                **values,
                "conversation_id": uuid4().hex,
                "project_id": self.project_id,
                "created_at": stamp,
                "updated_at": stamp,
                "chat_revision": 0,
                "create_request_id": request.request_id,
                "create_hash": signature,
                "is_legacy": 0,
            }
            self._insert_conversation(conn, data)
            if step:
                conn.execute("INSERT INTO sra_chat_meta(key,value) VALUES (?,?)",
                             ("discussion_step:" + data["conversation_id"], step))
        return self.get(data["conversation_id"])

    def _check_moved_create(self,conn,rid):
        from core.workspace.conversation_moves import moved_target, ConversationMoved
        moved=moved_target(conn,self.project_id,create_id=rid)
        if moved and not conn.execute('SELECT 1 FROM sra_conversations WHERE conversation_id=?',(moved['conversation_id'],)).fetchone():
            raise ConversationMoved(moved['target_project_id'],moved['conversation_id'])

    def patch(self, cid, request: ConversationPatch):
        self.get(cid)
        values = request.model_dump(exclude_unset=True)
        if not values or any(v is None for k, v in values.items() if k != "case_id"):
            raise WorkspaceError("请提供有效的会话设置；只有 case_id 可以为空")
        basis = self.material_state()
        step = values.pop("discussion_step", None)
        with self._connection(True) as conn:
            self._ensure(conn, basis)
            self._row(conn, cid)
            if step:
                conn.execute("INSERT OR REPLACE INTO sra_chat_meta(key,value) VALUES (?,?)",
                             ("discussion_step:" + cid, step))
            conn.execute(
                "UPDATE sra_conversations SET "
                + "".join(k + "=?," for k in values)
                + "updated_at=?,chat_revision=chat_revision+1 WHERE conversation_id=? AND project_id=?",
                (*values.values(), now(), cid, self.project_id),
            )
        return self.get(cid)

    def _insert_message(self, conn, cid, data, rid=None):
        data = {**data, "conversation_id": cid, "request_id": rid}
        conn.execute(
            "INSERT INTO sra_chat_messages(message_id,project_id,conversation_id,request_id,data) VALUES (?,?,?,?,?)",
            (data["message_id"], self.project_id, cid, rid, encode(data)),
        )

    def _insert_suggestion(self, conn, cid, data, rid, dep):
        conn.execute(
            "INSERT INTO sra_chat_suggestions VALUES (?,?,?,?,?,?)",
            (data["suggestion_id"], self.project_id, cid, rid, dep, encode(data)),
        )

    def _effective(self, row):
        data = dict(row)
        if data["status"] == "processing" and not conversation_lock.is_held(
            self.db_path, data["conversation_id"]
        ):
            data.update(
                status="interrupted",
                error="原处理进程已退出或未持有请求锁，可明确重试",
                error_code=409,
            )
        return data

    def _request(self, conn, cid, rid):
        row = (
            conn.execute(
                "SELECT * FROM sra_chat_requests WHERE conversation_id=? AND request_id=? AND project_id=?",
                (cid, rid, self.project_id),
            ).fetchone()
            if self._has_tables(conn)
            else None
        )
        return dict(row) if row else None

    def _passage_record(self, conn, cid, rid):
        row = conn.execute("SELECT value FROM sra_chat_meta WHERE key=?", (source_key(cid, rid),)).fetchone()
        return json.loads(row[0]) if row else None

    def _failed_reading_anchor(self, cid, rid, papers, topic):
        from core.workspace.paper_reading import PaperReadingTargetError, paper_selection, reading_query

        with self._connection() as conn:
            prior = self._request(conn, cid, rid)
            if not prior or self._effective(prior)["status"] not in ("failed", "interrupted"):
                return None
            saved = conn.execute("SELECT value FROM sra_chat_meta WHERE key=?",
                                 (f"reading-request:{cid}:{rid}",)).fetchone()
        payload = json.loads(prior["payload"])
        selected = payload.get("selected_paper_ids", [])
        if saved:
            return json.loads(saved[0]), selected
        # Older requests can still be resolved if they asked a complete method question.
        question = payload["content"]
        purpose = payload.get("reading_purpose", "orientation")
        if payload.get("plan_request") or payload.get("passage") or reading_query(question, purpose)[0] != "method":
            return None
        try:
            targets, single, reading = paper_selection(papers, selected, topic, question, purpose)
        except PaperReadingTargetError:
            targets, single, reading = [], False, True
        if reading:
            pid = targets[0] if len(targets) == 1 and (single or len(selected) == 1) else None
            return {"paper_id": pid, "question": question[:360]}, selected
        return None

    def cancel_passage(self, cid, rid):
        self.get(cid)
        with self._connection(True) as conn:
            row = self._request(conn, cid, rid)
            if not row or not json.loads(row["payload"]).get("passage"):
                raise ConversationNotFound("选段请求不存在或不属于当前对话")
            conn.execute(
                "UPDATE sra_chat_requests SET status='cancelled',error=?,error_code=409,updated_at=? "
                "WHERE conversation_id=? AND request_id=? AND project_id=? AND status='processing'",
                ("已取消本次选段交付。已经发出的调用仍可能产生费用。", now(), cid, rid, self.project_id),
            )
        return self.request_view(cid, rid)

    def messages(self, cid, limit=30, after=0, *, before=None, tail=False):
        self._page(limit, 0)
        if after < 0:
            raise WorkspaceError("消息游标不能小于零")
        if (
            (before is not None and before < 1)
            or (after and (tail or before is not None))
            or (tail and before is not None)
        ):
            raise WorkspaceError("请只使用一种消息分页方向")
        backwards = tail or before is not None
        with self._connection() as conn:
            self._row(conn, cid)
            if not self._has_tables(conn):
                history = self._workspace(conn).messages
                rows = [
                    {**m.model_dump(mode="json"), "conversation_id": cid, "seq": i + 1}
                    for i, m in enumerate(history)
                    if i + 1 > after and (before is None or i + 1 < before)
                ]
                rows = rows[-limit - 1 :] if backwards else rows[: limit + 1]
            else:
                rows = []
                sql = "SELECT * FROM sra_chat_messages WHERE conversation_id=? AND project_id=?"
                args = [cid, self.project_id]
                if before is not None:
                    sql += " AND seq<?"
                    args.append(before)
                elif not tail:
                    sql += " AND seq>?"
                    args.append(after)
                sql += " ORDER BY seq " + ("DESC" if backwards else "ASC") + " LIMIT ?"
                args.append(limit + 1)
                for row in conn.execute(sql, args):
                    data = {**json.loads(row["data"]), "seq": row["seq"]}
                    req = (
                        self._request(conn, cid, row["request_id"])
                        if row["request_id"]
                        else None
                    )
                    if req:
                        status = self._effective(req)
                        data.update(
                            request_status=status["status"], error=status["error"]
                        )
                    rows.append(data)
                if backwards:
                    rows.reverse()
        if backwards:
            more = len(rows) > limit
            rows = rows[-limit:]
            return {
                "messages": self._guidance_view(cid, rows),
                "next_after": None,
                "next_before": rows[0]["seq"] if more else None,
            }
        return {
            "messages": self._guidance_view(cid, rows[:limit]),
            "next_after": rows[limit - 1]["seq"] if len(rows) > limit else None,
            "next_before": None,
        }

    def _recent(self, cid):
        with self._connection() as conn:
            if self._has_tables(conn):
                has_requests=conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_chat_requests'").fetchone()
                revision='r.based_on_revision' if has_requests else 'NULL'
                join=' LEFT JOIN sra_chat_requests r ON r.conversation_id=m.conversation_id AND r.request_id=m.request_id AND r.project_id=m.project_id' if has_requests else ''
                rows = conn.execute(
                    f"SELECT m.data,{revision} AS context_revision FROM sra_chat_messages m{join} WHERE m.conversation_id=? AND m.project_id=? ORDER BY m.seq DESC LIMIT 8",
                    (cid, self.project_id),
                ).fetchall()
                from core.workspace.conversation_moves import history
                moves=history(conn,cid);origins=moves[-1]['message_origins'] if moves else {}
                return [{**json.loads(r['data']),'context_revision':r['context_revision'],**({'historical_project_context':True,'history_project_id':origins[json.loads(r['data'])['message_id']]} if json.loads(r['data'])['message_id'] in origins else {})} for r in reversed(rows)]
            return (
                [m.model_dump(mode="json") for m in self._workspace(conn).messages[-8:]]
                if cid == self.legacy_id
                else []
            )

    def suggestions(self, cid, *, limit=None, offset=0, request_id=None, recent=False):
        self.get(cid)
        basis = self.material_state()
        with self._connection() as conn:
            if not self._has_tables(conn):
                data = [
                    {
                        **s.model_dump(mode="json"),
                        "conversation_id": cid,
                        "basis_changed": s.material_fingerprint != basis["fingerprint"],
                    }
                    for s in self._workspace(conn).suggestions
                ]
                if recent:
                    data.reverse()
                return (
                    []
                    if request_id is not None
                    else data[offset : offset + limit if limit is not None else None]
                )
            sql = "SELECT * FROM sra_chat_suggestions WHERE conversation_id=? AND project_id=?"
            args = [cid, self.project_id]
            if request_id is not None:
                sql += " AND request_id=?"
                args.append(request_id)
            sql += " ORDER BY rowid" + (" DESC" if recent else "")
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                args.extend([limit, offset])
            rows = conn.execute(sql, args).fetchall()
        return [
            {
                **json.loads(r["data"]),
                "conversation_id": cid,
                "material_fingerprint": r["material_fingerprint"],
                "basis_changed": r["material_fingerprint"] != basis["fingerprint"],
            }
            for r in rows
        ]

    def project_legacy(self, w):
        with self._connection() as conn:
            if not self._has_tables(conn):
                dependency = (
                    self.material_state()["fingerprint"] if w.suggestions else None
                )
                for suggestion in w.suggestions:
                    suggestion.conversation_id = self.legacy_id
                    suggestion.basis_changed = (
                        suggestion.material_fingerprint != dependency
                    )
                return
            row = conn.execute(
                "SELECT * FROM sra_conversations WHERE conversation_id=? AND project_id=?",
                (self.legacy_id, self.project_id),
            ).fetchone()
            if not row:
                w.messages = []
                w.suggestions = []
                return
            # Old clients intentionally receive only the default/history conversation.
            messages = []
            for r in conn.execute(
                "SELECT data,request_id FROM sra_chat_messages WHERE conversation_id=? AND project_id=? ORDER BY seq",
                (self.legacy_id, self.project_id),
            ):
                data = json.loads(r["data"])
                req = (
                    self._request(conn, self.legacy_id, r["request_id"])
                    if r["request_id"]
                    else None
                )
                if req:
                    state = self._effective(req)
                    data.update(request_status=state["status"], error=state["error"])
                messages.append(Message.model_validate(data))
            w.messages = messages
            w.chat_revision = row["chat_revision"]
            proposals = list(
                conn.execute(
                    "SELECT data,material_fingerprint FROM sra_chat_suggestions WHERE conversation_id=? AND project_id=? ORDER BY rowid",
                    (self.legacy_id, self.project_id),
                )
            )
        # Both legacy and conversation views expose the same current dependency
        # status, not the stale flag captured when the response was first saved.
        dependency = self.material_state()["fingerprint"] if proposals else None
        w.suggestions = [
            Suggestion.model_validate(
                {
                    **json.loads(r["data"]),
                    "conversation_id": self.legacy_id,
                    "material_fingerprint": r["material_fingerprint"],
                    "basis_changed": r["material_fingerprint"] != dependency,
                }
            )
            for r in proposals
        ]

    def request_view(self, cid, rid):
        self.get(cid)
        with self._connection() as conn:
            row = self._request(conn, cid, rid)
            if row is None:
                raise ConversationNotFound("消息请求不存在或不属于当前对话")
            result = self._effective(row)
            failure = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_chat_failures'").fetchone():
                failure = conn.execute(
                    "SELECT reason,retry_at FROM sra_chat_failures WHERE conversation_id=? AND request_id=?",
                    (cid, rid),
                ).fetchone()
            recovery_id = self._recovery_id(cid, rid)
            recovery_row = self._request(conn, cid, recovery_id)
            payload = json.loads(row["payload"])
            result = {
                k: v
                for k, v in result.items()
                if k not in ("payload", "payload_hash", "owner_token", "owner_process")
            }
            result["recovery_of"] = payload.get("recovery_of")
            result["recovery_request_id"] = recovery_id if recovery_row else None
            if payload.get("passage"):
                result["passage"] = self._passage_record(conn, cid, rid)
            result["messages"] = [
                {
                    **json.loads(r["data"]),
                    "seq": r["seq"],
                    "request_status": result["status"],
                    "error": result["error"],
                }
                for r in conn.execute(
                    "SELECT data,seq FROM sra_chat_messages WHERE conversation_id=? AND request_id=? AND project_id=? ORDER BY seq",
                    (cid, rid, self.project_id),
                )
            ]
        result["suggestions"] = self.suggestions(cid, request_id=rid)
        result["basis_changed"] = (
            row["material_fingerprint"] != self.material_state()["fingerprint"]
        )
        result["messages"] = self._guidance_view(cid, result["messages"])
        from core.invocation_audit import read_audit, budget_state
        result["call_audit"] = read_audit(self.db_path,self.project_id,operation_id="chat:"+cid+":"+rid)
        budget = budget_state(self.db_path, "chat:"+cid+":"+rid)
        used, remaining = budget["used"], budget["remaining"]
        reason = failure["reason"] if failure else ("interrupted" if result["status"] == "interrupted" else "unknown")
        cooldown = max(0, round(failure["retry_at"] - time.time(), 1)) if failure and failure["retry_at"] else 0
        failure_view = {
            "reason": reason,
            "can_retry": result["status"] in ("failed", "interrupted") and remaining > 0 and cooldown == 0
                and reason in ("connection_error", "timeout", "interrupted", "generation_failed", "unknown", "rate_limited"),
            "retry_after_seconds": cooldown or None,
            "model_attempts_used": used,
            "model_attempts_limit": budget["limit"],
            "model_attempts_remaining": remaining,
            "can_restart": not payload.get("passage") and result["status"] == "failed" and reason == "budget_exhausted"
                and not payload.get("recovery_of") and not recovery_row
                and budget["parent_remaining"] != 0 and not result["basis_changed"],
        }
        result["failure"] = failure_view if result["status"] in ("failed", "interrupted") else None
        from core.workspace.conversation_moves import history
        with self._connection() as conn:moves=history(conn,cid)
        origin=(moves[-1]['request_origins'].get(rid) if moves else None)
        if origin:
            result['history_project_id']=origin
            if result['failure']:
                result['failure'].update(can_retry=False,can_restart=False,recovery_blocked_reason='moved_history',notice='这条历史请求发生在移动前，保留原记录和额度；不能在新项目直接重试。请核对目标资料后发送新的讨论。')
        return result

    def _recovery_id(self, cid, rid):
        return "recovery-" + uuid5(NAMESPACE_URL, f"sra:recovery:{self.project_id}:{cid}:{rid}").hex

    def _guidance_view(self, cid, messages):
        """Live eligibility is a read-only projection, never a history rewrite."""
        from core.workspace.conversation_moves import history
        with self._connection() as conn:moves=history(conn,cid)
        historical_messages=moves[-1]['message_origins'] if moves else {}
        for m in messages:
            origin=historical_messages.get(m.get('message_id'))
            if origin:
                name=next((h['source_name'] for h in moves if h['source_project_id']==origin),origin)
                m.update(historical_project_context=True,history_project_id=origin,history_project_name=name)
        with_gaps = [m for m in messages if m.get("research_gaps")]
        with_reading = [m for m in messages if m.get("reading_materials") or m.get("reading_citations")]
        has_actions = any(m.get("actions") for m in messages)
        if not with_gaps and not with_reading and not has_actions:
            return messages
        basis = self.material_state()
        for m in with_gaps:
            saved_fingerprint = m.pop("research_gap_fingerprint", None)
            m["research_gap_basis_changed"] = bool(
                m.get("historical_project_context") or saved_fingerprint != basis["fingerprint"]
            )
        for m in with_reading:
            saved_fingerprint = m.pop("reading_fingerprint", None)
            m["reading_basis_changed"] = bool(
                m.get("historical_project_context") or saved_fingerprint != basis["fingerprint"]
            )
        if not has_actions:
            return messages
        proposals = {s["suggestion_id"]: s for s in self.suggestions(cid)}
        records = {i.item_id: i for i in basis["workspace"].items}
        papers = {p.paper_id for p in self.shared.store.list_papers()}
        workflow = self.shared.workflow(basis["workspace"])
        for m in messages:
            projected = []
            for action in m.get("actions", []):
                reason = ""
                kind = action["type"]
                if kind == "adopt_suggestion":
                    s = proposals.get(action.get("suggestion_id"))
                    if not s or s["kind"] not in ("question", "plan"):
                        reason = "草案不存在或不属于当前会话的可采纳方案／问题"
                    elif s.get("applied_item_id"):
                        reason = "已采纳，可在研究材料中查看"
                    elif not s.get("material_fingerprint") or s.get("basis_changed"):
                        reason = "草案依据已变化或缺少快照，请重新讨论或复核"
                    elif any(
                        r not in records
                        or records[r].needs_review
                        or records[r].status != "active"
                        for r in s["related_ids"]
                    ):
                        reason = "依赖记录需要复核"
                elif kind == "open_plan":
                    plan = workflow["flow"].get("plan")
                    if not plan or plan["item_id"] != action.get("item_id"):
                        reason = "当前正式方案已变化，请刷新材料"
                elif (
                    kind == "view_references"
                    and not set(action.get("paper_ids", [])) <= papers
                ):
                    reason = "引用资料已变化，请刷新材料"
                if m.get('message_id') in historical_messages:
                    reason='历史操作来自移动前的项目；请基于目标项目资料重新明确发起。'
                projected.append(
                    {**action, "enabled": not bool(reason), "disabled_reason": reason}
                )
            m["actions"] = projected
        return messages

    def perform_action(self, cid, message_id, action_id, expected_revision=None):
        """Resolve only a persisted, owned card; adoption reuses atomic apply."""
        self.get(cid)
        with self._connection() as conn:
            row = (
                conn.execute(
                    "SELECT data FROM sra_chat_messages WHERE project_id=? AND conversation_id=? AND message_id=?",
                    (self.project_id, cid, message_id),
                ).fetchone()
                if self._has_tables(conn)
                else None
            )
        if not row:
            raise ConversationNotFound("操作所属消息不存在或不属于当前会话")
        message = self._guidance_view(cid, [json.loads(row[0])])[0]
        action = next(
            (a for a in message.get("actions", []) if a["action_id"] == action_id), None
        )
        if not action:
            raise ConversationNotFound("操作卡片不存在或不属于当前消息")
        if action["type"] == "adopt_suggestion":
            # apply checks ownership, source fingerprints, boundary and repeats
            # atomically; even a disabled old card cannot bypass its checks.
            return {
                "type": action["type"],
                **self.apply(cid, action["suggestion_id"], expected_revision),
            }
        if not action["enabled"]:
            raise RevisionConflict(action["disabled_reason"])
        if action["type"] == "start_search":
            # 卡片启动走检索任务接口（服务端绑定卡片参数），此处仅确认存在。
            return {
                "type": "start_search",
                "page": None,
                "card_message_id": message_id,
                "card_action_id": action_id,
            }
        pages = {
            "view_references": "papers",
            "open_plan": "experiments",
            "open_settings": None,
        }
        return {
            "type": action["type"],
            "page": pages[action["type"]],
            "item_id": action.get("item_id"),
            "paper_ids": action.get("paper_ids", []),
        }

    def guidance_actions(self, cid, message_ids):
        self.get(cid)
        if not message_ids or len(message_ids) > 100:
            raise WorkspaceError("请提供一至一百个消息编号")
        with self._connection() as conn:
            rows = (
                list(
                    conn.execute(
                        "SELECT data FROM sra_chat_messages WHERE project_id=? AND conversation_id=? AND message_id IN ("
                        + ",".join("?" for _ in message_ids)
                        + ")",
                        (self.project_id, cid, *message_ids),
                    )
                )
                if self._has_tables(conn)
                else []
            )
        if len(rows) != len(set(message_ids)):
            raise ConversationNotFound("消息不存在或不属于当前项目会话")
        views = self._guidance_view(cid, [json.loads(r[0]) for r in rows])
        return {
            "actions": {m["message_id"]: m.get("actions", []) for m in views},
            "research_gap_basis_changed": {
                m["message_id"]: m["research_gap_basis_changed"] for m in views
                if "research_gap_basis_changed" in m
            },
            "reading_basis_changed": {
                m["message_id"]:m["reading_basis_changed"] for m in views if "reading_basis_changed" in m
            },
        }

    def _validate_draft(self, draft, context, workspace):
        allowed = {p["paper_id"] for p in context["papers"]}
        if not set(draft.cited_paper_ids) <= allowed:
            raise WorkspaceError("指导回复引用了当前上下文中不存在的论文")
        records = {i.item_id: i for i in workspace.items}
        for proposal in draft.suggestions:
            if proposal.kind == "plan":
                if not context.get("plan_generation", {}).get("allowed"):
                    raise WorkspaceError("方案仅在用户明确请求且问题与类型已确认后生成")
                if not proposal.plan:
                    raise WorkspaceError(
                        "新方案须使用对应研究方法的结构；历史方案仍可查看"
                    )
            if proposal.supersedes_id:
                target = records.get(proposal.supersedes_id)
                if (
                    proposal.kind != "plan"
                    or not target
                    or target.kind != "plan"
                    or target.status != "active"
                ):
                    raise WorkspaceError("修订目标须为本项目当前有效方案")
            if proposal.kind in POST_PLAN_KINDS:
                raise WorkspaceError(NOTICE)
            if not set(proposal.paper_ids) <= allowed or any(
                ref not in records for ref in proposal.related_ids
            ):
                raise WorkspaceError("建议引用的论文或记录不属于当前项目上下文")
            if (
                proposal.kind == "plan"
                and sum(records[r].kind == "question" for r in proposal.related_ids)
                != 1
            ):
                raise WorkspaceError("方案草案须关联一个已经确认的研究问题")
            if any(
                records[r].status != "active" or records[r].needs_review
                for r in proposal.related_ids
            ):
                raise WorkspaceError("建议的依赖记录需要重新核对")

    def _discussion_context(self, cid, request, topic, area, meta, basis, legacy, search_available):
        from core.workspace.guide import READING_CANDIDATE, build_guide_context
        from core.workspace.guidance_policy import turn_preferences
        from core.workspace.paper_reading import PaperReadingTargetError, _named_papers

        recent_messages=self._recent(cid)
        reading_guide = request.reading_guide.model_dump() if request.reading_guide else None
        latest = next((m for m in reversed(recent_messages)
                       if m["role"] == "assistant" and not m.get("historical_project_context")), {})
        prefs = turn_preferences(request.content)
        continuation = prefs["paused"] or (
            len(request.content) <= 72 and (
                re.match(r"^(?:还是|我|有点)?(?:没看懂|没听懂|不太懂|再简单一点)", request.content)
                or re.fullmatch(r"[^。？！?，,《》]{1,42}(?:是什么意思|是什么术语)[？?。]?", request.content)
                or re.match(r"^(?:那|所以)?(?:为什么|为何|然后呢|接下来呢|重排呢|再排序呢|第二步呢|能举个例子吗)(?:[。！？?，,\s]|$)", request.content)
            )
        )
        followup = None
        sources = latest.get("reading_materials") or []
        prior_guide = latest.get("reading_guide")
        can_continue = (not reading_guide and continuation and not request.plan_request and not request.selected_item_id
                and not re.search(r"《|\b10\.\d{4,9}/|只看|只读|换(?:一篇|成|到|读|看)|改(?:读|看)|\bswitch\b", request.content, re.I)
                and not _named_papers(self.shared.store.list_papers(), request.content))
        if can_continue and READING_CANDIDATE:
            last_turn = next((m for m in reversed(recent_messages)
                              if m["role"] in ("user", "assistant") and not m.get("historical_project_context")
                              and m.get("request_id") != request.request_id), {})
            previous_id = last_turn.get("request_id")
            if last_turn.get("role") == "user" and previous_id:
                papers = self.shared.store.list_papers()
                if recovered := self._failed_reading_anchor(cid, previous_id, papers, topic):
                    anchor, prior_selection = recovered
                    can_continue = False
                    pid = anchor["paper_id"]
                    selection = set(request.selected_paper_ids)
                    if selection and selection != set(prior_selection) and selection != {pid}:
                        # A changed selection is a new scope. An unchanged stale checkbox
                        # cannot overrule the previous user's explicit title or DOI.
                        if len(selection) == 1:
                            followup = {"paper_id": next(iter(selection)), "question": request.content[:360]}
                    elif pid is None or not any(p.paper_id == pid for p in papers):
                        raise PaperReadingTargetError("上一次阅读的目标已不可用或无法唯一确定。请重新勾选一篇论文，或写明完整题名或 DOI。")
                    else:
                        followup = anchor
        if can_continue and not followup and not latest.get("research_gaps") and len(sources) == 1:
            pid = sources[0]["paper_id"]
            if ((prior_guide or not set(request.selected_paper_ids) - {pid})
                    and any(p.paper_id == pid for p in self.shared.store.list_papers())):
                previous = latest.get("reading_question") or next((
                    m["content"] for m in reversed(recent_messages)
                    if m["role"] == "user" and not m.get("historical_project_context")
                    and m.get("request_id") == latest.get("request_id")
                ), "")
                if prior_guide:
                    reading_guide = dict(prior_guide)
                    previous = previous or GUIDE_GOALS[reading_guide["goal"]][1]
                followup = {"paper_id": pid, "question": previous[:360]}
        history = [
            {
                "role": m["role"],
                "content": m["content"][:1800],
                "area": m["area"],
                "suggested_replies": m.get("suggested_replies", []),
                **({"message_id": m["message_id"], "source_revision": m.get("context_revision"),
                    "full_user_message": len(m["content"]) <= 1800} if m["role"] == "user" else {}),
                "background_outdated": bool(basis['workspace'].brief_revision and (m.get('context_revision') is None or m['context_revision'] < basis['workspace'].brief_revision)),
                **({"prior_gap_discussion": {
                    "points": [{"kind": g["kind"], "summary": g["summary"]} for g in m["research_gaps"]],
                    "basis_changed": m.get("research_gap_fingerprint") != basis["fingerprint"],
                    "notice": "仅用于解释上次讨论，不能替代本次提供的原文或当成已确认结论。",
                }} if m.get("research_gaps") else {}),
                **({"report_reference_map": {
                    "task_id":m["research_task_id"], "version":m.get("report_version"),
                    "citations":[{k:p.get(k) for k in ("number","paper_id","title","reading_scope")} for p in m.get("report_citations",[])],
                    "notice":"仅解释这条历史消息的编号；不替代当前项目可用材料或其引用校验。",
                }} if m.get("research_task_id") else {}),
            }
            for m in recent_messages
            if not m.get('historical_project_context') and m.get("request_id") not in (request.request_id, request.recovery_of)
        ]
        context = build_guide_context(
            self.shared,
            topic,
            area,
            request.selected_item_id,
            request.selected_paper_ids,
            legacy_notes=self.shared.store.get_kv("user.notes") or [],
            history=history,
            text=request.content,
            reading_purpose=request.reading_purpose,
            reading_guide=reading_guide,
            reading_followup=followup,
        )
        context["conversation"] = {
            k: meta[k]
            for k in (
                "conversation_id",
                "discussion_goal",
                "guidance_mode",
                "entry_stage",
                "case_id",
            )
        }
        context["evidence_scope"] = {
            "project_id": self.project_id, "conversation_id": cid,
            "request_id": request.request_id, "revision": basis["revision"],
            "material": basis["fingerprint"],
        }
        context["conversation"]["discussion_step"] = meta.get("discussion_step")
        if meta.get('movement_history'):
            context['conversation']['movement_history']=meta['movement_history']
            context['conversation']['prior_project_history']=[{'role':m['role'],'content':m['content'][:1800],'origin_project_id':m['history_project_id'],'notice':'仅供历史参考，不是目标项目已确认的条件、问题或方案。'} for m in recent_messages if m.get('historical_project_context')]
        context["conversation"][
            "case_available"
        ] = False  # Contract only; no case content in step 2.
        from core.workspace.guidance_policy import CAPABILITIES, prepare

        context["capabilities"] = CAPABILITIES
        context["current_plan"] = self.shared.workflow(basis["workspace"])["flow"].get(
            "plan"
        )
        if context["current_plan"]:
            context["current_plan"] = {
                k: context["current_plan"][k]
                for k in ("item_id", "kind", "title", "needs_review")
            }
        try:
            own_suggestions = self.suggestions(cid)
        except ConversationNotFound:
            own_suggestions = []  # First explicit old-client chat has no default yet.
        eligible = [
            s
            for s in own_suggestions
            if not s.get("applied_item_id")
            and s.get("material_fingerprint")
            and not s.get("basis_changed")
            and s["kind"] in ("question", "plan")
        ]
        context["eligible_plan_count"] = sum(s["kind"] == "plan" for s in eligible)
        visible = eligible[-8:]
        if context["eligible_plan_count"] == 1:
            only_plan = next(s for s in eligible if s["kind"] == "plan")
            if only_plan not in visible:
                visible = [only_plan, *visible[-7:]]
        context["allowed_suggestions"] = [
            {
                k: s[k][:1500] if k == "content" else s[k]
                for k in ("suggestion_id", "kind", "title", "content", "related_ids")
            }
            for s in visible
        ] if not reading_guide else []
        from core.workspace.research_coach import build_research_route, saved_reading_context
        context["research_route"] = {k:v for k,v in build_research_route(context, request.content).items()
                                     if k not in ("steps", "next_step")}  # UI labels aren't model evidence.
        context["saved_reading"] = saved_reading_context(self, context["research_route"]["readable_paper_ids"])
        context["research_search_available"] = search_available and not legacy and not reading_guide
        from core.workspace.plans import (
            requested,
            build_plan_context,
            prepare_plan,
            rule_plan,
        )

        plan_request = None if reading_guide else requested(request.content, request.plan_request)
        if plan_request is not None:
            revises = plan_request.revises_suggestion_id
            source = next((s for s in eligible if s["suggestion_id"] == revises and s["kind"] == "plan"), None)
            draft_revision = None
            if revises:
                if (not source or not source.get("plan")
                        or source["related_ids"] != [plan_request.question_id]
                        or source["plan"]["method"] != plan_request.method
                        or source.get("supersedes_id") != plan_request.supersedes_id):
                    raise WorkspaceError("待修订草案不属于当前对话、问题或材料版本，请重新打开草案。")
                draft_revision = {key: source[key] for key in ("suggestion_id", "title", "plan")}
            context["plan_generation"] = build_plan_context(
                self.shared, basis["workspace"], context, request.content, plan_request, draft_revision
            )
            from core.workspace.paper_reading import MAX_PLAN_PAPERS, bound_papers
            linked = set((context["plan_generation"]["question"] or {}).get("paper_ids",[]))
            context["papers"] = bound_papers(
                [p for p in context["papers"] if not linked or p["paper_id"] in linked][:MAX_PLAN_PAPERS]
            )
        return context

    def send(
        self,
        cid,
        request: ConversationSend,
        topic,
        generate,
        *,
        legacy=False,
        retry=False,
        recovery=False,
        search_available=False,
    ):
        from core.workspace.teaching import teaching_identity, TeachingService
        context_started = time.monotonic()

        if teaching_identity(self):
            if request.passage or request.reading_guide:
                raise WorkspaceError("教学案例暂未开放论文伴读与选段处理。")
            return TeachingService(self).free(cid, request)
        if request.passage and (
            legacy or request.reading_guide or request.plan_request or request.selected_item_id
            or request.selected_paper_ids or request.request_id != request.passage.identity()
            or request.content != "论文选段" or request.recovery_of
        ):
            raise WorkspaceError("选段请求与原文位置或操作身份不一致。")
        if request.reading_guide and (legacy or request.plan_request or request.selected_item_id):
            raise WorkspaceError("伴读只讨论所选论文，请单独提交其他研究操作。")
        meta = (
            self._default(self.shared.load(include_chat=False))
            if legacy
            else self.get(cid)
        )
        # A missing default may be created only by this explicit legacy write.
        if legacy:
            try:
                meta = self.get(cid)
            except ConversationNotFound:
                pass
        body = request.model_dump(exclude={"request_id", "expected_revision", "wait_for_completion"})
        if body.get("plan_request") is None:
            body.pop("plan_request", None)  # Preserve pre-step-6 request hashes.
        for key in ("reading_guide", "passage"):
            if body.get(key) is None:
                body.pop(key, None)
        if body.get("recovery_of") is None:
            body.pop("recovery_of", None)  # Preserve pre-recovery request hashes.
        elif not (recovery or retry):
            raise WorkspaceError("恢复关联只能通过明确的新一轮入口建立")
        signature = fingerprint(body)
        with self._connection() as conn:
            prior = self._request(conn, cid, request.request_id)
        if prior:
            if prior["payload_hash"] != signature:
                raise RevisionConflict("request_id 已用于不同消息内容或引用")
            if not retry or self._effective(prior)["status"] not in (
                "failed",
                "interrupted",
            ):
                return self.request_view(cid, request.request_id)
            if not self.request_view(cid, request.request_id)["failure"]["can_retry"]:
                return self.request_view(cid, request.request_id)
        elif retry:
            raise ConversationNotFound("要重试的原请求不存在")
        if request.recovery_of and recovery and not prior:
            parent = self.request_view(cid, request.recovery_of)
            if not parent["failure"]["can_restart"]:
                raise RevisionConflict("原请求不允许开启新一轮；请核对上层预算与原请求状态")
        from core.workspace.research_coach import STEP_AREAS
        area = request.area or STEP_AREAS.get(meta.get("discussion_step")) or STAGE_AREAS[meta["entry_stage"]]
        if request.passage or request.reading_guide:
            area = "literature"
        if area == "writing":
            raise WorkspaceError(NOTICE)
        basis = self.material_state()
        if (
            request.expected_revision is not None
            and request.expected_revision != basis["revision"]
        ):
            raise RevisionConflict("项目记录已有更新，请刷新后重新发送；输入尚未保存")
        if request.passage:
            with self._connection() as conn:
                saved = self._passage_record(conn, cid, request.request_id) if prior else None
            if prior and not saved:
                raise WorkspaceError("原选段快照无法核对，未发起调用。")
            snapshot = saved["source"] if saved else resolve_passage(self, cid, request.passage)
            context = {"passage": snapshot}
        else:
            context = self._discussion_context(cid, request, topic, area, meta, basis, legacy, search_available)
        # Serialize the short claim transaction before trying the nonblocking OS
        # lock. A racing duplicate then sees the committed request, instead of a
        # transient 'lock held but request not inserted yet' gap.
        with ExitStack() as ownership:
            token = uuid4().hex
            with self._connection(True) as conn:
                self._ensure(conn, basis, default=legacy)
                latest_meta = self._row(conn, cid)
                latest = self._request(conn, cid, request.request_id)
                if latest:
                    if latest["payload_hash"] != signature:
                        raise RevisionConflict("request_id 已用于不同消息内容或引用")
                    if (
                        not retry
                        or latest["status"] == "completed"
                        or prior
                        and latest["attempt"] != prior["attempt"]
                    ):
                        # Return after transaction/lock exit using the normal read path below.
                        duplicate = True
                    else:
                        duplicate = False
                else:
                    duplicate = False
                if not duplicate:
                    acquired = ownership.enter_context(
                        conversation_lock.hold(self.db_path, cid)
                    )
                    if not acquired:
                        raise RevisionConflict(
                            "本对话已有消息正在处理，请等待完成后再发送"
                        )
                    from core.resource_limits import RESOURCES
                    ownership.enter_context(RESOURCES.acquire())
                    if latest_meta["chat_revision"] != meta["chat_revision"]:
                        raise RevisionConflict(
                            "对话已有新消息或设置变化，请读取最新对话后再发送"
                        )
                    current = self.material_state()
                    if current["fingerprint"] != basis["fingerprint"]:
                        raise RevisionConflict("项目材料已变化，请刷新后重试")
                    # Exclusive OS ownership proves these old processing rows are orphaned.
                    conn.execute(
                        "UPDATE sra_chat_requests SET status='interrupted',error=?,error_code=409,updated_at=? WHERE conversation_id=? AND status='processing'",
                        ("原处理进程已中断，可明确重试", now(), cid),
                    )
                    if latest:
                        conn.execute(
                            "UPDATE sra_chat_requests SET status='processing',owner_token=?,owner_process=?,attempt=attempt+1,updated_at=?,error=NULL,error_code=NULL,material_fingerprint=?,based_on_revision=? WHERE conversation_id=? AND request_id=?",
                            (
                                token,
                                PROCESS_IDENTITY,
                                now(),
                                basis["fingerprint"],
                                basis["revision"],
                                cid,
                                request.request_id,
                            ),
                        )
                    else:
                        if request.recovery_of:
                            if request.request_id != self._recovery_id(cid, request.recovery_of):
                                raise RevisionConflict("恢复请求身份不一致")
                            parent_row = self._request(conn, cid, request.recovery_of)
                            if not parent_row or parent_row["status"] != "failed":
                                raise RevisionConflict("原请求状态已改变，未开启新一轮")
                            if parent_row["material_fingerprint"] != current["fingerprint"]:
                                raise RevisionConflict("原请求依据已变化，请先复核材料，不沿旧请求重开")
                        stamp = now()
                        conn.execute(
                            "INSERT INTO sra_chat_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                cid,
                                request.request_id,
                                self.project_id,
                                encode(body),
                                signature,
                                "processing",
                                token,
                                PROCESS_IDENTITY,
                                1,
                                stamp,
                                stamp,
                                None,
                                None,
                                basis["fingerprint"],
                                basis["revision"],
                            ),
                        )
                        if request.passage:
                            conn.execute("INSERT INTO sra_chat_meta(key,value) VALUES (?,?)", (
                                source_key(cid, request.request_id), encode({"source": context["passage"], "result": None}),
                            ))
                        elif not request.recovery_of:
                            self._insert_message(
                                conn,
                                cid,
                                Message(
                                    role="user",
                                    content=request.content,
                                    area=area,
                                    mode="user",
                                ).model_dump(mode="json"),
                                request.request_id,
                            )
                    if not request.passage:
                        selection = context["reading_selection"]
                        if (context["reading_candidate"] and selection["intent"] == "method"
                                and not context.get("plan_generation")):
                            ids = selection["paper_ids"]
                            anchor = {"paper_id": ids[0] if len(ids) == 1 else None,
                                      "question": context["reading_question"]}
                            conn.execute("INSERT OR REPLACE INTO sra_chat_meta(key,value) VALUES (?,?)", (
                                f"reading-request:{cid}:{request.request_id}", encode(anchor),
                            ))
                        self._touch(conn, cid)
            if not duplicate:
                elapsed = time.monotonic()-context_started
                args = (cid,request,topic,generate,token,basis,area,context,elapsed)
                if not request.wait_for_completion and (request.passage or request.reading_guide or context.get("plan_generation",{}).get("allowed")):
                    # Transfer the EXISTING OS lease, never leave a queued gap.
                    # No new queue, database or generation state machine.
                    lease = ownership.pop_all()
                    worker_entered = threading.Event()
                    def work():
                        worker_entered.set()
                        try:
                            self._complete_attempt(*args)
                        finally:
                            lease.close()
                    worker = None
                    try:
                        execution_context = copy_context()
                        worker = threading.Thread(target=execution_context.run,args=(work,),daemon=True)
                        worker.start()
                    except Exception:
                        if worker_entered.is_set() or (worker is not None and getattr(worker,"ident",None) is not None):
                            # A missing start acknowledgement is not proof that
                            # the worker stopped. Its finally owns this lease.
                            raise ConversationStorageError("后台启动响应未确认，原请求可能仍在处理；请查询原请求状态，不要重新发起") from None
                        try:
                            self._fail(cid,request.request_id,token,"后台生成未能启动，原消息保留，请明确重试",503,"WorkerStartError")
                        except Exception as storage_error:
                            raise ConversationStorageError("后台工作未启动，失败状态未能保存；请保留原请求编号，待存储恢复后查询并明确重试") from storage_error
                        finally:
                            lease.close()
                else:
                    self._complete_attempt(*args)
        return self.request_view(cid, request.request_id)

    def _complete_attempt(self,cid,request,topic,generate,token,basis,area,context,context_elapsed):
        from core.invocation_audit import AuditScope, inherited_policy
        from core.workspace.plans import prepare_plan, rule_plan, ConditionEvidenceError, UserStatementEvidenceError, _claim_literal
        from core.workspace.guidance_policy import prepare
        from core.workspace.paper_reading import apply_delivered_evidence, reading_citations
        scope = AuditScope(self.db_path,self.project_id,"chat:"+cid+":"+request.request_id,
                           conversation_id=cid,request_id=request.request_id)
        phase_kind = ("passage" if request.passage else "plan"
                      if context.get("plan_generation",{}).get("allowed") else "guidance")
        rejected_actions=[]
        try:
            if request.recovery_of or request.passage:
                policy = inherited_policy(self.db_path, "chat:"+cid+":"+(request.recovery_of or request.request_id))
                if policy:
                    if policy.get("unavailable"):
                        raise WorkspaceError("原请求的上层调用限制无法核实，未开启模型调用")
                    scope.group_id = policy["group_id"]
                    for key in ("limits", "pricing", "cost_limit", "stop_on_model_failure", "expected_model"):
                        setattr(scope, key, policy[key])
            with scope.activate():
                ticket = scope.begin("phase",phase_kind,"context",elapsed_before_start=context_elapsed)
                scope.finish(ticket)
                if request.passage:
                    with self._connection() as conn:
                        if self._request(conn, cid, request.request_id)["status"] != "processing":
                            return
                    with scope.span("phase", phase_kind, "generation"):
                        output, _ = generate(area, request.content, context, request.reading_purpose)
                    with scope.span("phase", phase_kind, "validation"):
                        result = passage_answer(output, request.passage.operation)
                    self._finish_passage(cid, request.request_id, token, result)
                    return
                with scope.span("phase",phase_kind,"generation"):
                    from core.workspace.guidance_policy import is_paused
                    if context.get("reading_guide") and is_paused(request.content):
                        draft,mode = GuideDraft(reply="好，先暂停伴读。下次可以直接问想弄清的部分。"),"rules"
                    elif context.get("plan_generation",{}).get("allowed") is False:
                        draft,mode = rule_plan(context),"rules"
                    else:
                        draft,mode = generate(area,request.content,context,request.reading_purpose)
                        apply_delivered_evidence(context)
                with scope.span("phase",phase_kind,"validation"):
                    draft = GuideDraft.model_validate(draft)
                    if context.get("reading_guide") or context.get("focused_reading"):
                        draft = draft.model_copy(update={
                            "actions": [], "suggestions": [], "research_gaps": [],
                            "suggested_replies": [], "questions": [],
                        })
                    # Check citations, record ownership and ALL model actions
                    # before plan rendering or display normalization can replace them.
                    self._validate_draft(draft,context,basis["workspace"])
                    draft = prepare(draft,request.content,context,rejected_actions=rejected_actions)
                    if context.get("plan_generation",{}).get("allowed"):
                        try:
                            draft = prepare_plan(draft,context,request.content)
                        except (ConditionEvidenceError, UserStatementEvidenceError) as exc:
                            # One bounded repair, only for evidence formatting, not
                            # a fabricated quote/unknown fact. All checks rerun.
                            statement_error = isinstance(exc, UserStatementEvidenceError)
                            if (mode != "model" or os.environ.get("SRA_GUIDED_SINGLE_MODEL_ATTEMPT") == "1"
                                    or not statement_error and exc.reason not in ("value_not_literal_excerpt", "condition_qualifier_omitted")):
                                raise
                            field = "user_statement" if statement_error else exc.field
                            reason = "verified_quote_attribution" if statement_error else exc.reason
                            with scope.span("phase", "plan", "condition_repair:" + field):
                                repair_context = {**context, "condition_repair": {"field": field, "reason": reason}}
                                if statement_error:
                                    repair_context["condition_repair"].update(quote=exc.quote,instruction="保留这条已提供的用户原话为独立user事实，text逐字使用quote。不得改为proposal或删除来跳过检查；额外建议另列，不得改变关键约束。")
                                draft, mode = generate(area,request.content,repair_context,request.reading_purpose)
                                apply_delivered_evidence(context,repair_context)
                                draft = GuideDraft.model_validate(draft)
                                if statement_error and not any(
                                    s.basis == "user" and _claim_literal(s.text) == _claim_literal(exc.quote) and s.quote == exc.quote
                                    for proposal in draft.suggestions if proposal.plan
                                    for section in proposal.plan.sections for s in section.statements
                                ):
                                    raise WorkspaceError("方案修复未保留原用户事实，未保存草案；请核对用户原话后重新提交")
                                self._validate_draft(draft,context,basis["workspace"])
                                draft = prepare(draft,request.content,context,rejected_actions=rejected_actions)
                                draft = prepare_plan(draft,context,request.content)
                    draft = prepare(draft,request.content,context,rejected_actions=rejected_actions)
                    if context.get("reading_guide") or context.get("focused_reading"):
                        draft = draft.model_copy(update={"suggested_replies": [], "questions": [], "actions": []})
                    self._validate_draft(draft,context,basis["workspace"])
                    from core.workspace.research_coach import validated_gap_snapshots
                    gaps = validated_gap_snapshots(draft, context, request.content)
                    quotes = reading_citations(draft,context)
                    if rejected_actions:
                        for kind in rejected_actions:
                            ticket=scope.begin("phase","guidance","action_rejected:"+kind)
                            scope.finish(ticket,outcome="rejected")
                        context.setdefault("delivery_warnings",[]).append("回答已保存，但部分操作未通过草案归属或可用性校验，未提供这些按钮，也未执行采纳或保存。可在研究材料中核对当前草案。")
                with scope.span("phase",phase_kind,"saving"):
                    self._finish(cid,request.request_id,token,draft,mode,basis,area,context,gaps=gaps,quotes=quotes)
        except Exception as exc:
            with scope.activate():
                diagnostic = ("condition:" + exc.field + ":" + exc.reason) if isinstance(exc,ConditionEvidenceError) else type(exc).__name__
                ticket = scope.begin("phase",phase_kind,"final_failure:" + diagnostic)
                scope.finish(ticket,outcome="failed")
            code = 503 if isinstance(exc,(ConversationStorageError,sqlite3.Error)) else 422 if isinstance(exc,WorkspaceError) else 502
            reason = str(exc) if code in (422,503) else "模型未能返回完整有效方案或指导，原消息已保留；请查看调用记录，再明确重试"
            from core.llm.base import LLMTruncatedError
            if isinstance(exc,LLMTruncatedError):
                reason = "模型回复达到本次输出上限，未保存不完整内容。原消息和已有材料仍保留。请缩小问题范围后明确发送，系统不会自动补发。"
            classified, retry_after = failure_reason(exc)
            if classified == "budget_exhausted":
                reason = "本次请求的模型网络调用额度已用完。原请求不能继续重试；如无上层预算限制，可明确开启一次新的处理轮次（会再次调用模型并可能产生费用）。原消息和已有材料仍保留。"
            elif classified == "authentication":
                reason = "模型认证失败。请核对服务凭据或权限后再发起请求；当前请求不会自动重试。"
            elif classified == "timeout":
                reason = "等待模型响应超时，未取得可保存的回答，上游完成和计费状态未知。原请求仍保留，系统未自动补发；明确重试会再次调用模型，可能产生费用。"
            elif classified == "rate_limited":
                reason = "模型限流，当前不能直接重试。请等待冷却结束后核对原请求；原消息已保留。"
            elif classified == "cost_exhausted":
                reason = "本次体验的共享费用额度不足以预留下一次调用，已停止发送；可能尚未花满授权金额。原消息和已有材料仍保留，切换项目或重试不会补充额度。"
            elif classified == "experience_expired":
                reason = "本次真实模型体验授权已到期，未发出新调用。原消息和已有材料仍保留，请联系本地操作者重新确认体验安排。"
            elif classified == "experience_limit":
                reason = "本次体验已达到累计调用次数的保护上限，未发出新调用。原消息和已有材料仍保留，重试或开始新一轮不会补充本次体验额度。"
            if request.passage and classified in ("unknown", "generation_failed", "connection_error"):
                reason = "本次选段未完成，原文已保留。请核对请求状态，再决定是否重试。"
            self._fail(cid,request.request_id,token,reason,code,type(exc).__name__,classified,retry_after)

    def _touch(self, conn, cid):
        conn.execute(
            "UPDATE sra_conversations SET updated_at=?,chat_revision=chat_revision+1 WHERE conversation_id=? AND project_id=?",
            (now(), cid, self.project_id),
        )

    def _finish_passage(self, cid, rid, token, result):
        with self._connection(True) as conn:
            changed = conn.execute(
                "UPDATE sra_chat_requests SET status='completed',updated_at=?,error=NULL,error_code=NULL "
                "WHERE project_id=? AND conversation_id=? AND request_id=? AND owner_token=? AND status='processing'",
                (now(), self.project_id, cid, rid, token),
            ).rowcount
            if changed:
                record = self._passage_record(conn, cid, rid)
                record["result"] = result
                conn.execute("UPDATE sra_chat_meta SET value=? WHERE key=?", (encode(record), source_key(cid, rid)))

    def _finish(self, cid, rid, token, draft, mode, basis, area, context=None, *, gaps=(), quotes=()):
        from core.workspace.schema import GuidanceAction

        context = context or {}
        with self._connection(True) as conn:
            row = self._request(conn, cid, rid)
            if (
                row is None
                or row["owner_token"] != token
                or row["status"] != "processing"
            ):
                raise RevisionConflict("请求执行权已变化，未写入旧回复")
            stale = self.material_state()["fingerprint"] != basis["fingerprint"]
            ids = []
            for proposal in draft.suggestions:
                fields = proposal.model_dump()
                gap = gaps[proposal.gap_link.gap_index] if proposal.gap_link else None
                if gap:
                    fields["paper_ids"] = list(dict.fromkeys(
                        [*proposal.paper_ids, *(source.paper_id for source in gap.sources)]
                    ))
                s = Suggestion(
                    **fields,
                    gap_snapshot=gap,
                    based_on_revision=basis["revision"],
                    material_fingerprint=basis["fingerprint"],
                    basis_changed=stale,
                    conversation_id=cid,
                )
                self._insert_suggestion(
                    conn, cid, s.model_dump(mode="json"), rid, basis["fingerprint"]
                )
                ids.append(s.suggestion_id)
            actions = []
            for a in draft.actions:
                values = {"type": a.type}
                if a.type == "adopt_suggestion":
                    sid = (
                        ids[a.suggestion_index]
                        if a.suggestion_index is not None
                        else a.suggestion_id
                    )
                    proposal = (
                        draft.suggestions[a.suggestion_index]
                        if a.suggestion_index is not None
                        else next(
                            s
                            for s in context["allowed_suggestions"]
                            if s["suggestion_id"] == sid
                        )
                    )
                    title = (
                        proposal.title
                        if hasattr(proposal, "title")
                        else proposal["title"]
                    )
                    values.update(suggestion_id=sid, label="采纳草案：" + title)
                elif a.type == "open_plan":
                    values.update(
                        item_id=context["current_plan"]["item_id"],
                        label="打开当前正式方案",
                    )
                elif a.type == "view_references":
                    values.update(
                        paper_ids=draft.cited_paper_ids, label="查看本条引用资料"
                    )
                else:
                    values["label"] = "打开讨论设置"
                actions.append(GuidanceAction(**values))
            route=context.get("research_route", {})
            if (not context.get("focused_reading") and context.get("research_search_available")
                    and route.get("offer_search") and not stale and len(actions)<4):
                actions.append(GuidanceAction(type="start_search",label="补充代表性文献（先确认范围）",topic=route["search_topic"][:200]))
            replies=draft.suggested_replies[:max(0,4-len(actions))]
            reading_materials = context.get("papers", []) if (
                quotes or mode=="model" and context.get("reading_selection",{}).get("active")
                and not context.get("plan_generation")
            ) else []
            message = Message(
                role="assistant",
                content=draft.reply,
                area=area,
                mode=mode,
                cited_paper_ids=list(dict.fromkeys(
                    [*draft.cited_paper_ids, *(source.paper_id for gap in gaps for source in gap.sources),
                     *(source.paper_id for source in quotes)]
                )),
                research_gaps=list(gaps),
                research_gap_fingerprint=basis["fingerprint"] if gaps else None,
                reading_materials=reading_materials,
                reading_citations=list(quotes),
                reading_fingerprint=basis["fingerprint"] if reading_materials else None,
                reading_guide={k: context["reading_guide"][k] for k in ("paper_id", "goal")}
                    if context.get("reading_guide") else None,
                reading_question=context.get("reading_question") if len(reading_materials) == 1 else None,
                questions=draft.questions,
                suggestion_ids=ids,
                suggested_replies=replies,
                actions=actions,
                delivery_warnings=context.get("delivery_warnings", []),
            )
            self._insert_message(conn, cid, message.model_dump(mode="json"), rid)
            conn.execute(
                "UPDATE sra_chat_requests SET status='completed',updated_at=?,error=NULL,error_code=NULL WHERE conversation_id=? AND request_id=? AND owner_token=?",
                (now(), cid, rid, token),
            )
            self._touch(conn, cid)

    def _fail(self, cid, rid, token, message, code, error_type, reason=None, retry_after=None):
        with self._connection(True) as conn:
            conn.execute(DDL[-1])
            changed = conn.execute(
                "UPDATE sra_chat_requests SET status='failed',error=?,error_code=?,updated_at=? WHERE conversation_id=? AND request_id=? AND owner_token=? AND status='processing'",
                (message, code, now(), cid, rid, token),
            ).rowcount
            if changed:
                conn.execute(
                    "INSERT OR REPLACE INTO sra_chat_failures VALUES (?,?,?,?)",
                    (cid, rid, reason or error_type or "generation_failed",
                     time.time() + retry_after if retry_after else None),
                )
                if not json.loads(self._request(conn, cid, rid)["payload"]).get("passage"):
                    self._touch(conn, cid)

    def retry(self, cid, rid, topic, generate, expected_revision=None, wait_for_completion=True, *, search_available=False):
        self.get(cid)
        with self._connection() as conn:
            prior = self._request(conn, cid, rid)
        if not prior:
            raise ConversationNotFound("消息请求不存在或不属于当前对话")
        request = ConversationSend(
            request_id=rid,
            expected_revision=expected_revision,
            wait_for_completion=wait_for_completion,
            **json.loads(prior["payload"]),
        )
        return self.send(cid, request, topic, generate, retry=True, search_available=search_available)

    def restart(self, cid, rid, topic, generate, *, acknowledge_model_cost,
                wait_for_completion=True, search_available=False):
        if acknowledge_model_cost is not True:
            raise WorkspaceError("须先确认新一轮会再次调用模型且可能产生费用")
        self.get(cid)
        new_id = self._recovery_id(cid, rid)
        with self._connection() as conn:
            existing = self._request(conn, cid, new_id)
            parent = self._request(conn, cid, rid)
        if existing:
            return self.request_view(cid, new_id)
        if not parent:
            raise ConversationNotFound("原消息请求不存在或不属于当前对话")
        if not self.request_view(cid, rid)["failure"]["can_restart"]:
            raise RevisionConflict("原请求不能开启新一轮；请核对请求原因及上层总预算")
        payload = json.loads(parent["payload"])
        payload["recovery_of"] = rid
        request = ConversationSend(
            request_id=new_id,
            expected_revision=parent["based_on_revision"],
            wait_for_completion=wait_for_completion,
            **payload,
        )
        return self.send(cid, request, topic, generate, recovery=True,
                         search_available=search_available)

    def apply(self, cid, sid, expected_revision=None, *, _transaction=None):
        # Validate ownership/boundary before any migration or mutation.
        candidate = next(
            (s for s in self.suggestions(cid) if s["suggestion_id"] == sid), None
        )
        if not candidate:
            raise ConversationNotFound("建议不存在或不属于当前会话")
        if candidate["kind"] in POST_PLAN_KINDS:
            raise WorkspaceError(NOTICE)
        basis = self.material_state()
        with (
            nullcontext(_transaction)
            if _transaction is not None
            else self._connection(True)
        ) as conn:
            self._ensure(conn, basis)
            row = conn.execute(
                "SELECT * FROM sra_chat_suggestions WHERE suggestion_id=? AND conversation_id=? AND project_id=?",
                (sid, cid, self.project_id),
            ).fetchone()
            if not row:
                raise ConversationNotFound("建议不属于当前会话")
            suggestion = json.loads(row["data"])
            w = self._workspace(conn)
            if suggestion.get("applied_item_id"):
                item = next(
                    (i for i in w.items if i.item_id == suggestion["applied_item_id"]),
                    None,
                )
                if item is None:
                    raise WorkspaceError("已采纳记录缺失，请核对项目历史")
                return {
                    "item": item.model_dump(mode="json"),
                    "already_applied": True,
                    "revision": w.revision,
                }
            current = self.material_state()
            if not row["material_fingerprint"]:
                raise RevisionConflict(
                    "历史建议缺少生成时的材料依据快照，请在原会话重新核对；草案已保留"
                )
            if row["material_fingerprint"] != current["fingerprint"] or (
                expected_revision is not None and expected_revision != w.revision
            ):
                raise RevisionConflict(
                    "这条建议之后项目材料已有变化，请重新核对；草案已保留"
                )
            mark_changed_sources(w, self.shared.catalog())
            data = ItemInput(
                **{
                    k: suggestion[k]
                    for k in (
                        "kind",
                        "title",
                        "content",
                        "paper_ids",
                        "related_ids",
                        "supersedes_id",
                        "plan",
                    )
                    if k in suggestion
                }
            )
            self.shared.validate_item(w, data)
            item = self.shared._append_item(w, data, origin="accepted_suggestion")
            w.revision += 1
            w.updated_at = now()
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key,value,updated_at) VALUES (?,?,?)",
                (KEY, w.model_dump_json(), w.updated_at),
            )
            suggestion["applied_item_id"] = item.item_id
            conn.execute(
                "UPDATE sra_chat_suggestions SET data=? WHERE suggestion_id=? AND conversation_id=?",
                (encode(suggestion), sid, cid),
            )
            self._touch(conn, cid)
        return {
            "item": item.model_dump(mode="json"),
            "already_applied": False,
            "revision": w.revision,
        }

    def record_legacy_draft(self, area, text, draft, mode, expected_revision):
        # Compatibility for callers with an already-built draft: reject malformed
        # inputs before writing. HTTP callers use send(), persisting before generation.
        from core.workspace.guide import build_guide_context

        w = self.shared.load(include_chat=False)
        ctx = build_guide_context(self.shared, "", area, None, [], history=[])
        self._validate_draft(draft, ctx, w)
        result = self.send(
            self.legacy_id,
            ConversationSend(
                content=text, area=area, expected_revision=expected_revision
            ),
            "",
            lambda *a: (draft, mode),
            legacy=True,
        )
        if result["status"] != "completed":
            raise WorkspaceError(result["error"])
        return result
