"""Durable first-send identity reservation; never runs research or a model."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, field_validator, model_validator

from core.knowledge import KnowledgeStore
from core.research_scope import normalize_public_domain
from core.workspace.conversation_lock import hold
from core.workspace.conversation_schema import (
    ConversationCreate,
    GuidanceMode,
    EntryStage,
    DiscussionStep,
)
from core.workspace.conversations import (
    ConversationService,
    ConversationStorageError,
    ConversationNotFound,
)
from core.workspace.materials import fingerprint
from core.workspace.schema import Contract, now
from core.workspace.service import RevisionConflict


class StartConversation(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,120}$")
    title: str = Field(default="新研究", min_length=1, max_length=200)
    domain: str = "computer_science"
    conversation_title: str = Field(default="新对话", min_length=1, max_length=200)
    discussion_goal: str = Field(default="", max_length=2000)
    guidance_mode: GuidanceMode = "direct"
    entry_stage: EntryStage = "orientation"
    discussion_step: DiscussionStep | None = None

    @field_validator("request_id", "title", "conversation_title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("标题及请求编号不能为空白")
        return value

    @model_validator(mode="after")
    def domain_valid(self):
        if self.project_id is None:
            self.domain = normalize_public_domain(self.domain)
        return self


class ConversationStarter:
    def __init__(self, paths, require_project, register_project, owner_id=None):
        self.paths, self.require_project, self.register_project = (
            paths,
            require_project,
            register_project,
        )
        self.db = paths.projects / ".conversation-starts.db"
        self.owner_id = owner_id

    @contextmanager
    def transaction(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        # Two concurrent first-starts race while the store is still a 0-byte
        # file; SQLite (Windows, 3.53) can then briefly classify the new
        # connection as read-only (a stale empty rollback journal next to an
        # empty database, confirmed by diagnostics: writable file, normal
        # attributes, 0-byte main db, leftover -journal). WAL would avoid
        # rollback journals, but its -wal/-shm side files leak from the
        # read-only precheck connection and break the "rejected requests
        # create no files" contract, so instead the setup statements retry a
        # bounded number of times. Retries only cover the statements before
        # any caller code runs, so conflict (409) and idempotent reuse (200)
        # semantics are unchanged; a non-transient error still surfaces as
        # ConversationStorageError.
        conn = None
        for attempt in range(3):
            try:
                conn = sqlite3.connect(self.db, timeout=15)
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS starts (request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, payload TEXT NOT NULL, project_id TEXT NOT NULL, phase TEXT NOT NULL, conversation_id TEXT, created_at TEXT NOT NULL)"
                )
                break
            except sqlite3.Error as exc:
                if conn is not None:
                    conn.close()
                    conn = None
                transient = any(
                    word in str(exc).lower() for word in ("readonly", "locked", "busy")
                )
                if attempt < 2 and transient:
                    time.sleep(0.15 * (attempt + 1))
                    continue
                raise ConversationStorageError(
                    "开始对话的记录暂时不可用，请保留原 request_id 后重试"
                ) from exc
        try:
            with closing(conn), conn:
                yield conn
        except sqlite3.Error as exc:
            raise ConversationStorageError(
                "开始对话的记录暂时不可用，请保留原 request_id 后重试"
            ) from exc

    def _bootstrap(self, row, request):
        db = self.paths.project_db(row["project_id"])
        if db.exists():
            # A reserved identity may have an empty partially initialized DB, but
            # must never take over another project or recreate a finished one.
            with closing(
                sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
            ) as conn:
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                marker = (
                    conn.execute(
                        "SELECT value FROM kv_store WHERE key='project.bootstrap_request'"
                    ).fetchone()
                    if "kv_store" in tables
                    else None
                )
                if marker:
                    if json.loads(marker[0]) != request.request_id:
                        raise RevisionConflict(
                            "保留的项目编号已被其他项目使用，未覆盖数据"
                        )
                    return
                for table in tables - {"sqlite_sequence"}:
                    if conn.execute(
                        'SELECT 1 FROM "' + table.replace('"', '""') + '" LIMIT 1'
                    ).fetchone():
                        raise RevisionConflict(
                            "项目已有内容且无法核对创建依据，未覆盖数据"
                        )
        KnowledgeStore(
            db
        )  # Only an explicit, reserved NEW project initializes knowledge tables.
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for key, value in {
                "research.topic": request.title,
                "project.display_name": request.title,
                "project.domain": request.domain,
                "project.bootstrap_request": request.request_id,
                "project.created_at": row["created_at"],
            }.items():
                conn.execute(
                    "INSERT INTO kv_store(key,value,updated_at) VALUES (?,?,?)",
                    (key, json.dumps(value, ensure_ascii=False), row["created_at"]),
                )

    def start(self, request: StartConversation):
        public_request_id = request.request_id
        if self.owner_id:
            # The durable reservation, project UUID and bootstrap marker must all
            # use the same user-scoped identity. Never trust an owner from JSON.
            request = request.model_copy(update={
                "request_id": "u:" + uuid5(NAMESPACE_URL, self.owner_id + ":" + public_request_id).hex
            })
        identity = request.model_dump(exclude={"request_id"})
        if identity.get("discussion_step") is None:
            identity.pop("discussion_step", None)
        signature = fingerprint(identity)
        # Check a known identity before validating a different project supplied
        # with it. A reused key must report conflict, without initializing any DB
        # for an invalid project or repairing a missing reservation store.
        if self.db.is_file():
            try:
                with closing(
                    sqlite3.connect(
                        self.db.resolve().as_uri() + "?mode=ro", uri=True, timeout=15
                    )
                ) as reader:
                    if reader.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='starts'"
                    ).fetchone():
                        known = reader.execute(
                            "SELECT signature FROM starts WHERE request_id=?",
                            (request.request_id,),
                        ).fetchone()
                        if known and known[0] != signature:
                            raise RevisionConflict(
                                "开始请求的 request_id 已用于不同内容"
                            )
            except RevisionConflict:
                raise
            except sqlite3.Error:
                # 预检查只是快速路径：并发下另一请求可能正持有 starts 库写锁。
                # 此处读不到不构成冲突证据，也不应把并发请求变成 503——
                # 事务内（BEGIN IMMEDIATE）有同一冲突校验兜底，继续走主流程。
                pass
        if request.project_id:
            self.require_project(request.project_id)
            if not self.paths.project_db(request.project_id).is_file():
                raise ConversationNotFound("项目数据库不存在，未创建空库替代原项目")
            from core.workspace.teaching import guard_teaching

            guard_teaching(
                ConversationService(
                    request.project_id, self.paths.project_db(request.project_id)
                )
            )
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM starts WHERE request_id=?", (request.request_id,)
            ).fetchone()
            if row is not None and row["signature"] != signature:
                raise RevisionConflict("开始请求的 request_id 已用于不同内容")
            if row is None:
                pid = (
                    request.project_id
                    or "proj_"
                    + uuid5(NAMESPACE_URL, "sra:start:" + request.request_id).hex
                )
                conn.execute(
                    "INSERT INTO starts VALUES (?,?,?,?,?,?,?)",
                    (
                        request.request_id,
                        signature,
                        request.model_dump_json(),
                        pid,
                        "reserved",
                        None,
                        now(),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM starts WHERE request_id=?", (request.request_id,)
                ).fetchone()
            row = dict(row)
        # No database transaction spans initialization of the other database.
        with hold(self.db, request.request_id) as acquired:
            if not acquired:
                raise RevisionConflict(
                    "开始请求正在处理，请稍后使用原 request_id 继续核对"
                )
            with self.transaction() as conn:
                row = dict(
                    conn.execute(
                        "SELECT * FROM starts WHERE request_id=?", (request.request_id,)
                    ).fetchone()
                )
            db = self.paths.project_db(row["project_id"])
            if row["phase"] == "reserved" and not request.project_id:
                self._bootstrap(row, request)
                with self.transaction() as conn:
                    conn.execute(
                        "UPDATE starts SET phase='project_created' WHERE request_id=?",
                        (request.request_id,),
                    )
            if not db.is_file():
                raise ConversationNotFound(
                    "已创建项目的数据库不可用，未自动重建；请检查项目是否已归档"
                )
            # Register only this explicitly created identity after restart. Do not
            # scan or migrate unrelated historical projects to finish this request.
            if not request.project_id:
                self.register_project(
                    row["project_id"], request.title, request.domain, row["created_at"]
                )
            self.require_project(row["project_id"])
            service = ConversationService(row["project_id"], db)
            if row["phase"] == "ready":
                conversation = service.get(row["conversation_id"])
            else:
                conversation = service.create(
                    ConversationCreate(
                        request_id="start:" + fingerprint(request.request_id),
                        title=request.conversation_title,
                        guidance_mode=request.guidance_mode,
                        entry_stage=request.entry_stage,
                        discussion_step=request.discussion_step,
                        discussion_goal=request.discussion_goal,
                    )
                )
                with self.transaction() as conn:
                    conn.execute(
                        "UPDATE starts SET phase='ready',conversation_id=? WHERE request_id=?",
                        (conversation["conversation_id"], request.request_id),
                    )
            return {
                "request_id": public_request_id,
                "project_id": row["project_id"],
                "conversation_id": conversation["conversation_id"],
                "conversation": conversation,
            }
