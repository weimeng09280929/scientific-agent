"""Discover actual project outputs and export exact records/versions, read-only."""

from pathlib import Path
from contextlib import closing
import sqlite3
from urllib.parse import quote
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from core.knowledge.store import StoreError
from core.workspace.service import WorkspaceService, WorkspaceError
from core.workspace.conversations import ConversationService, ConversationNotFound
from core.workspace.plans import METHODS, export_plan_content


def artifact_text(artifact, root):
    if artifact.content is not None:
        return str(artifact.content)
    if artifact.content_path:
        path = Path(artifact.content_path)
        if not path.is_absolute():
            local = root / path
            original = root.parent.parent.parent / path
            path = local if local.is_file() else original
        path = path.resolve()
        if path.is_relative_to(root.resolve()) and path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                pass
    return ""


def build_exports_router(
    configuration,
    require_project,
    workflow_provider,
    legacy_download,
    make_download,
    docx,
    pdf,
):
    router = APIRouter(prefix="/api/projects", tags=["project-exports"])
    config = lambda: configuration() if callable(configuration) else configuration

    def research_task_entries(pid, base):
        from core.workspace.read_model import reports
        return reports(ConversationService(pid, config().paths.project_db(pid)), base)

    def read(pid):
        state = require_project(pid)
        paths = config().paths
        db = paths.project_db(pid)
        if not db.is_file():
            raise HTTPException(404, "项目数据库不存在，未创建空库")
        service = WorkspaceService(pid, db, initialize_store=False)
        return paths, state, service

    def render(content, name, fmt):
        if fmt == "docx":
            return docx(content, name)
        if fmt == "pdf":
            return pdf(content, name)
        return make_download(content, name + ".md", "text/markdown")

    def inventory(pid, conversation_id=None):
        paths, state, service = read(pid)
        workspace = service.load(include_chat=False)
        workflow = workflow_provider(state)
        flow = workflow["flow"]
        base = f"/api/projects/{quote(pid, safe='')}"
        entries = []
        current_plan = flow.get("plan")
        from core.workspace.teaching import teaching_identity

        teaching = teaching_identity(ConversationService(pid, service.db_path))
        if teaching:
            entries.append(
                {
                    "key": "teaching-sources",
                    "title": "教学来源清单、已采纳记录与待讨论事项",
                    "group": "current",
                    "label": "教学产物（非实验结果）",
                    "version": teaching["version"],
                    "status_label": "仅已采纳问题/方案；预置分支不作为完成产物",
                    "url": base
                    + "/conversations/"
                    + quote(teaching["conversation_id"], safe="")
                    + "/teaching/export",
                    "formats": ["md"],
                }
            )
        if current_plan:
            ready = flow.get("planReady") is True
            entries.append(
                {
                    "key": "plan:" + current_plan["item_id"],
                    "title": current_plan["title"],
                    "group": "current",
                    "label": "当前正式方案",
                    "version": (
                        f"v{current_plan['plan_version']}"
                        if current_plan.get("plan_version") is not None
                        else "历史未标版"
                    )
                    + " · "
                    + current_plan["item_id"][:8],
                    "status_label": (
                        "可审阅（未验证）" if ready else "待复核／待确认关联"
                    ),
                    "recommended": ready,
                    "url": base
                    + "/exports/items/"
                    + quote(current_plan["item_id"], safe=""),
                    "formats": ["md", "docx", "pdf"],
                }
            )
        if workspace.items:
            entries.append(
                {
                    "key": "records",
                    "title": "当前项目研究记录（含状态与来源）",
                    "group": "current",
                    "label": "研究记录",
                    "version": f"材料版本 {workspace.revision}",
                    "status_label": "保留待复核、历史及来源标记",
                    "url": base + "/workspace/export",
                    "formats": ["md", "json"],
                }
            )
        for item in workspace.items:
            if item.kind == "plan" and (
                not current_plan or item.item_id != current_plan["item_id"]
            ):
                entries.append(
                    {
                        "key": "plan:" + item.item_id,
                        "title": item.title,
                        "group": "history" if item.status == "superseded" else "other",
                        "label": (
                            "历史方案版本"
                            if item.status == "superseded"
                            else "其他已保存方案"
                        ),
                        "version": (
                            f"v{item.plan_version}"
                            if item.plan_version is not None
                            else "历史未标版"
                        ),
                        "status_label": f"{item.status}；需复核：{item.needs_review}",
                        "recommended": False,
                        "url": base + "/exports/items/" + quote(item.item_id, safe=""),
                        "formats": ["md", "docx", "pdf"],
                    }
                )
        if conversation_id:
            chat = ConversationService(pid, paths.project_db(pid))
            chat.get(conversation_id)
            for s in chat.suggestions(conversation_id, limit=50, recent=True):
                if s["kind"] != "plan" or s.get("applied_item_id"):
                    continue
                entries.append(
                    {
                        "key": "draft:" + s["suggestion_id"],
                        "title": s["title"],
                        "group": "other",
                        "label": "当前会话未采纳讨论稿",
                        "version": "未采纳，尚未成为正式版本",
                        "status_label": (
                            "草案待复核"
                            if s.get("basis_changed")
                            else "待用户明确采纳；非当前正式方案"
                        ),
                        "recommended": False,
                        "url": base
                        + "/conversations/"
                        + quote(conversation_id, safe="")
                        + "/suggestions/"
                        + quote(s["suggestion_id"], safe="")
                        + "/export",
                        "formats": ["md", "docx", "pdf"],
                    }
                )
        store = service.store
        historical_report = store.get_kv("cross_validation_report")
        if historical_report:
            with closing(sqlite3.connect(service.db_path.resolve().as_uri()+"?mode=ro",uri=True)) as conn:
                saved = conn.execute("SELECT updated_at FROM kv_store WHERE key='cross_validation_report'").fetchone()
            entries.append(
                {
                    "key": "research-report",
                    "project_id": pid,
                    "title": "研究现状汇总",
                    "label": "历史流水线研究现状报告",
                    "group": "current",
                    "version": None,
                    "saved_at": saved[0] if saved else None,
                    "status_label": "待复核" if isinstance(historical_report,dict) and historical_report.get('needs_review') else "已有历史材料，覆盖范围请核对；非验证结论",
                    "url": base + "/download/research-report?preserve_status=true",
                    "formats": ["md", "docx", "pdf"],
                }
            )
        entries.extend(research_task_entries(pid, base))
        artifacts = sorted(
            store.list_artifacts(),
            key=lambda a: (a.created_at, a.version, a.artifact_id),
            reverse=True,
        )
        latest = {}
        for a in artifacts:
            if (
                a.artifact_group not in latest
                or a.version > latest[a.artifact_group].version
            ):
                latest[a.artifact_group] = a
        readable = {
            a.artifact_id: artifact_text(a, paths.project_artifacts(pid))
            for a in artifacts
        }
        readable_latest = {}
        for a in artifacts:
            previous = readable_latest.get(a.artifact_group)
            if readable[a.artifact_id].strip() and (
                previous is None or a.version > previous.version
            ):
                readable_latest[a.artifact_group] = a
        candidates = [
            a for a in readable_latest.values() if a.artifact_type.value == "method_doc"
        ]
        latest_method = (
            max(candidates, key=lambda a: (a.created_at, a.artifact_id)).artifact_id
            if candidates
            else None
        )
        source = current_plan.get("source_ref") if current_plan else None
        for a in artifacts:
            if not readable[a.artifact_id].strip():
                continue
            historical = a.artifact_type.value in (
                "experiment_result",
                "paper_draft",
                "review_note",
            ) or a.source_stage in ("experiment", "writing")
            current = (
                a.artifact_type.value == "method_doc" and a.artifact_id == latest_method
            )
            linked = (
                source
                and source.get("kind") == "method"
                and source.get("source_id") == a.artifact_group
                and latest[a.artifact_group].artifact_id == a.artifact_id
            )
            label = (
                "最新可读取版本（更新版本为空或缺失）"
                if current and latest[a.artifact_group].artifact_id != a.artifact_id
                else (
                    "历史版本"
                    if latest[a.artifact_group].artifact_id != a.artifact_id
                    else "最新版本"
                )
            )
            status = (
                "历史产物 · 非本轮新增成果"
                if historical
                else (
                    "已关联当前方案 · "
                    + ("可审阅（未验证）" if flow.get("planReady") else "待复核")
                    if linked
                    else "已有文档，未确认为当前正式方案"
                )
            )
            entries.append(
                {
                    "key": "artifact:" + a.artifact_id,
                    "title": a.title,
                    "group": (
                        "history" if historical else "current" if current else "other"
                    ),
                    "label": label,
                    "version": f"v{a.version} · {a.artifact_group[:8]}",
                    "created_at": a.created_at.isoformat(),
                    "status_label": status,
                    "recommended": bool(linked and flow.get("planReady")),
                    "url": base + "/exports/artifacts/" + quote(a.artifact_id, safe=""),
                    "formats": ["md", "docx", "pdf"],
                }
            )
        if (paths.project_dir(pid) / "experiments" / "README.md").is_file():
            entries.append(
                {
                    "key": "experiment-tasks",
                    "title": "历史实验任务说明书",
                    "group": "history",
                    "status_label": "历史计划，不代表已执行",
                    "url": base + "/download/experiment-tasks?preserve_status=true",
                    "formats": ["md"],
                }
            )
        for kind, title, exists in [
            ("experiment-results", "历史实验记录", bool(store.list_experiments())),
            ("claims-summary", "历史论断与证据", bool(store.list_claims())),
            ("ideas-summary", "已有研究思路", bool(store.list_ideas())),
            (
                "discovery-report",
                "历史发现材料",
                bool(
                    store.get_kv("discovery_report_content")
                    or store.get_kv("discovery_summary")
                ),
            ),
        ]:
            if exists:
                entries.append(
                    {
                        "key": kind,
                        "title": title,
                        "group": "other" if kind == "ideas-summary" else "history",
                        "status_label": "已有材料，保留原始状态",
                        "url": base + "/download/" + kind + "?preserve_status=true",
                        "formats": ["md", "docx", "pdf"],
                    }
                )
        return entries

    @router.get("/{project_id}/workspace/materials")
    def materials(
        project_id: str,
        kind: Literal["reports", "plans", "history"] = "reports",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    ):
        from core.workspace.read_model import drafts, progress, material_counts
        try:
            _, state, service = read(project_id)
            workflow = workflow_provider(state)
            chat = ConversationService(project_id, service.db_path)
            entries = inventory(project_id)
            if kind == "reports":
                entries = [e for e in entries if e["key"] == "research-report" or e["key"].startswith("research-task:")]
            elif kind == "plans":
                entries = [e for e in entries if e["key"].startswith("plan:")]
            else:
                entries = [e for e in entries if e.get("group") == "history" or e["key"].startswith("artifact:")]
            proposals, draft_next = drafts(chat, limit, offset) if kind == "plans" else ([], None)
            reference = service.store.get_kv("teaching.reference.v1")
            return {"project_id": project_id, "project_name": state.display_name or state.topic,
                    "kind": kind, "workflow": workflow, "progress": progress(workflow),
                    "material_counts": material_counts(chat),
                    "entries": entries[offset:offset+limit],
                    "next_offset": offset+limit if offset+limit < len(entries) else None,
                    "drafts": proposals, "draft_next_offset": draft_next,
                    "teaching_reference": reference}
        except ConversationNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (WorkspaceError, sqlite3.Error, StoreError) as exc:
            raise HTTPException(503, "项目材料暂不可读取，未自动修复数据") from exc

    @router.get("/{project_id}/activities")
    def activity_list(project_id: str,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0):
        from core.workspace.read_model import activities, progress, material_counts
        try:
            _, state, service = read(project_id)
            workflow = workflow_provider(state)
            return {**activities(ConversationService(project_id, service.db_path),
                                {"status": state.status, **workflow.get("execution", {})},limit=limit,offset=offset),
                    "workflow": workflow, "progress": progress(workflow),
                    "project_name": state.display_name or state.topic,
                    "material_counts":material_counts(ConversationService(project_id,service.db_path))}
        except ConversationNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (WorkspaceError, sqlite3.Error, StoreError) as exc:
            raise HTTPException(503, "任务记录暂不可读取，未自动恢复或启动任务") from exc

    @router.get("/{project_id}/exports")
    def exports(
        project_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
        conversation_id: Annotated[str | None, Query(max_length=200)] = None,
    ):
        try:
            entries = inventory(project_id, conversation_id)
        except ConversationNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (WorkspaceError, sqlite3.Error, StoreError) as exc:
            raise HTTPException(503, "项目材料暂不可读取，未自动修复数据") from exc
        return {
            "project_id": project_id,
            "scope": "当前项目及其选定的正式方案，不仅是本段聊天",
            "entries": entries[offset : offset + limit],
            "next_offset": offset + limit if offset + limit < len(entries) else None,
            "format_notice": "Markdown 保留公式源码；Word/PDF 为现有转换，复杂公式和表格可能受限，可用原页面公式预览与打印。",
        }

    @router.get("/{project_id}/exports/items/{item_id}")
    def item_export(
        project_id: str, item_id: str, format: Literal["md", "docx", "pdf"] = "md"
    ):
        _, state, service = read(project_id)
        w = service.load(include_chat=False)
        item = next((i for i in w.items if i.item_id == item_id), None)
        if item is None:
            raise HTTPException(404, "研究记录不存在或不属于当前项目")
        flow = workflow_provider(state)["flow"]
        is_current = (flow.get("plan") or {}).get("item_id") == item_id
        status = (
            "当前正式方案，可审阅，尚未验证"
            if is_current and flow.get("planReady")
            else "待复核／未选为有效当前方案" if is_current else "历史／其他研究记录"
        )
        papers = {p.paper_id: p for p in service_papers(project_id)}
        labels = {pid: str(n) for n, pid in enumerate(item.paper_ids, 1)}
        body = export_plan_content(item, labels)
        content = f"# {item.title}\n\n状态：{status}；{'待复核' if item.needs_review else '未发现已标记的资料变更'}\n\n"
        if item.kind == "plan":
            content += plan_identity(state, w, item) + "\n\n"
        else:
            content += f"项目名称：{getattr(state, 'display_name', '') or state.topic}\n\n"
            related = [i.title for i in w.items if i.item_id in item.related_ids]
            if related:
                content += "关联记录：" + "；".join(related) + "\n\n"
        content += "> 保存不代表实验已执行或结论得到验证。原文中的草稿、教学、预期与历史标记均保留。\n\n"
        from core.workspace.teaching import export_header

        content += export_header(ConversationService(project_id, service.db_path), compact=item.kind == "plan") + body
        content += reference_list(item, papers)
        if item.kind == "plan":
            content += "\n\n" + plan_review(state, item)
        if item.source_ref:
            content += "\n\n来源：已关联的项目资料；保留原始记录状态。"
        return render(
            content,
            f"research-plan-v{item.plan_version or 'legacy'}" if item.kind == "plan" else "research-record",
            format,
        )

    def plan_identity(state, workspace, item, draft=False):
        questions = [
            i
            for i in workspace.items
            if i.item_id in item.related_ids and i.kind == "question"
        ]
        name = getattr(state, "display_name", "") or state.topic
        text = f"项目名称：{name}\n\n研究问题：" + "；".join(
            q.title + "：" + q.content for q in questions
        )
        previous = next((i for i in workspace.items if i.item_id == item.supersedes_id), None)
        previous_label = (f"{previous.title}（版本 {previous.plan_version or '历史未标版'}）" if previous
                          else "历史方案（关联记录已不可用）" if item.supersedes_id else "无")
        text += f"\n\n方案类型：{METHODS[item.plan.method] if item.plan else '历史未标注'}；版本：{item.plan_version if item.plan_version is not None else '讨论稿，尚无正式版本' if draft else '历史未标版'}；修订自：{previous_label}"
        if item.plan:
            text += "\n\n待确认：" + "；".join(item.plan.unconfirmed)
        return text

    def reference_list(item, papers):
        if not item.paper_ids:
            return ""
        lines = ["\n\n## 参考文献"]
        for n, pid in enumerate(item.paper_ids, 1):
            p = papers.get(pid)
            lines.append(f"[{n}] {p.title}；DOI：{p.doi or '未提供'}" + (f"；来源链接：{p.url}" if p.url else "") if p else f"[{n}] 来源未找到，请核对项目资料。")
        return "\n\n".join(lines)

    def plan_review(state, item):
        from core.workspace.plan_quality import render_quality

        service = read(state.project_id)[2]
        quality = service.plan_quality(item, needs_review=getattr(item, "needs_review", False))
        return render_quality(quality)

    def service_papers(pid):
        return read(pid)[2].store.list_papers()

    @router.get(
        "/{project_id}/conversations/{conversation_id}/suggestions/{suggestion_id}/export"
    )
    def draft_export(
        project_id: str,
        conversation_id: str,
        suggestion_id: str,
        format: Literal["md", "docx", "pdf"] = "md",
    ):
        _, state, service = read(project_id)
        try:
            chat = ConversationService(project_id, service.db_path)
            s = next(
                (
                    s
                    for s in chat.suggestions(conversation_id)
                    if s["suggestion_id"] == suggestion_id and s["kind"] == "plan"
                ),
                None,
            )
        except ConversationNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        if not s:
            raise HTTPException(404, "方案草案不存在或不属于当前会话")
        from core.workspace.schema import ItemInput, WorkItem

        data = ItemInput(
            **{
                k: s[k]
                for k in (
                    "kind",
                    "title",
                    "content",
                    "paper_ids",
                    "related_ids",
                    "supersedes_id",
                    "plan",
                )
                if k in s
            }
        )
        item = WorkItem(**data.model_dump())
        item.needs_review = bool(s.get("basis_changed", True))
        w = service.load(include_chat=False)
        status = (
            "已采纳的原始草案（请在正式记录查看版本）"
            if s.get("applied_item_id")
            else "未采纳讨论稿，非当前正式方案"
        )
        content = f"# {s['title']}\n\n状态：{status}；依据{'待复核' if s.get('basis_changed', True) else '未发现变更'}\n\n" + plan_identity(state, w, item, draft=True) + "\n\n"
        from core.workspace.teaching import export_header

        labels = {pid: str(n) for n, pid in enumerate(item.paper_ids, 1)}
        papers = {p.paper_id: p for p in service_papers(project_id)}
        content += export_header(chat, compact=True) + export_plan_content(item, labels)
        content += reference_list(item, papers) + "\n\n" + plan_review(state, item)
        return render(
            content,
            "research-plan-draft",
            format,
        )

    @router.get("/{project_id}/exports/artifacts/{artifact_id}")
    def artifact_export(
        project_id: str, artifact_id: str, format: Literal["md", "docx", "pdf"] = "md"
    ):
        paths, _, service = read(project_id)
        try:
            artifact = service.store.get_artifact(artifact_id)
        except StoreError:
            raise HTTPException(404, "产物版本不存在或不属于当前项目")
        text = artifact_text(artifact, paths.project_artifacts(project_id))
        if not text.strip():
            raise HTTPException(
                404, "该版本内容缺失或不在当前项目产物目录，未生成替代文档"
            )
        entry = next(
            (e for e in inventory(project_id) if e["key"] == "artifact:" + artifact_id),
            None,
        )
        if entry is None:
            raise HTTPException(
                404, "产物版本已不可读取，请刷新目录后核对；未生成替代文档"
            )
        state = read(project_id)[1]
        origin = "规则或离线演示（未验证）" if any(marker in (artifact.created_by or "").lower() for marker in ("rules", "demo", "fixture", "simulat")) else "项目原始文档（未经独立验证）"
        content = f"# {artifact.title}\n\n项目名称：{getattr(state, 'display_name', '') or state.topic}\n\n版本：{artifact.version}\n\n状态：{entry['status_label']} · {entry['label']}\n\n来源说明：{origin}\n\n创建时间：{artifact.created_at.isoformat()}\n\n> 保留原文状态，不代表新实验或已验证结论。\n\n---\n\n{text}"
        return render(
            content,
            f"research-report-v{artifact.version}",
            format,
        )

    return router
