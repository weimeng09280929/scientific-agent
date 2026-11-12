"""检索任务服务：持久化、有界执行、幂等、取消与恢复。

约定（详见 docs/conversation-research-tasks.md）：
- 一个项目同时最多一个检索任务；忙时返回明确冲突，不悄悄重复任务。
- 任务行、来源调用、候选与报告全部持久化在项目 knowledge.db。
- 网络与模型调用期间不持有 SQLite 写事务；结果消息与完成状态同一事务提交。
- OS 锁持有执行权；内核在进程死亡时释放锁，GET 只读探测，不自动重启。
- 取消在阶段边界生效；迟到的阶段结果不能把任务改回成功或发布最终回复。
- 重复提交同一 request_id 返回原任务；同一 ID 不同内容返回冲突。
- 检索成功但整理失败时，保留候选，明确重试整理阶段，不重复检索。
"""

from __future__ import annotations

from contextlib import contextmanager, closing
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from contextvars import copy_context
from uuid import uuid4
from core.invocation_audit import read_audit

from pydantic import Field

from core.knowledge.schema import Paper
from core.knowledge.store import KnowledgeStore
from core.research_tasks.limits import SearchLimits, DEFAULT_SOURCES
from core.research_tasks.normalize import dedupe_candidates
from core.research_tasks.detect import SearchIntent
from core.research_tasks.planner import plan_queries
from core.research_tasks.ranking import rank_candidates
from core.research_tasks.sources import call_source
from core.research_tasks.synthesis import (
    SynthesisValidationError,
    synthesize_report,
    report_markdown,
)
from core.workspace.schema import Contract, Message, now
from core.workspace.service import WorkspaceError
from core.research_tasks.report_sources import (
    freeze_sources, reference_index, research_points, cited_text, format_prose,
    supported_limitations, supported_relations, catalog_markdown,
    prose_reference_ids,
    delivery_reference_ids,
)
from core.research_tasks.report_status import report_status

TERMINAL = ("completed", "partial", "failed", "interrupted", "cancelled")
ACTIVE = ("queued", "running")
STAGES = ("planning", "searching", "ranking", "saving", "synthesizing", "delivering", "done")

logger = logging.getLogger(__name__)


class TaskError(WorkspaceError):
    pass


class TaskConflict(WorkspaceError):
    """幂等冲突或并发冲突，调用方映射为 409。"""


class TaskNotFound(WorkspaceError):
    pass


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


DDL = [
    """CREATE TABLE IF NOT EXISTS sra_research_tasks (
       task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
       request_id TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
       status TEXT NOT NULL, stage TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1,
       cancel_requested INTEGER NOT NULL DEFAULT 0,
       topic TEXT NOT NULL, year_from INTEGER, year_to INTEGER,
       planned_by TEXT, planned_queries TEXT, source_calls TEXT, execution_log TEXT,
       candidates TEXT, saved_paper_ids TEXT, report TEXT,
       basis_fingerprint TEXT, start_fingerprint TEXT, basis_changed INTEGER NOT NULL DEFAULT 0,
       mode TEXT, owner_token TEXT, owner_process TEXT, error TEXT, error_code INTEGER,
       card_message_id TEXT, card_action_id TEXT, delivered_message_id TEXT,
       planning_note TEXT, ranking_note TEXT, synthesis_note TEXT, report_versions TEXT,
       created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, updated_at TEXT NOT NULL,
       UNIQUE(conversation_id, request_id))""",
    # 已有库的兼容迁移：缺列时逐条补齐（幂等）。
    "MIGRATIONS",
    "CREATE INDEX IF NOT EXISTS sra_research_active ON sra_research_tasks(project_id, status)",
    "CREATE INDEX IF NOT EXISTS sra_research_conversation ON sra_research_tasks(conversation_id, created_at DESC)",
]

_MIGRATION_COLUMNS = (
    ("planning_note", "TEXT"),
    ("ranking_note", "TEXT"),
    ("synthesis_note", "TEXT"),
    ("report_versions", "TEXT"),
)


def _migrate(conn):
    """为旧库补齐新增列；已存在时跳过。"""
    existing = {
        r[1] for r in conn.execute("PRAGMA table_info(sra_research_tasks)").fetchall()
    }
    for name, col_type in _MIGRATION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE sra_research_tasks ADD COLUMN {name} {col_type}")
# ---- 项目级 OS 锁（与 conversation_lock 同一模式：内核释放，读取只探测） ----

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _task_lock_path(db_path: Path):
    key = sha256(("research-tasks:" + str(db_path.resolve())).encode()).hexdigest()
    return db_path.parent / (".research-" + key[:24] + ".lock")


@contextmanager
def hold_task_lock(db_path: Path):
    path = _task_lock_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        acquired = False
        try:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, BlockingIOError):
            pass
        try:
            yield acquired
        finally:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def task_lock_held(db_path: Path) -> bool:
    path = _task_lock_path(db_path)
    try:
        stream = path.open("r+b")
    except FileNotFoundError:
        return False
    except OSError:
        return True  # 未知归属绝不能授权判定中断
    with stream:
        try:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            return True
        stream.seek(0)
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return False


class SearchTaskRequest(Contract):
    """启动检索任务。content 为用户原始请求；topic 可来自可编辑范围卡片。"""

    content: str = Field(min_length=1, max_length=6000)
    topic: str = Field(min_length=1, max_length=200)
    year_from: int | None = Field(default=None, ge=1500, le=2100)
    year_to: int | None = Field(default=None, ge=1500, le=2100)
    request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"\S")
    card_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    card_action_id: str | None = Field(default=None, min_length=1, max_length=128)


class ResearchTaskService:
    PROCESS_IDENTITY = f"research:{os.getpid()}:{uuid4().hex}"

    def __init__(
        self,
        project_id: str,
        db_path: Path,
        conversations,
        *,
        model_fn=None,
        limits: SearchLimits | None = None,
        runner=None,
        sources: tuple = DEFAULT_SOURCES,
    ):
        self.project_id = project_id
        self.db_path = Path(db_path)
        self.conversations = conversations  # ConversationService（复用材料状态与归属）
        self.model_fn = model_fn  # None => 规则模式
        self.limits = limits or SearchLimits()
        self.sources = sources[: self.limits.sources_per_query]
        self._runner = runner or (lambda fn: threading.Thread(target=copy_context().run, args=(fn,), daemon=True).start())

    # ---- 连接与 DDL ----

    @contextmanager
    def _connection(self, write=False):
        if not self.db_path.is_file():
            raise TaskNotFound("项目数据库不存在")
        try:
            with closing(
                sqlite3.connect(
                    self.db_path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"),
                    uri=True,
                    timeout=15,
                )
            ) as conn:
                conn.row_factory = sqlite3.Row
                if write:
                    with conn:
                        conn.execute("BEGIN IMMEDIATE")
                        if self._has_tables(conn):
                            _migrate(conn)
                        yield conn
                else:
                    yield conn
        except sqlite3.Error as exc:
            raise TaskError("检索任务存储暂时不可用，本次事务未完成") from exc

    def _has_tables(self, conn):
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sra_research_tasks'"
            ).fetchone()
        )

    def _ensure_tables(self, conn):
        for statement in DDL:
            if statement == "MIGRATIONS":
                continue
            conn.execute(statement)
        _migrate(conn)

    def _ensure_migrated(self, conn):
        """旧库缺列时补齐（表已存在的场景）。"""
        _migrate(conn)

    def _row(self, conn, task_id):
        row = (
            conn.execute(
                "SELECT * FROM sra_research_tasks WHERE task_id=? AND project_id=?",
                (task_id, self.project_id),
            ).fetchone()
            if self._has_tables(conn)
            else None
        )
        return dict(row) if row else None

    # ---- 状态呈现 ----

    def _effective(self, row):
        status = row["status"]
        if status == "queued" and row["owner_process"] != self.PROCESS_IDENTITY:
            return "interrupted"
        # running 必然在持锁后设置：锁已释放而状态仍 running => 执行者已丢失。
        # queued 不探测：刚登记的任务可能尚未被 runner 领取，不能误报中断。
        if status == "running" and not task_lock_held(self.db_path):
            return "interrupted"
        return status

    def _log(self, row, stage, detail):
        log = json.loads(row["execution_log"] or "[]")
        log.append({"stage": stage, "detail": detail, "at": now()})
        return log

    def view(self, row, *, include_report=True):
        status = self._effective(row)
        queries = json.loads(row["planned_queries"] or "[]")
        calls = json.loads(row["source_calls"] or "[]")
        saved = json.loads(row["saved_paper_ids"] or "[]")
        candidates = json.loads(row["candidates"] or "[]")
        report = json.loads(row["report"]) if row["report"] else None
        versions = json.loads(row["report_versions"] or "[]") if "report_versions" in row.keys() else []
        # 失败来源按「每个 (查询,来源) 的最新一次尝试」判定：补跑成功后不再误报。
        latest = {}
        for c in calls:
            key = (c.get("query"), c.get("source"))
            latest[key] = c
        failed_sources = sorted(
            {c["source"] for c in latest.values() if c.get("status") == "error"}
        )
        included = sum(1 for c in candidates if c.get("relevance") != "excluded")
        excluded = len(candidates) - included
        view = {
            "task_id": row["task_id"],
            "project_id": row["project_id"],
            "conversation_id": row["conversation_id"],
            "request_id": row["request_id"],
            "status": status,
            "stage": row["stage"] if status in ACTIVE else row["stage"],
            "attempts": row["attempts"],
            "cancel_requested": bool(row["cancel_requested"]),
            "topic": row["topic"],
            "year_from": row["year_from"],
            "year_to": row["year_to"],
            "year_label": self._year_label(row),
            "planned_by": row["planned_by"],
            "planning_note": row["planning_note"] or "",
            "ranking_note": row["ranking_note"] or "",
            "synthesis_note": row["synthesis_note"] or "",
            "queries": queries,
            "source_calls": calls,
            "call_audit": read_audit(self.db_path,self.project_id,operation_id=row["task_id"]),
            "failed_sources": failed_sources,
            "degraded_sources": sorted({c["source"] for c in latest.values() if c.get("degraded")}),
            "candidates_count": len(candidates),
            "included_count": included,
            "excluded_count": excluded,
            "excluded": [
                {"paper_id": c.get("paper_id"), "title": c.get("title", "")[:120],
                 "relevance_reason": c.get("relevance_reason", "")}
                for c in candidates if c.get("relevance") == "excluded"
            ],
            "saved_papers_count": len(saved),
            "saved_paper_ids": saved,
            "basis_changed": bool(row["basis_changed"]),
            "mode": row["mode"],
            "error": row["error"],
            "card_message_id": row["card_message_id"],
            "report_versions": [
                {"version": v.get("version"), "status": v.get("status"),
                 "delivered_at": v.get("delivered_at"), "resume": bool(v.get("resume")),
                 **report_status(v.get("status"), (v.get("report") or {}).get("mode") or v.get("mode"),
                                 v.get("synthesis_note") or "", (v.get("report") or {}).get("catalog_kind"))}
                for v in versions
            ],
            "current_version": versions[-1]["version"] if versions else 0,
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "limits": self.limits.as_dict(),
            "status_label": {
                "completed": "已完成",
                "partial": "部分完成（有来源失败）",
                "failed": "失败",
                "interrupted": "已中断，可重试",
                "cancelled": "已取消",
                "queued": "排队中",
                "running": "运行中",
            }[status],
        }
        if report:
            state = self._report_task(row)
            view.update({k:state[k] for k in ("content_status", "content_label")})
            if status in ("completed", "partial"):
                view["status_label"] = state["status_label"]
        from core.resource_limits import RESOURCES
        view["work_still_running"] = RESOURCES.is_active(self._resource_key(row["task_id"]))
        if row["cancel_requested"] and view["work_still_running"]:
            view["cancel_note"] = "取消已提出，当前操作尚未结束，仍占用处理名额；结束后才可继续。已保存材料保留。"
        if include_report and status in ("completed", "partial", "cancelled", "failed"):
            view["report"] = report
            view["report_markdown"] = report_markdown(self._report_task(row))
        return view

    def _report_task(self, row, version=None):
        """One version-owned projection for panel, API and download. No writes."""
        versions = json.loads(row.get("report_versions") or "[]")
        entry = next((v for v in versions if v.get("version") == version), None) if version is not None else (versions[-1] if versions else None)
        if version is not None and entry is None:
            raise TaskNotFound(f"报告版本 v{version} 不存在")
        report = (entry.get("report") or {}) if entry else json.loads(row.get("report") or "null") or {}
        context = (entry or {}).get("render_context") or {}
        coverage = report.get("coverage") or {}
        status = (entry or {}).get("status") or row["status"]
        synthesis_note = (entry.get("synthesis_note") if entry else row.get("synthesis_note")) or ""
        state = report_status(status, report.get("mode") or (entry.get("mode") if entry else row.get("mode")), synthesis_note, report.get("catalog_kind"))
        return {
            "task_id": row["task_id"], "topic": context.get("topic", row["topic"]),
            "status": status, **state,
            "year_label": context.get("year_label", self._year_label(row)),
            "limits": context.get("limits") or coverage.get("limits") or {},
            "queries": context.get("queries", coverage.get("queries", [])),
            "source_calls": context.get("source_calls", coverage.get("source_calls", [])),
            "report": report, "report_version": (entry or {}).get("version"),
            "report_versions": [{k:v for k,v in entry.items() if k not in ("report","render_context")}] if entry else [],
            "historical_context_missing": not context and not coverage,
            "synthesis_note": synthesis_note,
        }

    def report_document(self, task_id, version=None, *, catalog=False):
        task = self._report_task(self._read(task_id), version)
        if not task["report"]:
            return "任务尚未产出报告；当前状态：" + task["status_label"] + "。已保存资料不等于整理结论。"
        return catalog_markdown(task) if catalog else report_markdown(task)

    def _year_label(self, row):
        if row["year_from"] and row["year_to"]:
            return f"{row['year_from']}—{row['year_to']}"
        if row["year_from"]:
            return f"{row['year_from']} 年之后"
        if row["year_to"]:
            return f"{row['year_to']} 年之前"
        return "未限定年份"

    # ---- 启动 ----

    def start(self, cid: str, request: SearchTaskRequest):
        from contextlib import ExitStack
        with ExitStack() as ownership:
            return self._start(cid, request, ownership)

    def _start(self, cid, request, ownership):
        """启动任务：幂等登记 + 立即返回 task 视图（后台执行）。"""
        from core.workspace.teaching import guard_teaching
        guard_teaching(self.conversations)
        meta = self.conversations.get(cid)  # 归属校验；不存在会抛 ConversationNotFound
        self._require_real_conversation(cid)
        basis = self.conversations.material_state()
        payload = request.model_dump(exclude={"request_id"})
        payload["area"] = self._area_for(meta)
        signature = sha256(encode(payload).encode()).hexdigest()
        rid = request.request_id or ("rt-" + uuid4().hex)
        with self._connection(True) as conn:
            if not self._has_tables(conn):
                self._ensure_tables(conn)
            # 同会话同 request_id：同内容返回原任务；不同内容冲突。
            existing = conn.execute(
                "SELECT * FROM sra_research_tasks WHERE conversation_id=? AND request_id=?",
                (cid, rid),
            ).fetchone()
            if existing:
                row = dict(existing)
                if row["payload_hash"] != signature:
                    raise TaskConflict("request_id 已用于不同的检索请求内容")
                return self.view(row)
            active = conn.execute(
                "SELECT task_id FROM sra_research_tasks WHERE project_id=? AND status IN ('queued','running')",
                (self.project_id,),
            ).fetchone()
            if active:
                raise TaskConflict(
                    "该项目已有检索任务在执行，请等待完成或先取消；未悄悄启动重复任务"
                )
            if not request.card_message_id:
                processing = conn.execute(
                    "SELECT 1 FROM sra_chat_requests WHERE conversation_id=? AND status='processing'",
                    (cid,),
                ).fetchone()
                if processing:
                    raise TaskConflict("当前会话有正在处理的消息请求，请稍后再发起检索")
            task_id = "task-" + uuid4().hex
            from core.resource_limits import RESOURCES
            ownership.enter_context(RESOURCES.acquire(key=self._resource_key(task_id)))
            stamp = now()
            initial_log = encode(
                [
                    {"stage": "queued", "detail": "任务已登记", "at": stamp},
                    {"stage": "queued", "detail": "用户消息已登记", "at": stamp},
                ]
            )
            conn.execute(
                """INSERT INTO sra_research_tasks (
                   task_id,project_id,conversation_id,request_id,payload,payload_hash,
                   status,stage,attempts,cancel_requested,topic,year_from,year_to,
                   planned_by,planned_queries,source_calls,execution_log,candidates,
                   saved_paper_ids,report,basis_fingerprint,start_fingerprint,basis_changed,
                   mode,owner_token,owner_process,error,error_code,card_message_id,
                   card_action_id,delivered_message_id,created_at,started_at,finished_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    self.project_id,
                    cid,
                    rid,
                    encode(payload),
                    signature,
                    "queued",
                    "planning",
                    1,
                    0,
                    request.topic,
                    request.year_from,
                    request.year_to,
                    None,
                    None,
                    None,
                    initial_log,
                    None,
                    None,
                    None,
                    basis["fingerprint"],
                    basis["fingerprint"],
                    0,
                    None,
                    None,
                    self.PROCESS_IDENTITY,
                    None,
                    None,
                    request.card_message_id,
                    request.card_action_id,
                    None,
                    stamp,
                    None,
                    None,
                    stamp,
                ),
            )
            # 用户消息登记（幂等：同 request_id 的用户消息不重复插入）。
            already = conn.execute(
                "SELECT 1 FROM sra_chat_messages WHERE conversation_id=? AND request_id=? AND project_id=? AND json_extract(data,'$.role')='user'",
                (cid, rid, self.project_id),
            ).fetchone()
            if not already and self._has_chat_tables(conn):
                message = Message(
                    role="user",
                    content=request.content,
                    area=self._area_for(meta),
                    mode="user",
                    conversation_id=cid,
                    request_id=rid,
                )
                conn.execute(
                    "INSERT INTO sra_chat_messages (message_id,project_id,conversation_id,request_id,data) VALUES (?,?,?,?,?)",
                    (
                        message.message_id,
                        self.project_id,
                        cid,
                        rid,
                        encode(json.loads(message.model_dump_json())),
                    ),
                )
                conn.execute(
                    "UPDATE sra_conversations SET chat_revision=chat_revision+1, updated_at=? WHERE conversation_id=? AND project_id=?",
                    (stamp, cid, self.project_id),
                )
            conn.execute(
                "UPDATE sra_research_tasks SET execution_log=? WHERE task_id=?",
                (
                    encode(
                        [
                            {"stage": "queued", "detail": "任务已登记", "at": stamp},
                            {"stage": "queued", "detail": "用户消息已登记", "at": stamp},
                        ]
                    ),
                    task_id,
                ),
            )
        row = self._read(task_id)
        self._launch(task_id, 1, ownership)
        return self.view(row)

    def _launch(self, task_id, attempt, ownership):
        lease = ownership.pop_all()
        def work():
            try:
                self._execute(task_id, attempt)
            finally:
                lease.close()
        try:
            self._runner(work)
        except BaseException:
            lease.close()
            self._update(task_id, status="failed", error="后台任务未能启动，请使用原任务明确重试",
                         error_code=503, finished_at=now())
            raise TaskError("后台任务未能启动，请使用原任务明确重试") from None

    def _resource_key(self, task_id):
        return str(self.db_path.resolve()) + ":research:" + task_id

    def _require_real_conversation(self, cid):
        """只允许在真实会话中发起检索；legacy 历史会话不做表迁移。"""
        with self._connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sra_conversations'"
            ).fetchone()
            row = (
                conn.execute(
                    "SELECT 1 FROM sra_conversations WHERE conversation_id=? AND project_id=?",
                    (cid, self.project_id),
                ).fetchone()
                if exists
                else None
            )
        if row is None:
            raise TaskError("请在新建的研究对话中发起检索；历史会话不迁移、不重建")

    def _has_chat_tables(self, conn):
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sra_chat_messages'"
            ).fetchone()
        )

    def _area_for(self, meta):
        mapping = {
            "orientation": "overview",
            "literature": "literature",
            "question": "question",
            "design": "study",
        }
        return mapping.get(meta["entry_stage"], "literature")

    def _read(self, task_id):
        with self._connection() as conn:
            row = self._row(conn, task_id)
        if row is None:
            raise TaskNotFound("检索任务不存在或不属于当前项目")
        return row

    # ---- 执行 ----

    def _update(self, task_id, **fields):
        fields["updated_at"] = now()
        sets = ",".join(f"{k}=?" for k in fields)
        with self._connection(True) as conn:
            conn.execute(
                f"UPDATE sra_research_tasks SET {sets} WHERE task_id=?",
                (*fields.values(), task_id),
            )
        return self._read(task_id)

    def _cancelled(self, task_id) -> bool:
        row = self._read(task_id)
        return row["cancel_requested"] == 1 or row["status"] == "cancelled"

    def _execute(self, task_id: str, attempt: int):
        try:
            with hold_task_lock(self.db_path) as acquired:
                if not acquired:
                    self._update(
                        task_id,
                        status="failed",
                        error="项目已有其他检索任务在执行，本任务未启动",
                        error_code=409,
                        finished_at=now(),
                    )
                    return
                token = uuid4().hex
                self._update(
                    task_id,
                    status="running",
                    stage="planning",
                    owner_token=token,
                    owner_process=self.PROCESS_IDENTITY,
                    started_at=now(),
                )
                row = self._read(task_id)
                payload = json.loads(row["payload"])
                from core.invocation_audit import AuditScope
                with AuditScope(self.db_path,self.project_id,task_id,conversation_id=row["conversation_id"],request_id=row["request_id"],task_id=task_id).activate():
                    self._run_pipeline(task_id, row, payload, token, attempt)
        except Exception as exc:  # 执行线程的最后防线：任务必须落到明确状态
            logger.exception("检索任务 %s 执行失败", task_id)
            try:
                row = self._read(task_id)
                if row["status"] in ACTIVE:
                    self._update(
                        task_id,
                        status="failed",
                        stage="synthesizing" if row["candidates"] else "searching",
                        error=f"{type(exc).__name__}: {exc}"[:500],
                        error_code=500,
                        finished_at=now(),
                    )
            except TaskError:  # pragma: no cover - 存储彻底不可用
                logger.exception("检索任务 %s 失败状态无法写回", task_id)

    def _run_pipeline(self, task_id, row, payload, token, attempt):
        topic = row["topic"]
        log = json.loads(row["execution_log"] or "[]")
        resume = bool(payload.get("_resume"))

        # ---- 1. 查询规划（本地/模型，无网络写事务） ----
        if resume:
            # 补跑：保留原任务的主题、查询与范围，不重新规划。
            queries = json.loads(row["planned_queries"] or "[]")
            planned_by = row["planned_by"] or "rules"
            row = self._update(task_id, stage="searching")
            log.append(
                {
                    "stage": "planning",
                    "detail": f"补跑：沿用原查询计划（{len(queries)} 条，{planned_by}），未重新规划",
                    "at": now(),
                }
            )
        else:
            queries, planned_by, plan_note = plan_queries(
                topic, payload["content"], self.limits, self.model_fn
            )
            row = self._update(
                task_id,
                planned_queries=encode(queries),
                planned_by=planned_by,
                planning_note=plan_note,
                stage="searching",
            )
            log.append(
                {
                    "stage": "planning",
                    "detail": f"{len(queries)} 条查询（{planned_by}）" + (f"；{plan_note}" if plan_note else ""),
                    "at": now(),
                }
            )

        # ---- 2. 有界检索（网络；每步检查取消） ----
        # source_calls 保留全部历史调用记录（含补跑），按 (查询,来源) 的
        # 最新一次尝试判定当前状态；审计不覆盖、不丢失。
        prior_calls = json.loads(row["source_calls"] or "[]")
        latest = {}
        for c in prior_calls:
            latest[(c.get("query"), c.get("source"))] = c
        outcomes = []
        if not resume and row["candidates"] and attempt > 1 and not payload.get("_re_search"):
            # 重试且已有候选：不重复检索（用户未要求重新搜索）。
            log.append({"stage": "searching", "detail": "复用上次已保存的候选，未重新检索", "at": now()})
        elif resume:
            redo = {k for k, c in latest.items() if c.get("status") == "error"}
            new_calls = []
            if redo:
                redo_by_query = {}
                for q, s in redo:
                    redo_by_query.setdefault(q, set()).add(s)
                for q in queries:
                    if self._cancelled(task_id):
                        self._finish_cancel(task_id, row, log)
                        return
                    targets = redo_by_query.get(q["query"])
                    if not targets:
                        continue
                    for source in self.sources:
                        if source not in targets:
                            continue
                        if self._cancelled(task_id):
                            self._finish_cancel(task_id, row, log)
                            return
                        outcome = call_source(
                            source,
                            q["query"],
                            max_results=self.limits.max_results_per_call,
                            timeout=self.limits.call_timeout,
                            year_from=row["year_from"],
                            year_to=row["year_to"],
                            attempt=attempt,
                        )
                        outcomes.append(outcome)
                        new_calls.append(outcome.to_dict())
            row = self._update(task_id, source_calls=encode(prior_calls + new_calls))
            log.append(
                {
                    "stage": "searching",
                    "detail": (
                        f"补跑：仅重试此前失败的 {len(redo)} 组调用，"
                        f"实际执行 {len(outcomes)} 次；已成功来源未重跑"
                    ),
                    "at": now(),
                }
            )
        else:
            for q in queries:
                if self._cancelled(task_id):
                    self._finish_cancel(task_id, row, log)
                    return
                for source in self.sources:
                    if self._cancelled(task_id):
                        self._finish_cancel(task_id, row, log)
                        return
                    outcome = call_source(
                        source,
                        q["query"],
                        max_results=self.limits.max_results_per_call,
                        timeout=self.limits.call_timeout,
                        year_from=row["year_from"],
                        year_to=row["year_to"],
                        attempt=attempt,
                    )
                    outcomes.append(outcome)
            calls = [o.to_dict() for o in outcomes]
            row = self._update(task_id, source_calls=encode(calls))
            log.append(
                {
                    "stage": "searching",
                    "detail": (
                        f"{len(queries)} 条查询 × 至多 {self.limits.sources_per_query} 来源，"
                        f"实际调用 {len(outcomes)} 次"
                    ),
                    "at": now(),
                }
            )

        calls = json.loads(row["source_calls"] or "[]")
        current_latest = {}
        for c in calls:
            current_latest[(c.get("query"), c.get("source"))] = c
        current_statuses = list(current_latest.values())
        if outcomes and all(o.status == "error" for o in outcomes) and not resume:
            error = "；".join(f"{o.source}: {o.error[:80]}" for o in outcomes[:3])
            self._update(
                task_id,
                status="failed",
                stage="searching",
                execution_log=encode(
                    log + [{"stage": "searching", "detail": "全部来源调用失败", "at": now()}]
                ),
                error=f"全部检索来源失败：{error}",
                error_code=502,
                finished_at=now(),
            )
            return
        if resume and outcomes and all(o.status == "error" for o in outcomes):
            # 补跑再次失败：旧报告保留，不冒充成功；任务保持 partial。
            failed_names = sorted({o.source for o in outcomes})
            self._update(
                task_id,
                status="partial",
                stage="done",
                error=f"补跑仍未成功，失败来源：{'、'.join(failed_names)}；已交付报告保持不变",
                error_code=502,
                finished_at=now(),
                execution_log=encode(
                    log + [{"stage": "searching", "detail": "补跑全部失败；旧报告保留", "at": now()}]
                ),
            )
            self._deliver_resume_note(task_id, row, ok=False, failed_names=failed_names)
            return
        if resume and (not outcomes or all(o.count == 0 for o in outcomes)):
            # 补跑无可重试失败项，或重跑成功但零新增：候选与依据未变，
            # 不重复调用模型、不生成重复报告；来源完整性状态按最新尝试更新。
            self._update(
                task_id,
                status="partial"
                if any(c.get("status") == "error" or c.get("degraded") for c in current_statuses)
                else "completed",
                stage="done",
                finished_at=now(),
                execution_log=encode(
                    log
                    + [
                        {
                            "stage": "searching",
                            "detail": "补跑完成：候选与依据无实质变化，保留原报告，未重复整理",
                            "at": now(),
                        }
                    ]
                ),
            )
            self._deliver_resume_note(task_id, row, ok=True)
            return

        # ---- 3. 去重与筛选 ----
        if self._cancelled(task_id):
            self._finish_cancel(task_id, row, log)
            return
        raw_candidates = []
        for outcome in outcomes:
            raw_candidates.extend(outcome.papers)
        merged = dedupe_candidates(raw_candidates)
        # 复用场景：合并上次候选（保持重试不重复入库）。
        for prior in json.loads(row["candidates"] or "[]"):
            if all(
                (prior.get("doi") and prior.get("doi") != c.get("doi"))
                or (prior.get("arxiv_id") and prior.get("arxiv_id") != c.get("arxiv_id"))
                for c in merged
            ):
                merged.append(prior)
        # Publish filtering before its (possibly slow) model call, not afterwards.
        self._update(task_id, stage="ranking")
        ranked, ranked_by, rank_note = rank_candidates(merged, topic, self.limits, self.model_fn)
        for index, candidate in enumerate(ranked):
            candidate.setdefault("paper_id", "cand-" + uuid4().hex)
        row = self._update(
            task_id,
            candidates=encode(ranked),
            ranking_note=rank_note,
            stage="saving",
            execution_log=encode(
                log + [
                    {
                        "stage": "ranking",
                        "detail": (
                            f"去重后 {len(merged)} 条，筛选保留 {len(ranked)} 条（{ranked_by}）"
                            + (f"；{rank_note}" if rank_note else "")
                        ),
                        "at": now(),
                    }
                ]
            ),
        )
        log = json.loads(row["execution_log"] or "[]")

        # ---- 4. 保存材料（复用论文库；不覆盖已有更完整元数据） ----
        if self._cancelled(task_id):
            self._finish_cancel(task_id, row, log)
            return
        saved, linked, skipped = self._save_papers(task_id, ranked)
        # 任务关联的文献 = 本轮新入库 + 关联已有（含此前尝试已入库的）；用于交付引用。
        final_ids = list(dict.fromkeys([*saved, *linked]))
        basis = self.conversations.material_state()
        row = self._update(
            task_id,
            saved_paper_ids=encode(final_ids),
            candidates=encode(ranked),
            basis_fingerprint=basis["fingerprint"],
            stage="synthesizing",
            execution_log=encode(
                log
                + [
                    {
                        "stage": "saving",
                        "detail": f"新入库 {len(saved)} 条，关联已有 {len(linked)} 条，未重复入库 {len(skipped)} 条",
                        "at": now(),
                    }
                ]
            ),
        )
        log = json.loads(row["execution_log"] or "[]")

        # ---- 5. 研究现状整理（模型；失败保留候选，可只重试整理） ----
        if self._cancelled(task_id):
            self._finish_cancel(task_id, row, log)
            return
        saved_full = [self._paper_view(self._read_paper(pid)) for pid in saved]
        # 纳入综合的候选：排除被标记为不相关（relevance=excluded）的条目；
        # 被排除者仍保留在任务候选列表中并明确标记，不混入报告引用。
        report_candidates = [
            c for c in ranked if c.get("relevance") != "excluded"
        ]
        report_candidates = [c for c in report_candidates if c.get("paper_id")]
        mode = "model" if self.model_fn is not None else "rules"
        from core.research_tasks.coverage import coverage_facts
        coverage = coverage_facts(queries,calls,ranked,unique_count=len(merged),saved=saved,linked=linked,
                                  limits=self.limits.as_dict(),ranking_mode=ranked_by)
        if not report_candidates:
            # 没有纳入候选时，不让模型发挥；由 coverage 区分未返回和已返回但排除。
            report = synthesize_report(
                topic, queries, [], self._outcomes_from(coverage["source_calls"]), self.limits, None,coverage=coverage
            )[0]
            synth_note = ""
        else:
            try:
                report, synth_note = synthesize_report(
                    topic,
                    queries,
                    report_candidates,
                    self._outcomes_from(coverage["source_calls"]),
                    self.limits,
                    self.model_fn,
                    coverage=coverage,
                )
            except SynthesisValidationError as exc:
                self._update(
                    task_id,
                    status="failed",
                    stage="synthesizing",
                    error=f"整理阶段失败（检索结果已保留，可只重试整理）：{exc}",
                    error_code=502,
                    finished_at=now(),
                    execution_log=encode(
                        log + [{"stage": "synthesizing", "detail": str(exc)[:200], "at": now()}]
                    ),
                )
                return
        mode = report.get("mode", mode)
        row = self._update(task_id, synthesis_note=synth_note)
        self._deliver(task_id, row, report, mode, saved, linked, token, resume=resume)

    def _outcomes_from(self, calls):
        from core.research_tasks.sources import SourceOutcome

        return [
            SourceOutcome(
                source=c["source"],
                query=c["query"],
                status=c["status"],
                count=c["count"],
                error=c.get("error", ""),
            )
            for c in calls
        ]

    def _read_paper(self, paper_id):
        try:
            store = KnowledgeStore(self.db_path, read_only=True)
            return store.get_paper(paper_id)
        except Exception:
            return None

    def _paper_view(self, paper):
        if paper is None:
            return {}
        return {
            "paper_id": paper.paper_id,
            "title": paper.title,
            "year": paper.year,
            "abstract": paper.abstract or "",
            "has_abstract": bool((paper.abstract or "").strip()),
            "doi": paper.doi,
            "arxiv_id": paper.arxiv_id,
            "sources": ["library"],
        }

    def _save_papers(self, task_id, ranked):
        """入库新论文；已有论文只关联不覆盖。返回 (saved_ids, linked_ids, skipped)。"""
        store = KnowledgeStore(self.db_path)
        existing = {p.paper_id: p for p in store.list_papers()}
        by_doi = {
            (p.doi or "").lower(): pid for pid, p in existing.items() if p.doi
        }
        by_arxiv = {
            (p.arxiv_id or "").lower(): pid for pid, p in existing.items() if p.arxiv_id
        }
        saved, linked, skipped = [], [], []
        for candidate in ranked:
            doi = (candidate.get("doi") or "").lower()
            arxiv = (candidate.get("arxiv_id") or "").lower()
            if candidate.get("paper_id") in existing:
                # 本任务此前已入库（重试），不重复入库。
                linked.append(candidate["paper_id"])
                continue
            match = by_doi.get(doi) or by_arxiv.get(arxiv)
            if match:
                linked.append(match)
                candidate["paper_id"] = match
                continue
            if not candidate.get("title", "").strip():
                skipped.append(candidate.get("paper_id"))
                continue
            paper = Paper(
                paper_id=candidate["paper_id"],
                title=candidate["title"].strip(),
                authors=candidate.get("authors") or [],
                year=candidate.get("year"),
                venue=candidate.get("venue") or None,
                arxiv_id=candidate.get("arxiv_id"),
                doi=candidate.get("doi"),
                abstract=candidate.get("abstract") or None,
                url=candidate.get("url") or None,
                metadata={
                    "research_task_id": task_id,
                    "retrieved_at": candidate.get("retrieved_at"),
                    "source_queries": candidate.get("queries") or [],
                    "relevance_reason": candidate.get("relevance_reason", ""),
                    "candidates_only": True,  # 候选文献不等于用户已阅读或认可
                    "text_origin": "source_abstract",
                },
                source_stage="research",
            )
            store.save_paper(paper)
            by_doi[doi] = paper.paper_id if doi else by_doi.get(doi)
            by_arxiv[arxiv] = paper.paper_id if arxiv else by_arxiv.get(arxiv)
            saved.append(paper.paper_id)
        return saved, linked, skipped

    # ---- 交付与终态 ----

    def _deliver(self, task_id, row, report, mode, saved_ids, linked_ids, token, resume=False):
        """结果消息与完成状态在同一事务提交；迟到取消不能发布最终回复。"""
        cid = row["conversation_id"]
        stamp = now()
        basis = self.conversations.material_state()
        basis_changed = basis["fingerprint"] != row["basis_fingerprint"]
        # 部分完成按「每个 (查询,来源) 的最新一次尝试」判定：补跑修复后可回到 completed。
        latest = {}
        for c in json.loads(row["source_calls"] or "[]"):
            latest[(c.get("query"), c.get("source"))] = c
        status = (
            "partial"
            if any(c["status"] == "error" or c.get("degraded") for c in latest.values())
            else "completed"
        )
        area = json.loads(row["payload"]).get("area") or "literature"
        report = freeze_sources(report, json.loads(row["candidates"] or "[]"), mode)
        catalog_ids = list(dict.fromkeys([*saved_ids, *linked_ids]))
        report["source_snapshot"]["catalog"] = [p for p in report["source_snapshot"]["catalog"] if p["paper_id"] in catalog_ids]
        refs = reference_index(report)
        cited = delivery_reference_ids(report)
        cited = [pid for pid in cited if pid in catalog_ids]
        content = self._delivery_text(
            row, report, status, len(saved_ids), len(linked_ids), resume=resume
        )
        message = Message(
            role="assistant",
            content=content,
            area=area,
            mode=mode,
            cited_paper_ids=cited,
            conversation_id=cid,
            request_id=row["request_id"],
            research_task_id=task_id,
            report_citations=[p for p in refs if p["paper_id"] in cited],
            suggested_replies=self._delivery_choices(report),
        )
        actions = []
        if cited:
            actions.append(
                {
                    "action_id": uuid4().hex,
                    "type": "view_references",
                    "label": "查看支撑本条回复的文献",
                    "paper_ids": cited,
                }
            )
        if catalog_ids:
            actions.append({"action_id":uuid4().hex,"type":"view_references", "label":"查看本轮资料目录（非全部正文引用）","paper_ids":catalog_ids})
        message_data = json.loads(message.model_dump_json())
        message_data["actions"] = actions
        with self._connection(True) as conn:
            current = self._row(conn, task_id)
            if current is None:
                raise TaskError("任务不存在，无法交付")
            if (
                current["status"] not in ACTIVE
                or current["cancel_requested"] == 1
                or current["owner_token"] not in (None, token)
            ):
                # 取消/中断/换代的迟到结果：不发布，不把任务改回成功。
                return
            log = json.loads(current["execution_log"] or "[]")
            # 报告版本：每次成功交付追加一版，旧版本保留（下载可指定版本）。
            versions = json.loads(current["report_versions"] or "[]") if current["report_versions"] else []
            version_no = (versions[-1]["version"] + 1) if versions else 1
            versions.append(
                {
                    "version": version_no,
                    "status": status,
                    "report": report,
                    "mode": mode,
                    "resume": bool(resume),
                    "saved_count": len(saved_ids),
                    "linked_count": len(linked_ids),
                    "delivered_at": stamp,
                    "synthesis_note": row.get("synthesis_note") or "",
                    "render_context": {"topic":row["topic"], "year_label":self._year_label(row),
                        "limits":self.limits.as_dict(), "queries":json.loads(row["planned_queries"] or "[]"),
                        "source_calls":json.loads(row["source_calls"] or "[]")},
                }
            )
            content = (
                message_data["content"]
                + f"\n（报告版本 v{version_no}，可在任务卡查看各版本）"
            )
            message_data["content"] = content
            message_data["report_version"] = version_no
            redeliver = bool(current["delivered_message_id"]) and not resume
            if redeliver:
                # 结果此前已交付（非补跑重跑）：不重复发布消息，只写回重跑后的终态。
                log.append({"stage": "delivering", "detail": f"重跑完成，结果此前已交付（{status}，v{version_no}）", "at": stamp})
                conn.execute(
                    "UPDATE sra_research_tasks SET status=?, stage='done', report=?, mode=?, basis_changed=?, "
                    "report_versions=?, execution_log=?, finished_at=?, updated_at=? WHERE task_id=?",
                    (status, encode(report), mode, int(basis_changed), encode(versions), encode(log), stamp, stamp, task_id),
                )
                return
            message_id = message_data["message_id"]
            conn.execute(
                "INSERT INTO sra_chat_messages (message_id,project_id,conversation_id,request_id,data) VALUES (?,?,?,?,?)",
                (message_id, self.project_id, cid, row["request_id"], encode(message_data)),
            )
            log.append({"stage": "delivering", "detail": f"结果已交付（{status}，v{version_no}）", "at": stamp})
            conn.execute(
                "UPDATE sra_research_tasks SET status=?, stage='done', report=?, mode=?, basis_changed=?, "
                "delivered_message_id=?, report_versions=?, execution_log=?, finished_at=?, updated_at=? WHERE task_id=?",
                (status, encode(report), mode, int(basis_changed), message_id, encode(versions), encode(log), stamp, stamp, task_id),
            )
            conn.execute(
                "UPDATE sra_conversations SET chat_revision=chat_revision+1, updated_at=? WHERE conversation_id=? AND project_id=?",
                (stamp, cid, self.project_id),
            )

    def _delivery_text(self, row, report, status, saved_count, linked_count, *, resume=False) -> str:
        latest = {}
        for c in json.loads(row["source_calls"] or "[]"):
            latest[(c.get("query"), c.get("source"))] = c
        still_failed = sorted(
            {c["source"] for c in latest.values() if c.get("status") == "error"}
        )
        if resume:
            if still_failed:
                header = (
                    "检索补跑完成，仍有来源失败："
                    + "、".join(still_failed)
                    + "。覆盖可能仍不足，可再次补跑或调整检索范围。"
                )
            else:
                header = "检索补跑完成：此前失败的来源本次已成功，以下为更新后的整理结果。"
        else:
            header = {
                "completed": "检索与整理已完成。",
                "partial": "检索与整理部分完成：部分来源调用失败，覆盖可能不足。",
            }[status]
        if row.get("synthesis_note") and report.get("mode") == "rules":
            header = "文献资料已保存，但模型内容整理未成功；本条仅交付规则元信息目录。" + ("部分来源也未成功，覆盖不足。" if status == "partial" else "")
        lines = [header, "仅依据本轮可用资料，不代表全面调研或创新已获证明。"]
        refs = reference_index(report)
        points = research_points(report)
        if points:
            lines += ["", "**研究要点（基于已提供的摘要／片段）**"]
            lines += ["- " + cited_text(text, ids, refs) for text,ids in points]
            relation = supported_relations(report)
            if relation:
                lines += ["", "**主要差异：** " + format_prose(relation,refs)]
            limits = supported_limitations(report)
            if limits:
                lines += ["", "**待核查：** " + cited_text(limits[0]["point"], limits[0]["paper_ids"], refs)]
            else:
                lines.append("\n现有报告尚未给出有逐项引用支持的局限；需要核对原文，不能据此认定研究空白。")
        elif not (report.get("coverage") or {}).get("included_count", len(report.get("representative") or [])):
            lines.append("本轮没有可用于综合的资料：可能是成功零结果，或候选已返回但被筛选排除；具体状态见任务详情。未生成研究结论。")
        elif report.get("mode") == "rules":
            lines.append("本轮仅交付规则元信息目录，未交付模型内容综合；没有把目录描述为研究结论。")
        else:
            lines.append("现有报告没有可追溯到可读摘要或正文的研究要点；仅元信息不能支持具体方法或结论，请补充材料后再讨论。")
        lines.append("\n可从下面选择继续讨论；详细统计见任务详情。问题与方案仍须明确采纳。")
        return "\n".join(lines)

    def _delivery_choices(self, report):
        from core.workspace.schema import ReplyOption
        flow = self.conversations.shared.workflow().get("flow") or {}
        if not research_points(report):
            pairs = [("讨论缺少哪些材料", "请说明现有资料还缺少哪些阅读依据，先讨论，不自动重新检索。"),
                     ("缩小检索问题", "先讨论怎样缩小检索问题和关键词，不自动执行检索。")]
        elif flow.get("planReady"):
            pairs = [("核对现有方案依据", "请对照刚才的文献核对当前方案依据，先讨论，不自动修改或采纳。"),
                     ("讨论方案待确认事项", "请区分当前方案已确认与仍待核查的事项，不执行实验。")]
        elif flow.get("question"):
            pairs = [("核对当前研究问题", "请对照这些资料核对当前研究问题的范围和依据，先不修改正式记录。"),
                     ("讨论方案比较条件", "基于当前问题和文献讨论拟定哪些比较或论证条件，暂不生成正式方案。")]
        else:
            pairs = [("比较论文方法", "请比较这些材料的研究对象、方法与适用条件，说明实际阅读范围，不重新检索。"),
                     ("讨论候选研究问题", "请根据这些材料与已知条件讨论少量具体研究问题，先给候选，不替我确认或采纳。")]
        return [ReplyOption(label=label,content=content) for label,content in pairs]

    def _deliver_resume_note(self, task_id, row, *, ok, failed_names=()):
        """补跑未能产生新报告时，在会话中明确说明；旧报告保持有效，不冒充新结果。"""
        cid = row["conversation_id"]
        stamp = now()
        versions = []
        current = self._read(task_id)
        if current["report_versions"]:
            versions = json.loads(current["report_versions"])
        if ok:
            content = "检索补跑完成：失败来源已恢复，但本轮候选与依据没有实质变化，原报告仍为当前版本，未重复生成新报告。"
        else:
            content = (
                "检索补跑未成功（失败来源："
                + "、".join(failed_names)
                + "）。上一次的报告与下载入口保持有效，未被覆盖；你可以稍后再次补跑或调整范围后重新检索。"
            )
        if versions:
            content += f"当前可用报告为 v{versions[-1]['version']}。"
        message = Message(
            role="assistant",
            content=content,
            area=json.loads(row["payload"]).get("area") or "literature",
            mode="rules",
            conversation_id=cid,
            request_id=row["request_id"] + ":resume-note:" + uuid4().hex[:8],
        )
        with self._connection(True) as conn:
            current = self._row(conn, task_id)
            if current is None or current["status"] not in ("partial", "failed", "completed"):
                return
            conn.execute(
                "INSERT INTO sra_chat_messages (message_id,project_id,conversation_id,request_id,data) VALUES (?,?,?,?,?)",
                (message.message_id, self.project_id, cid, message.request_id, encode(json.loads(message.model_dump_json()))),
            )
            conn.execute(
                "UPDATE sra_conversations SET chat_revision=chat_revision+1, updated_at=? WHERE conversation_id=? AND project_id=?",
                (stamp, cid, self.project_id),
            )

    def _finish_cancel(self, task_id, row, log):
        stamp = now()
        self._update(
            task_id,
            status="cancelled",
            finished_at=stamp,
            execution_log=encode(
                log + [{"stage": "cancelled", "detail": "用户取消；已取得的材料保留", "at": stamp}]
            ),
        )

    # ---- 读取、取消、重试、列表 ----

    def clarify(self, cid: str, request, intent: SearchIntent):
        """检索意向不明确：登记用户消息并交付一条规则回复 + 可编辑检索范围卡片。

        不启动任何后台任务、不调用模型、不检索。重复 request_id 不重复插入。
        """
        meta = self.conversations.get(cid)
        self._require_real_conversation(cid)
        rid = request.request_id or ("cl-" + uuid4().hex)
        area = self._area_for(meta)
        stamp = now()
        user_message = Message(
            role="user",
            content=request.content,
            area=area,
            mode="user",
            conversation_id=cid,
            request_id=rid,
        )
        card = {
            "action_id": uuid4().hex,
            "type": "start_search",
            "label": "开始检索",
            "topic": intent.topic or "",
            "year_from": intent.year_from,
            "year_to": intent.year_to,
            "description": intent.reason or "请补充或修改检索主题",
        }
        reply = Message(
            role="assistant",
            content=(
                "这看起来像是想检索文献，但还缺少明确的主题或范围，我没有启动检索。"
                "你可以在下面的卡片里补充或修改检索主题后点击开始；"
                "也可以直接回复更完整的检索请求。"
            ),
            area=area,
            mode="rules",
            questions=[intent.reason or "请补充要检索的主题"],
            conversation_id=cid,
            request_id=rid,
        )
        reply_data = json.loads(reply.model_dump_json())
        reply_data["actions"] = [card]
        with self._connection(True) as conn:
            if not self._has_tables(conn):
                self._ensure_tables(conn)
            duplicate = conn.execute(
                "SELECT 1 FROM sra_chat_messages WHERE conversation_id=? AND request_id=? AND project_id=?",
                (cid, rid, self.project_id),
            ).fetchone()
            if not duplicate and self._has_chat_tables(conn):
                conn.execute(
                    "INSERT INTO sra_chat_messages (message_id,project_id,conversation_id,request_id,data) VALUES (?,?,?,?,?)",
                    (user_message.message_id, self.project_id, cid, rid, encode(json.loads(user_message.model_dump_json()))),
                )
                conn.execute(
                    "INSERT INTO sra_chat_messages (message_id,project_id,conversation_id,request_id,data) VALUES (?,?,?,?,?)",
                    (reply_data["message_id"], self.project_id, cid, rid, encode(reply_data)),
                )
                conn.execute(
                    "UPDATE sra_conversations SET chat_revision=chat_revision+1, updated_at=? WHERE conversation_id=? AND project_id=?",
                    (stamp, cid, self.project_id),
                )
        return {
            "status": "completed",
            "request_id": rid,
            "messages": [json.loads(user_message.model_dump_json()), reply_data],
            "suggestions": [],
        }

    def resolve_card(self, cid: str, message_id: str, action_id: str):
        """读取会话消息上持久化的 start_search 卡片；服务端绑定，不信调用方。"""
        with self._connection() as conn:
            if not self._has_chat_tables(conn):
                raise TaskNotFound("卡片不存在或不属于当前会话")
            row = conn.execute(
                "SELECT data FROM sra_chat_messages WHERE project_id=? AND conversation_id=? AND message_id=?",
                (self.project_id, cid, message_id),
            ).fetchone()
        if row is None:
            raise TaskNotFound("卡片所属消息不存在或不属于当前会话")
        # Saved card JSON stays unchanged after a move; its original project
        # context is no longer an authorization to start work in the new one.
        from core.workspace.conversation_moves import history
        with self._connection() as conn:
            moves = history(conn, cid)
        if moves and message_id in moves[-1]['message_origins']:
            raise TaskConflict("历史卡片来自移动前的项目，请核对目标资料后重新明确发起检索")
        data = json.loads(row[0])
        action = next(
            (a for a in data.get("actions", []) if a.get("action_id") == action_id),
            None,
        )
        if not action or action.get("type") != "start_search":
            raise TaskNotFound("开始检索卡片不存在或不属于当前消息")
        return action

    def get(self, task_id):
        return self.view(self._read(task_id))

    def list(self, cid, limit=20, offset=0):
        with self._connection() as conn:
            if not self._has_tables(conn):
                return {"tasks": [], "next_offset": None}
            rows = conn.execute(
                "SELECT * FROM sra_research_tasks WHERE conversation_id=? AND project_id=? "
                "ORDER BY created_at DESC, task_id DESC LIMIT ? OFFSET ?",
                (cid, self.project_id, limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM sra_research_tasks WHERE conversation_id=? AND project_id=?",
                (cid, self.project_id),
            ).fetchone()["n"]
        views = [self.view(dict(row), include_report=False) for row in rows]
        return {
            "tasks": views,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    def cancel(self, task_id):
        row = self._read(task_id)
        if self._effective(row) in TERMINAL:
            return self.view(row)
        with self._connection(True) as conn:
            conn.execute(
                "UPDATE sra_research_tasks SET cancel_requested=1, updated_at=? WHERE task_id=? AND cancel_requested=0",
                (now(), task_id),
            )
        row = self._read(task_id)
        view = self.view(row)
        view.setdefault("cancel_note", "已请求取消；将在当前阶段边界停止。已保存的材料不会静默删除。")
        return view

    def retry(self, task_id, *, re_search=False, resume=False):
        from contextlib import ExitStack
        with ExitStack() as ownership:
            return self._retry(task_id, re_search=re_search, resume=resume, ownership=ownership)

    def _retry(self, task_id, *, re_search=False, resume=False, ownership):
        """明确重试/补跑：复用可用的已保存阶段结果，不承诺外部调用 exactly-once。

        - partial 任务只允许「补跑」：沿用原查询计划，仅重试失败来源，
          不重跑已成功来源；请求 re_search=True 会被拒绝（需要重新检索时
          应发起新任务，不伪装成原任务重试）。
        - failed/interrupted/cancelled 的重试沿用既有语义。
        """
        row = self._read(task_id)
        from core.resource_limits import RESOURCES
        if RESOURCES.is_active(self._resource_key(task_id)):
            return self.view(row)
        effective = self._effective(row)
        stale_queued = row["status"] == "queued" and not task_lock_held(self.db_path)
        if effective == "partial":
            if re_search:
                raise TaskConflict(
                    "部分完成任务请使用补跑（只重试失败部分）；需要更换检索范围时请发起新任务"
                )
            resume = True
        elif effective not in ("failed", "interrupted", "cancelled") and not stale_queued:
            raise TaskConflict("当前状态不可重试；请先查看任务详情")
        if not row["candidates"] and not re_search and row["stage"] not in ("planning", "searching"):
            re_search = True  # 没有可复用候选时必须重新检索
        payload = json.loads(row["payload"])
        if re_search or resume or not row["candidates"]:
            from datetime import datetime,timezone
            latest_calls = {}
            for call in json.loads(row["source_calls"] or "[]"):
                latest_calls[(call.get("query"), call.get("source"))] = call
            for call in latest_calls.values():
                delay = call.get("retry_after_seconds")
                if delay and call.get("finished_at"):
                    until = datetime.fromisoformat(call["finished_at"]).timestamp()+delay
                    if until>datetime.now(timezone.utc).timestamp():
                        raise TaskConflict("来源仍在限流等待期，请稍后补跑；本次没有发出额外来源请求")
        payload["_re_search"] = bool(re_search) and effective != "partial"
        payload["_resume"] = bool(resume)
        stamp = now()
        with self._connection(True) as conn:
            current = self._row(conn, task_id)
            if RESOURCES.is_active(self._resource_key(task_id)):
                return self.view(current)
            # 事务内重判：并发双击时，第二次请求会看到第一次已转入 queued/running。
            cur_effective = self._effective(current)
            cur_stale = current["status"] == "queued" and not task_lock_held(self.db_path)
            allowed = (
                cur_effective == "partial"
                or cur_effective in ("failed", "interrupted", "cancelled")
                or cur_stale
            )
            if not allowed:
                raise TaskConflict("任务仍在运行，无需重试")
            if task_lock_held(self.db_path):
                raise TaskConflict("项目检索执行权被其他进程持有，请稍后重试")
            from core.resource_limits import RESOURCES
            ownership.enter_context(RESOURCES.acquire(key=self._resource_key(task_id)))
            conn.execute(
                "UPDATE sra_research_tasks SET status='queued', stage='planning', attempts=attempts+1, "
                "cancel_requested=0, error=NULL, error_code=NULL, owner_process=?, payload=?, updated_at=? "
                "WHERE task_id=?",
                (self.PROCESS_IDENTITY, encode(payload), stamp, task_id),
            )
        self._launch(task_id, row["attempts"] + 1, ownership)
        return self.view(self._read(task_id))
