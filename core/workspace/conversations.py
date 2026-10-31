"""Project-local chat persistence. GETs never migrate; models run outside transactions.

An OS lock owns each synchronous generation attempt, not a time-based lease.
Local persistence is idempotent; external provider billing is not exactly-once.
"""

from contextlib import closing, contextmanager, ExitStack, nullcontext
import json
import os
from pathlib import Path
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
    from core.llm.base import LLMTruncatedError
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
            "can_restart": result["status"] == "failed" and reason == "budget_exhausted"
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
        if not any(m.get("actions") for m in messages):
            return messages
        basis = self.material_state()
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
        return {
            m["message_id"]: m.get("actions", [])
            for m in self._guidance_view(cid, [json.loads(r[0]) for r in rows])
        }

    def _validate_draft(self, draft, context, workspace):
        allowed = {
            p["paper_id"]
            for p in context["papers"] + context.get("paper_inventory", [])
        }
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
            return TeachingService(self).free(cid, request)
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
        if area == "writing":
            raise WorkspaceError(NOTICE)
        from core.workspace.guide import build_guide_context

        basis = self.material_state()
        if (
            request.expected_revision is not None
            and request.expected_revision != basis["revision"]
        ):
            raise RevisionConflict("项目记录已有更新，请刷新后重新发送；输入尚未保存")
        recent_messages=self._recent(cid)
        history = [
            {
                "role": m["role"],
                "content": m["content"][:1800],
                "area": m["area"],
                "suggested_replies": m.get("suggested_replies", []),
                "background_outdated": bool(basis['workspace'].brief_revision and (m.get('context_revision') is None or m['context_revision'] < basis['workspace'].brief_revision)),
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
        ]
        from core.workspace.research_coach import build_research_route, saved_reading_context
        context["research_route"] = {k:v for k,v in build_research_route(context, request.content).items()
                                     if k not in ("steps", "next_step")}  # UI labels aren't model evidence.
        context["saved_reading"] = saved_reading_context(self, context["research_route"]["readable_paper_ids"])
        context["research_search_available"] = search_available and not legacy
        from core.workspace.plans import (
            requested,
            build_plan_context,
            prepare_plan,
            rule_plan,
        )

        plan_request = requested(request.content, request.plan_request)
        if plan_request is not None:
            context["plan_generation"] = build_plan_context(
                self.shared, basis["workspace"], context, request.content, plan_request
            )
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
                        if not request.recovery_of:
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
                    self._touch(conn, cid)
            if not duplicate:
                elapsed = time.monotonic()-context_started
                args = (cid,request,topic,generate,token,basis,area,context,elapsed)
                if not request.wait_for_completion and context.get("plan_generation",{}).get("allowed"):
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
        from core.workspace.plans import prepare_plan, rule_plan, ConditionEvidenceError
        from core.workspace.guidance_policy import prepare
        scope = AuditScope(self.db_path,self.project_id,"chat:"+cid+":"+request.request_id,
                           conversation_id=cid,request_id=request.request_id)
        phase_kind = "plan" if context.get("plan_generation",{}).get("allowed") else "guidance"
        rejected_actions=[]
        try:
            if request.recovery_of:
                policy = inherited_policy(self.db_path, "chat:"+cid+":"+request.recovery_of)
                if policy:
                    if policy.get("unavailable"):
                        raise WorkspaceError("原请求的上层调用限制无法核实，未开启模型调用")
                    scope.group_id = policy["group_id"]
                    for key in ("limits", "pricing", "cost_limit", "stop_on_model_failure", "expected_model"):
                        setattr(scope, key, policy[key])
            with scope.activate():
                ticket = scope.begin("phase",phase_kind,"context",elapsed_before_start=context_elapsed)
                scope.finish(ticket)
                with scope.span("phase",phase_kind,"generation"):
                    if context.get("plan_generation",{}).get("allowed") is False:
                        draft,mode = rule_plan(context),"rules"
                    else:
                        draft,mode = generate(area,request.content,context,request.reading_purpose)
                with scope.span("phase",phase_kind,"validation"):
                    draft = GuideDraft.model_validate(draft)
                    # Check citations, record ownership and ALL model actions
                    # before plan rendering or display normalization can replace them.
                    self._validate_draft(draft,context,basis["workspace"])
                    draft = prepare(draft,request.content,context,rejected_actions=rejected_actions)
                    if context.get("plan_generation",{}).get("allowed"):
                        try:
                            draft = prepare_plan(draft,context,request.content)
                        except ConditionEvidenceError as exc:
                            # One bounded repair, only for evidence formatting, not
                            # a fabricated quote/unknown fact. All checks rerun.
                            if mode != "model" or exc.reason not in ("value_not_literal_excerpt", "condition_qualifier_omitted"):
                                raise
                            with scope.span("phase", "plan", "condition_repair:" + exc.field):
                                repair_context = {**context, "condition_repair": {"field": exc.field, "reason": exc.reason}}
                                draft, mode = generate(area,request.content,repair_context,request.reading_purpose)
                                draft = GuideDraft.model_validate(draft)
                                self._validate_draft(draft,context,basis["workspace"])
                                draft = prepare(draft,request.content,context,rejected_actions=rejected_actions)
                                draft = prepare_plan(draft,context,request.content)
                    draft = prepare(draft,request.content,context,rejected_actions=rejected_actions)
                    self._validate_draft(draft,context,basis["workspace"])
                    if rejected_actions:
                        for kind in rejected_actions:
                            ticket=scope.begin("phase","guidance","action_rejected:"+kind)
                            scope.finish(ticket,outcome="rejected")
                        context["delivery_warnings"]=["回答已保存，但部分操作未通过草案归属或可用性校验，未提供这些按钮，也未执行采纳或保存。可在研究材料中核对当前草案。"]
                with scope.span("phase",phase_kind,"saving"):
                    self._finish(cid,request.request_id,token,draft,mode,basis,area,context)
        except Exception as exc:
            with scope.activate():
                diagnostic = ("condition:" + exc.field + ":" + exc.reason) if isinstance(exc,ConditionEvidenceError) else type(exc).__name__
                ticket = scope.begin("phase",phase_kind,"final_failure:" + diagnostic)
                scope.finish(ticket,outcome="failed")
            code = 503 if isinstance(exc,(ConversationStorageError,sqlite3.Error)) else 422 if isinstance(exc,WorkspaceError) else 502
            reason = str(exc) if code in (422,503) else "模型未能返回完整有效方案或指导，原消息已保留；请查看调用记录，再明确重试"
            from core.llm.base import LLMTruncatedError
            if isinstance(exc,LLMTruncatedError):
                reason = "模型回复未完整返回，可能是推理或正文占满了输出额度。本次没有保存不完整草案；原消息和已有材料仍保留。可缩小本次问题后重新发送，或查看调用详情再明确重试。"
            classified, retry_after = failure_reason(exc)
            if classified == "budget_exhausted":
                reason = "本次请求的模型网络调用额度已用完。原请求不能继续重试；如无上层预算限制，可明确开启一次新的处理轮次（会再次调用模型并可能产生费用）。原消息和已有材料仍保留。"
            elif classified == "authentication":
                reason = "模型认证失败。请核对服务凭据或权限后再发起请求；当前请求不会自动重试。"
            elif classified == "rate_limited":
                reason = "模型限流，当前不能直接重试。请等待冷却结束后核对原请求；原消息已保留。"
            elif classified == "cost_exhausted":
                reason = "本次体验的共享费用额度不足以预留下一次调用，已停止发送；可能尚未花满授权金额。原消息和已有材料仍保留，切换项目或重试不会补充额度。"
            elif classified == "experience_expired":
                reason = "本次真实模型体验授权已到期，未发出新调用。原消息和已有材料仍保留，请联系本地操作者重新确认体验安排。"
            elif classified == "experience_limit":
                reason = "本次体验已达到累计调用次数的保护上限，未发出新调用。原消息和已有材料仍保留，重试或开始新一轮不会补充本次体验额度。"
            self._fail(cid,request.request_id,token,reason,code,type(exc).__name__,classified,retry_after)

    def _touch(self, conn, cid):
        conn.execute(
            "UPDATE sra_conversations SET updated_at=?,chat_revision=chat_revision+1 WHERE conversation_id=? AND project_id=?",
            (now(), cid, self.project_id),
        )

    def _finish(self, cid, rid, token, draft, mode, basis, area, context=None):
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
                s = Suggestion(
                    **proposal.model_dump(),
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
            if context.get("research_search_available") and route.get("offer_search") and not stale and len(actions)<4:
                actions.append(GuidanceAction(type="start_search",label="补充代表性文献（先确认范围）",topic=route["search_topic"][:200]))
            replies=draft.suggested_replies[:max(0,4-len(actions))]
            message = Message(
                role="assistant",
                content=draft.reply,
                area=area,
                mode=mode,
                cited_paper_ids=draft.cited_paper_ids,
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
