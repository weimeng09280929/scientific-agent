"""Read-only projections of existing materials and user operations, not new state.

No schema setup, migrations, model calls or recovery writes are allowed here.
"""
import json

from core.research_tasks.service import ResearchTaskService
from core.research_tasks.report_status import report_status


def material_counts(chat):
    """Count saved reports, not candidate questions or report versions."""
    with chat._connection() as conn:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_research_tasks'").fetchone()
        count = conn.execute("SELECT COUNT(*) FROM sra_research_tasks WHERE project_id=? AND report IS NOT NULL AND report!='null'",(chat.project_id,)).fetchone()[0] if has else 0
    return {"research_reports": count + int(bool(chat.shared.store.get_kv('cross_validation_report')))}


def search_audits(chat, *, limit=10, offset=0):
    """Bounded, read-only projection of saved conversation retrieval facts.

    This does not backfill the legacy evidence log or reinterpret saved reports.
    Counts are per task: they must not be summed as unique library papers.
    """
    with chat._connection() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'sra_research_tasks', 'sra_conversations'} <= tables:
            return {"tasks": [], "next_offset": None}
        rows = conn.execute(
            "SELECT r.* FROM sra_research_tasks r JOIN sra_conversations c "
            "ON c.conversation_id=r.conversation_id AND c.project_id=r.project_id "
            "WHERE r.project_id=? ORDER BY r.created_at DESC,r.task_id DESC LIMIT ? OFFSET ?",
            (chat.project_id, limit+1, offset),
        ).fetchall()
    service = ResearchTaskService(chat.project_id, chat.db_path, chat)
    result = []
    keys = ('task_id', 'conversation_id', 'topic', 'status', 'status_label', 'stage',
            'created_at', 'source_calls', 'candidates_count', 'included_count',
            'excluded_count', 'saved_papers_count', 'failed_sources', 'error')
    for row in rows[:limit]:
        view = service.view(dict(row), include_report=False)
        report = json.loads(row['report']) if row['report'] else None
        result.append({**{k:view.get(k) for k in keys},
                       'coverage': report.get('coverage') if isinstance(report, dict) else None,
                       'has_report': bool(report)})
    return {"tasks": result, "next_offset": offset+limit if len(rows)>limit else None}


def progress(workflow):
    flow = workflow["flow"]
    review = flow.get("next", {}).get("action") == "revise"
    label = ("材料待复核" if review else "方案可审阅" if flow.get("planReady")
             else "已确认问题，待完善方案" if flow.get("question")
             else "待确认研究问题")
    return {"label": label, "planReady": flow.get("planReady") is True,
            "needs_review": review, "product_stage": workflow["product_stage"],
            "next": flow.get("next"), "notice": "进入讨论阶段不等于已经确认；保存方案不表示结论已验证。"}


def reports(chat, base):
    """All saved report versions, using the task's existing export identity."""
    with chat._connection() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_research_tasks'").fetchone():
            return []
        columns={r[1] for r in conn.execute('PRAGMA table_info(sra_research_tasks)')}
        versions='report_versions' in columns
        # Read only version metadata, not all report bodies, source logs or
        # call audits. No silent first-100-tasks cutoff in the paged directory.
        fields="r.task_id,r.conversation_id,r.topic,r.status,r.basis_changed,r.created_at,r.finished_at"
        if versions:
            fields+=",json_extract(v.value,'$.version') AS version,json_extract(v.value,'$.status') AS version_status,json_extract(v.value,'$.delivered_at') AS delivered_at,json_extract(r.report_versions,'$[#-1].version') AS current_version"
        # Extract only scalar content metadata. A historical entry must never
        # fall back to the latest task's mode or failure note.
        mode = "COALESCE(json_extract(v.value,'$.report.mode'),json_extract(v.value,'$.mode'))" if versions else 'NULL'
        note = "json_extract(v.value,'$.synthesis_note')" if versions else 'NULL'
        kind = "json_extract(v.value,'$.report.catalog_kind')" if versions else 'NULL'
        task_note = 'r.synthesis_note' if 'synthesis_note' in columns else 'NULL'
        has_version = "v.value IS NOT NULL" if versions else '0'
        task_mode = 'r.mode' if 'mode' in columns else 'NULL'
        fields+=f",CASE WHEN {has_version} THEN {mode} ELSE COALESCE(json_extract(r.report,'$.mode'),{task_mode}) END AS content_mode"
        fields+=f",CASE WHEN {has_version} THEN {note} ELSE {task_note} END AS content_note"
        fields+=f",CASE WHEN {has_version} THEN {kind} ELSE json_extract(r.report,'$.catalog_kind') END AS catalog_kind"
        join=" LEFT JOIN json_each(CASE WHEN json_valid(r.report_versions) THEN r.report_versions ELSE '[]' END) v" if versions else ''
        rows = [dict(r) for r in conn.execute(f"SELECT {fields} FROM sra_research_tasks r{join} WHERE r.project_id=? AND r.report IS NOT NULL AND r.report!='null' ORDER BY r.created_at DESC,r.task_id DESC"+(",v.key DESC" if versions else ''), (chat.project_id,))]
    result = []
    for task in rows:
        version = task.get("version")
        latest = version == task.get("current_version") or version is None
        status = task.get("version_status") or task["status"]
        content = report_status(status,task.get("content_mode"),task.get("content_note"),task.get("catalog_kind"))
        content["status_label"] = ("待复核 · " if task["basis_changed"] else "") + content["status_label"]
        result.append({"key": f"research-task:{task['task_id']}"+(f":{version}" if not latest else ""),
                "project_id": chat.project_id, "task_id": task["task_id"],
                "conversation_id": task["conversation_id"], "title": "研究现状整理："+task["topic"],
                "kind": "report", "label": "对话检索报告" if latest else "历史报告版本",
                "group": "current" if latest else "history", "version": version,
                "created_at": task.get("delivered_at") or task["finished_at"] or task["created_at"],
                "status": status, "basis_changed": bool(task["basis_changed"]),
                **content,
                "review_label": "待复核" if task["basis_changed"] else "",
                "url": base+"/research-tasks/"+task["task_id"]+"/export"+(f"?version={version}" if version is not None else ""),
                "formats": ["md"]})
    return result


def drafts(chat, limit=20, offset=0):
    """Project-visible plan drafts; discussion histories remain isolated."""
    with chat._connection() as conn:
        if not chat._has_tables(conn):
            rows = []
            legacy = chat.list(limit=1)["conversations"]
            if legacy:
                rows = [s for s in chat.suggestions(legacy[0]["conversation_id"]) if s["kind"] == "plan" and not s.get("applied_item_id")]
            return rows[offset:offset+limit], offset+limit if offset+limit<len(rows) else None
        rows = conn.execute("SELECT s.data,s.material_fingerprint,s.conversation_id FROM sra_chat_suggestions s JOIN sra_conversations c ON c.conversation_id=s.conversation_id AND c.project_id=s.project_id WHERE s.project_id=? AND json_extract(s.data,'$.kind')='plan' AND json_extract(s.data,'$.applied_item_id') IS NULL ORDER BY s.rowid DESC LIMIT ? OFFSET ?", (chat.project_id,limit+1,offset)).fetchall()
    basis = chat.material_state()["fingerprint"] if rows else None
    return [{**json.loads(r["data"]), "conversation_id":r["conversation_id"],
             "material_fingerprint":r["material_fingerprint"],
             "basis_changed":not r["material_fingerprint"] or r["material_fingerprint"] != basis} for r in rows[:limit]], offset+limit if len(rows)>limit else None


def activities(chat, execution, *, limit=20, offset=0):
    """One user operation per row; phases/audit attempts are details, not tasks."""
    with chat._connection() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        queries, params = [], []
        if "sra_chat_requests" in tables:
            queries.append("SELECT 'chat' AS kind,r.request_id AS id,r.conversation_id,r.created_at AS sort_at FROM sra_chat_requests r JOIN sra_conversations c ON c.conversation_id=r.conversation_id AND c.project_id=r.project_id WHERE r.project_id=?")
            params.append(chat.project_id)
        if "sra_research_tasks" in tables:
            queries.append("SELECT 'search' AS kind,r.task_id AS id,r.conversation_id,r.created_at AS sort_at FROM sra_research_tasks r JOIN sra_conversations c ON c.conversation_id=r.conversation_id AND c.project_id=r.project_id WHERE r.project_id=?")
            params.append(chat.project_id)
        rows = conn.execute(" UNION ALL ".join(queries)+" ORDER BY sort_at DESC,id DESC LIMIT ? OFFSET ?", (*params,limit+1,offset)).fetchall() if queries else []
    result=[]
    task_service = ResearchTaskService(chat.project_id,chat.db_path,chat)
    for row in rows[:limit]:
        if row["kind"] == "search":
            item = task_service.get(row["id"])
            result.append({"kind":"search", "id":row["id"], "title":"检索与整理："+item["topic"], **{k:item.get(k) for k in ("status","stage","status_label","error","created_at","finished_at","conversation_id","request_id")},
                "result_url":f"/api/projects/{chat.project_id}/research-tasks/{row['id']}/export" if item.get("report") else None})
        else:
            with chat._connection() as conn:
                original=chat._request(conn,row["conversation_id"],row["id"])
                item=chat._effective(original)
                payload=json.loads(original["payload"])
                phase=conn.execute("SELECT kind FROM sra_call_audit WHERE project_id=? AND operation_id=? AND event='phase' ORDER BY started_at DESC LIMIT 1",(chat.project_id,"chat:"+row["conversation_id"]+":"+row["id"])).fetchone() if "sra_call_audit" in tables else None
            is_plan=bool(payload.get("plan_request")) or bool(phase and phase[0]=="plan")
            result.append({"kind":"plan" if is_plan else "guidance", "id":row["id"], "title":"方案生成请求" if is_plan else "科研指导请求", "conversation_id":row["conversation_id"], "request_id":row["id"], **{k:item.get(k) for k in ("status","created_at","updated_at","error","attempt")}})
    return {"activities":result,"next_offset":offset+limit if len(rows)>limit else None,
            "historical_execution": execution, "project_id":chat.project_id}
