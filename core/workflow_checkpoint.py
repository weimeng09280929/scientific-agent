"""只持久化科研域数据，不序列化客户端、凭据或内存检查点。"""
from __future__ import annotations

import json
from core.orchestration.context import ExecutionContext

KEY = "pipeline.context.v1"
PREFIXES = ("research.", "ideation.", "design.", "experiment.", "writing.", "topic_discovery.", "discovery.")


def save_context(ctx: ExecutionContext, store, dry_run: bool) -> None:
    snapshot = ctx.snapshot()
    snapshot["data"] = {
        key: value for key, value in snapshot["data"].items()
        if key.startswith(PREFIXES) or key == "project.domain"
    }
    snapshot["dry_run"] = dry_run
    # 失败时明确报错，不把不可恢复的内容标记为已经保存。
    json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    store.save_kv(KEY, snapshot)


def restore_context(ctx: ExecutionContext, store, dry_run: bool) -> bool:
    snapshot = store.get_kv(KEY)
    if not snapshot:
        return False
    if snapshot.get("project_id") != ctx.project_id:
        raise ValueError("执行材料与当前项目不匹配")
    if snapshot.get("dry_run") is not dry_run:
        raise ValueError("项目的演练/真实模式已改变，不能把演练产物作为真实材料继续；请新建项目")
    snapshot["data"] = {
        key: value for key, value in snapshot.get("data", {}).items()
        if key.startswith(PREFIXES) or key == "project.domain"
    }
    ctx.restore(snapshot)
    return True
