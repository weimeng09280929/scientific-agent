"""Project SQLite call ledger. No prompts, keys, URLs or authentication headers.

Reservations count BEFORE I/O. SDK requests are measured by its actual client's
event hooks, not by monkey-patching a guessed transport library.
"""

from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from functools import wraps
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

ACTIVE = ContextVar("sra_call_audit", default=None)
LOGICAL = ContextVar("sra_logical_call", default=None)


class CallLimitError(RuntimeError):
    def __init__(self, message, reason="budget_exhausted", retry_after_seconds=None):
        super().__init__(message)
        self.reason, self.retry_after_seconds = reason, retry_after_seconds


def stamp():
    return datetime.now(timezone.utc).isoformat()


def inherited_policy(db: Path, operation_id: str):
    """Return a persisted enclosing limit, never a fresh allowance."""
    db = Path(db)
    if not db.is_file():
        return None
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT group_id FROM sra_call_audit WHERE operation_id=? ORDER BY started_at,id LIMIT 1",
            (operation_id,),
        ).fetchone() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sra_call_audit'"
        ).fetchone() else None
        if not row or row[0] == operation_id:
            return None
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_call_policies'").fetchone():
            # Historical upper-limit runs have no durable policy. Fail closed.
            return {"unavailable": True}
        saved = conn.execute(
            "SELECT policy FROM sra_call_policies WHERE group_id=?", (row[0],)
        ).fetchone()
        return {"group_id": row[0], **json.loads(saved[0])} if saved else {"unavailable": True}


def budget_state(db: Path, operation_id: str):
    """Read actual network reservations, including any enclosing group."""
    db = Path(db)
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_call_audit'").fetchone():
            return {"used": 0, "limit": 6, "remaining": 6, "parent_remaining": None}
        used = conn.execute(
            "SELECT COUNT(*) FROM sra_call_audit WHERE operation_id=? AND event='network' AND kind='model'",
            (operation_id,),
        ).fetchone()[0]
        policy = inherited_policy(db, operation_id)
        parent_remaining = None
        if policy:
            if policy.get("unavailable"):
                parent_remaining = 0
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM sra_call_audit WHERE group_id=? AND event='network' AND kind='model'",
                    (policy["group_id"],),
                ).fetchone()[0]
                parent_remaining = max(0, policy["limits"].get("model", 0) - total)
                return {"used": total, "limit": policy["limits"].get("model", 0),
                        "remaining": parent_remaining, "parent_remaining": parent_remaining,
                        "local_used": used}
        return {"used": used, "limit": 6, "remaining": 0 if parent_remaining == 0 else max(0, 6 - used),
                "parent_remaining": parent_remaining, "local_used": used}


@dataclass
class AuditScope:
    db: Path
    project_id: str
    operation_id: str
    conversation_id: str | None = None
    request_id: str | None = None
    task_id: str | None = None
    group_id: str | None = None
    limits: dict = field(default_factory=lambda: {"model": 6, "source": 12})
    # Explicit verified prices per million tokens, never inferred from a model name.
    pricing: dict | None = None
    cost_limit: float | None = None
    stop_on_model_failure: bool = False
    expected_model: str | None = None

    @contextmanager
    def connect(self, write=False):
        db = Path(self.db)
        if not db.is_file():
            raise FileNotFoundError("审计所属项目数据库不存在")
        with closing(
            sqlite3.connect(
                db.resolve().as_uri() + "?mode=" + ("rw" if write else "ro"),
                uri=True,
                timeout=15,
            )
        ) as conn:
            conn.row_factory = sqlite3.Row
            if write:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        """CREATE TABLE IF NOT EXISTS sra_call_audit (
                        id TEXT PRIMARY KEY, project_id TEXT, operation_id TEXT, group_id TEXT,
                        conversation_id TEXT, request_id TEXT, task_id TEXT, parent_id TEXT,
                        event TEXT, kind TEXT, name TEXT, started_at TEXT, finished_at TEXT,
                        elapsed_ms REAL, outcome TEXT, http_status INTEGER, usage TEXT,
                        cost REAL, cost_basis TEXT, reserved_cost REAL, retry_at REAL)"""
                    )
                    yield conn
            else:
                yield conn

    @contextmanager
    def activate(self):
        parent = ACTIVE.get()
        if parent and Path(parent.db).resolve() == Path(self.db).resolve():
            self.group_id = parent.group_id or parent.operation_id
            self.limits, self.pricing, self.cost_limit = (
                parent.limits,
                parent.pricing,
                parent.cost_limit,
            )
            self.stop_on_model_failure = parent.stop_on_model_failure
            self.expected_model = parent.expected_model
        token = ACTIVE.set(self)
        try:
            yield self
        finally:
            ACTIVE.reset(token)

    def begin(self, event, kind, name, *, reserved_cost=None, elapsed_before_start=0.0):
        identity = uuid4().hex
        with self.connect(True) as conn:
            group = self.group_id or self.operation_id
            if event == "network":
                # A parent authorization must survive a worker restart. A new
                # chat round may get its own local allowance, but may never
                # discard an enclosing user-set group/cost/model boundary.
                if group != self.operation_id:
                    conn.execute("""CREATE TABLE IF NOT EXISTS sra_call_policies (
                        group_id TEXT PRIMARY KEY, policy TEXT NOT NULL)""")
                    conn.execute(
                        "INSERT OR IGNORE INTO sra_call_policies VALUES (?,?)",
                        (group, json.dumps({"limits": self.limits, "pricing": self.pricing,
                                            "cost_limit": self.cost_limit,
                                            "stop_on_model_failure": self.stop_on_model_failure,
                                            "expected_model": self.expected_model})),
                    )
                    policy = json.loads(conn.execute(
                        "SELECT policy FROM sra_call_policies WHERE group_id=?", (group,)
                    ).fetchone()[0])
                    self.limits = policy["limits"]
                    self.pricing = policy["pricing"]
                    self.cost_limit = policy["cost_limit"]
                    self.stop_on_model_failure = policy["stop_on_model_failure"]
                    self.expected_model = policy["expected_model"]
                if (
                    kind == "model"
                    and self.expected_model
                    and name != self.expected_model
                ):
                    raise CallLimitError("模型与本次授权不一致，未发出调用", "model_mismatch")
                if (
                    self.stop_on_model_failure
                    and conn.execute(
                        "SELECT 1 FROM sra_call_audit WHERE group_id=? AND kind='model' AND outcome NOT IN ('success','processing') LIMIT 1",
                        (group,),
                    ).fetchone()
                ):
                    raise CallLimitError(
                        "本次真实验收已遇到模型失败，按约定停止后续调用", "parent_stopped"
                    )
                count = conn.execute(
                    "SELECT COUNT(*) FROM sra_call_audit WHERE group_id=? AND event='network' AND kind=?",
                    (group, kind),
                ).fetchone()[0]
                if count >= self.limits.get(kind, 0):
                    raise CallLimitError(
                        "本次请求的实际调用上限已达到；未发出额外请求，请核对调用记录"
                    )
                cooldown = conn.execute(
                    "SELECT MAX(retry_at) FROM sra_call_audit WHERE kind=? AND name=?",
                    (kind, name),
                ).fetchone()[0]
                if cooldown and cooldown > time.time():
                    raise CallLimitError(
                        "来源或模型要求等待后再试；未重复发送限流请求",
                        "rate_limited",
                        cooldown - time.time(),
                    )
                if kind == "model" and self.cost_limit is not None:
                    if reserved_cost is None:
                        raise CallLimitError(
                            "未取得可核验单价，无法保证费用上限，本次未发出模型请求", "cost_unknown"
                        )
                    used = conn.execute(
                        "SELECT COALESCE(SUM(reserved_cost),0) FROM sra_call_audit WHERE group_id=? AND event='network' AND kind='model'",
                        (group,),
                    ).fetchone()[0]
                    if used + reserved_cost > self.cost_limit:
                        raise CallLimitError(
                            "本次预留费用将超过授权上限，未发出模型请求", "cost_exhausted"
                        )
            conn.execute(
                "INSERT INTO sra_call_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    self.project_id,
                    self.operation_id,
                    group,
                    self.conversation_id,
                    self.request_id,
                    self.task_id,
                    LOGICAL.get(),
                    event,
                    kind,
                    str(name)[:150],
                    (
                        datetime.now(timezone.utc)
                        - timedelta(seconds=elapsed_before_start)
                    ).isoformat(),
                    None,
                    None,
                    "processing",
                    None,
                    None,
                    None,
                    "unknown",
                    reserved_cost,
                    None,
                ),
            )
        return identity, time.monotonic() - elapsed_before_start

    def finish(
        self,
        ticket,
        *,
        outcome="success",
        http_status=None,
        usage=None,
        retry_after=None,
    ):
        if ticket is None:
            return
        cost, basis = None, "unknown"
        if (
            usage
            and self.pricing
            and all(
                usage.get(k) is not None for k in ("prompt_tokens", "completion_tokens")
            )
        ):
            cost = (
                usage["prompt_tokens"] * self.pricing["input"]
                + usage["completion_tokens"] * self.pricing["output"]
            ) / 1_000_000
            basis = "estimate:" + self.pricing.get("basis", "explicit configuration")
        with self.connect(True) as conn:
            conn.execute(
                "UPDATE sra_call_audit SET finished_at=?,elapsed_ms=?,outcome=?,http_status=?,usage=?,cost=?,cost_basis=?,retry_at=? WHERE id=? AND outcome='processing'",
                (
                    stamp(),
                    round((time.monotonic() - ticket[1]) * 1000, 3),
                    outcome,
                    http_status,
                    json.dumps(usage) if usage is not None else None,
                    cost,
                    basis,
                    time.time() + retry_after if retry_after else None,
                    ticket[0],
                ),
            )

    @contextmanager
    def span(self, event, kind, name):
        ticket = self.begin(event, kind, name)
        logical_token = LOGICAL.set(ticket[0]) if event == "logical" else None
        try:
            yield
        except Exception as exc:
            self.finish(ticket, outcome=type(exc).__name__)
            raise
        else:
            self.finish(ticket)
        finally:
            if logical_token is not None:
                LOGICAL.reset(logical_token)


@contextmanager
def span(event, kind, name):
    scope = ACTIVE.get()
    if scope:
        with scope.span(event, kind, name):
            yield
    else:
        yield


def audited_model(fn):
    @wraps(fn)
    def call(self, task_type, *args, **kwargs):
        with span("logical", "model", task_type):
            return fn(self, task_type, *args, **kwargs)

    return call


def read_audit(db, project_id, *, operation_id=None, limit=100, offset=0):
    scope = AuditScope(Path(db), project_id, operation_id or "")
    with scope.connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sra_call_audit'"
        ).fetchone():
            return {"entries": [], "next_offset": None}
        where, args = "project_id=?", [project_id]
        if operation_id:
            where += " AND operation_id=?"
            args.append(operation_id)
        rows = conn.execute(
            f"SELECT * FROM sra_call_audit WHERE {where} ORDER BY started_at,id LIMIT ? OFFSET ?",
            (*args, min(100, limit) + 1, offset),
        ).fetchall()
        entries = []
        for row in rows[:limit]:
            item = dict(row)
            item["usage"] = json.loads(item["usage"]) if item["usage"] else None
            # Reading must not turn an in-flight or interrupted attempt into success.
            entries.append(item)
        return {
            "entries": entries,
            "next_offset": offset + limit if len(rows) > limit else None,
        }
