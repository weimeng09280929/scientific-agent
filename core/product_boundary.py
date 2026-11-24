"""Read-only product boundary. Runtime mode is deliberately not an authorization."""
from __future__ import annotations

from enum import Enum
from contextlib import closing
import json
import sqlite3

from core.state.lifecycle import LifecycleStage, StageStatus


class ExecutionPolicy(Enum):
    PRODUCT_PLAN = "product_plan"
    INTERNAL_LEGACY = "internal_legacy"  # Explicit in-process architecture fixtures only.


NOTICE = "当前产品止于研究方案；实验、结果回填、研究判断和后续写作未开放，历史材料仍可查看、导出。"
POST_PLAN_KINDS = frozenset(("result", "finding", "outline"))


def node_stage(node_id=None, node_type=None):
    kind = str(node_type or "")
    if kind.startswith(("experiment_", "writing_")):
        return kind.split("_", 1)[0]
    if node_id in {"config_gen", "experiment_config", "cp_before_run", "experiment_review", "code_generate", "code_review", "experiment_run", "anomaly_check", "claim_verify", "outcome_assess", "experiment_outcome_assess"}:
        return "experiment"
    if node_id in {"provenance_check", "outline", "style_adapt", "section_draft", "paper_review", "revise", "final_save"}:
        return "writing"
    return None


def inspect_boundary(paths, project_id, *, session=None, ctx=None, pending=None, observed_status=None, observed_history=None):
    """Inspect existing files without constructing stores, repairing state or creating dirs."""
    saved, statuses = {}, {}
    try:
        db = paths.project_db(project_id)
        if db.is_file():
            with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                row = conn.execute("SELECT value FROM kv_store WHERE key=?", ("pipeline.context.v1",)).fetchone()
                saved = json.loads(row[0]) if row else {}
                if not isinstance(saved, dict) or not isinstance(saved.get("data", {}), dict) or not isinstance(saved.get("history", []), list) or any(not isinstance(h, dict) for h in saved.get("history", [])):
                    raise ValueError("Invalid checkpoint shape")
                outcome = saved.get("data", {}).get("experiment.outcome")
                if outcome is not None and not isinstance(outcome, dict):
                    raise ValueError("Invalid outcome shape")
        index = paths.project_snapshots(project_id) / "index.json"
        if index.is_file():
            entries = json.loads(index.read_text(encoding="utf-8"))
            if entries:
                sid = str(entries[-1]["snapshot_id"])
                if not sid or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in sid):
                    raise ValueError("Invalid snapshot id")
                snapshot = json.loads((index.parent / (sid + ".json")).read_text(encoding="utf-8"))
                statuses = {key: value["status"] for key, value in snapshot["stage_states"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
        return {"max_stage":"design", "restricted":True, "reason":"checkpoint_unreadable", "message":"历史进度暂时无法核对，未启动或修改项目；请先检查检查点。", "stage_statuses":statuses, "history":[]}
    if session is not None:
        # A stale in-memory session must not erase an already-entered disk stage.
        for stage in LifecycleStage.ordered():
            current = session.status_of(stage).value
            if current != StageStatus.NOT_STARTED.value and statuses.get(stage.value) != StageStatus.DONE.value:
                statuses[stage.value] = current
    positions = [saved]
    if observed_history:
        positions.append({"history":observed_history})
    if ctx is not None:
        positions.append({"current_stage":ctx.current_stage,"current_node_id":ctx.current_node_id,"history":ctx.history()})
    restricted_stage = None
    if saved.get("data", {}).get("experiment.outcome"):
        restricted_stage = "experiment"
    if any(saved.get("data", {}).get(key) for key in ("writing.outline", "writing.draft_content", "writing.final_artifact_id")):
        restricted_stage = "writing"
    if observed_status in ("experiment_pending","experiment_failed"):
        restricted_stage="experiment"
    for stage in ("experiment", "writing"):
        if statuses.get(stage, "not_started") != StageStatus.NOT_STARTED.value:
            restricted_stage = stage
    for position in positions:
        history = position.get("history") or []
        last = history[-1] if history else {}
        found = position.get("current_stage") if position.get("current_stage") in ("experiment", "writing") else node_stage(position.get("current_node_id") or last.get("node_id"), last.get("node_type"))
        restricted_stage = found or restricted_stage
        for entry in history:
            restricted_stage = node_stage(entry.get("node_id"),entry.get("node_type")) or restricted_stage
    if pending:
        context = pending.get("context") or {}
        restricted_stage = node_stage(context.get("node_id"), context.get("node_type")) or restricted_stage
    return {"max_stage":"design", "restricted":bool(restricted_stage),
            "reason":"legacy_stage_restricted" if restricted_stage else "design_generated" if statuses.get("design")=="done" else "within_product",
            "message":NOTICE if restricted_stage else "研究流程最多推进到研究方案；方案需由用户核对与审阅。",
            "execution_stage":restricted_stage or saved.get("current_stage") or "", "stage_statuses":statuses,
            "history":saved.get("history") or []}


def public_boundary(boundary):
    return {key:value for key,value in boundary.items() if key not in ("history", "stage_statuses")}


def historical_stage(boundary):
    statuses=boundary["stage_statuses"]
    for stage in reversed(LifecycleStage.ordered()):
        if statuses.get(stage.value) in ("in_progress","pending_review","blocked"):
            return stage.value
    return next((s.value for s in LifecycleStage.ordered() if statuses.get(s.value,"not_started")=="not_started"),"writing")


class ProductExecutionRestricted(ValueError):
    def __init__(self, boundary):
        self.boundary = public_boundary(boundary)
        super().__init__(boundary["message"])
