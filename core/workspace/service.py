"""研究记录、依赖检查和用户采纳；不把模型建议当作实验事实。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable

from core.knowledge import KnowledgeStore
from core.workspace.schema import (
    Area,
    Brief,
    GuideDraft,
    ItemInput,
    Message,
    ResearchFocus,
    SourceRef,
    Suggestion,
    WorkItem,
    Workspace,
    now,
)
from core.workspace.materials import (
    material_catalog,
    mark_changed_sources,
    experiment_result_payload,
    fingerprint,
)
from core.workspace.workflow import build_workflow

KEY = "research_workspace.v1"
AREA_LABELS = {
    "overview": "项目总览",
    "literature": "文献阅读",
    "question": "研究问题",
    "study": "方案与实验",
    "writing": "成果写作",
}
KIND_LABELS = {
    "note": "阅读与灵感笔记",
    "question": "研究问题",
    "plan": "研究方案",
    "result": "用户提交的实验结果",
    "finding": "用户确认的研究判断",
    "outline": "写作骨架",
}


class WorkspaceError(ValueError):
    pass


class RevisionConflict(WorkspaceError):
    pass


class WorkspaceService:
    def __init__(
        self, project_id: str, db_path: Path, *, initialize_store: bool = True
    ):
        self.project_id = project_id
        self.db_path = db_path
        # Explicit project creation owns schema initialization. HTTP reads and
        # preflight checks use a read-only material store; formal mutations still
        # use the separate compare-and-save transaction below.
        self.store = KnowledgeStore(db_path, read_only=not initialize_store)

    def load(self, *, catalog=None, include_chat=True) -> Workspace:
        data = self.store.get_kv(KEY)
        workspace = (
            Workspace.model_validate(data)
            if data is not None
            else Workspace(project_id=self.project_id)
        )
        if workspace.project_id != self.project_id:
            raise WorkspaceError("工作台与项目不匹配")
        mark_changed_sources(workspace, self.catalog() if catalog is None else catalog)
        if include_chat:
            from core.workspace.conversations import ConversationService

            ConversationService(self.project_id, self.db_path).project_legacy(workspace)
        else:
            workspace.messages = []
            workspace.suggestions = []
            workspace.chat_revision = 0
        return workspace

    def catalog(self) -> list[dict]:
        return material_catalog(self.store, self.db_path.parent)

    def workflow(self, workspace=None, execution=None) -> dict:
        catalog = self.catalog()
        return build_workflow(
            workspace or self.load(catalog=catalog, include_chat=False),
            self.store.list_papers(),
            catalog,
            execution,
        )

    def _mutate(
        self,
        expected_revision: int,
        change: Callable[[Workspace], None],
        *,
        sync_result=False,
    ) -> Workspace:
        # A SQLite write transaction guards compare-and-save across threads/processes.
        # No lock is held during a network/model call.
        with closing(sqlite3.connect(self.db_path, timeout=15)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (KEY,)
            ).fetchone()
            workspace = (
                Workspace.model_validate_json(row[0])
                if row
                else Workspace(project_id=self.project_id)
            )
            if workspace.project_id != self.project_id:
                raise WorkspaceError("工作台与项目不匹配")
            if workspace.revision != expected_revision:
                raise RevisionConflict(
                    "项目记录已有更新，请刷新后重新核对并提交；你的输入尚未保存"
                )
            mark_changed_sources(workspace, self.catalog())
            change(workspace)
            if sync_result:
                self._sync_bound_result(workspace, conn)
            workspace.revision += 1
            workspace.updated_at = now()
            conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES (?, ?, ?)",
                (KEY, workspace.model_dump_json(), workspace.updated_at),
            )
        return workspace

    def save_focus(self, focus: ResearchFocus, expected_revision: int) -> Workspace:
        def change(workspace):
            by_id = {i.item_id: i for i in workspace.items if i.status == "active"}
            question = by_id.get(focus.question)
            plan = by_id.get(focus.plan)
            if focus.question and (question is None or question.kind != "question"):
                raise WorkspaceError("所选研究问题不存在或已被修订")
            if focus.plan and (
                plan is None
                or plan.kind != "plan"
                or not question
                or question.item_id not in plan.related_ids
            ):
                raise WorkspaceError("所选方案不属于当前研究问题")
            workspace.focus = focus

        return self._mutate(expected_revision, change)

    def save_brief(self, brief: Brief, expected_revision: int) -> Workspace:
        def change(workspace: Workspace) -> None:
            if workspace.brief != brief:
                for item in workspace.items:
                    if item.status == "active" and item.kind != "note":
                        item.needs_review = True
            workspace.brief = brief

        return self._mutate(expected_revision, change)

    def validate_item(
        self, workspace: Workspace, data: ItemInput, *, _transaction=None
    ) -> None:
        if data.plan:
            if (
                data.kind != "plan"
                or data.plan.question_id not in data.related_ids
                or not set(data.plan.references) <= set(data.paper_ids)
            ):
                raise WorkspaceError("结构化方案类型、问题关联和引用必须与研究记录一致")
        if not data.title.strip() or not data.content.strip():
            raise WorkspaceError("标题和内容不能只包含空白")
        if len(data.paper_ids) != len(set(data.paper_ids)) or len(
            data.related_ids
        ) != len(set(data.related_ids)):
            raise WorkspaceError("来源和关联记录不能重复")
        # Case reference transfers insert sources and notes atomically. Read the
        # same transaction so ownership checks also see its uncommitted sources.
        papers = (
            {r[0] for r in _transaction.execute("SELECT paper_id FROM papers")}
            if _transaction is not None
            else {p.paper_id for p in self.store.list_papers()}
        )
        if not set(data.paper_ids) <= papers:
            raise WorkspaceError("引用的论文不在当前项目文献库中")
        by_id = {i.item_id: i for i in workspace.items}
        refs = [by_id.get(i) for i in data.related_ids]
        if any(i is None or i.status != "active" or i.needs_review for i in refs):
            raise WorkspaceError("关联记录不存在、已被替换或需要重新核对")
        if data.supersedes_id:
            old = by_id.get(data.supersedes_id)
            if old is None or old.status != "active" or old.kind != data.kind:
                raise WorkspaceError("只能修订当前有效的同类记录")
            if data.supersedes_id in data.related_ids:
                raise WorkspaceError("新版本不能依赖将被替换的旧版本")
            dependent_ids = {data.supersedes_id}
            while True:
                descendants = {
                    i.item_id
                    for i in workspace.items
                    if set(i.related_ids) & dependent_ids
                }
                if descendants <= dependent_ids:
                    break
                dependent_ids |= descendants
            if set(data.related_ids) & dependent_ids:
                raise WorkspaceError(
                    "新版本不能依赖其旧版本的下游记录，请先重新核对研究依据"
                )
        kinds = {i.kind for i in refs if i is not None}
        if data.kind == "plan" and "question" not in kinds:
            raise WorkspaceError("方案须关联一个已经保存的研究问题")
        if data.kind == "plan" and sum(i.kind == "question" for i in refs) != 1:
            raise WorkspaceError("一份方案只能对应一个明确研究问题，不能混用不同主线")
        if data.kind == "result":
            if "plan" not in kinds or data.result_status is None:
                raise WorkspaceError("实验结果须关联研究方案，并填写运行状态")
            if data.result_status == "completed" and not data.metrics:
                raise WorkspaceError(
                    "已完成的计算实验须填写实际指标；运行失败可以只记录原因"
                )
            if sum(i.kind == "plan" for i in refs) != 1:
                raise WorkspaceError("一条结果只能关联一份明确方案")
        elif data.metrics or data.result_status is not None:
            raise WorkspaceError("只有实验结果可以填写运行状态和指标")
        if data.kind == "finding":
            results = [i for i in refs if i is not None and i.kind == "result"]
            if not results or data.assessment is None:
                raise WorkspaceError(
                    "研究判断须关联实验结果，并选择支持、反驳或无法定论"
                )
            if data.assessment in ("supports", "refutes") and not any(
                i.result_status == "completed" for i in results
            ):
                raise WorkspaceError(
                    "运行失败或无法定论的实验不能用于确认支持或反驳设想"
                )
            plan_ids = {
                ref
                for result in results
                for ref in result.related_ids
                if by_id.get(ref) and by_id[ref].kind == "plan"
            }
            if len(plan_ids) > 1:
                raise WorkspaceError(
                    "一条判断不能混用不同方案的结果，请分别记录后再讨论"
                )
        elif data.assessment is not None:
            raise WorkspaceError("只有研究判断可以填写支持程度")

    def _append_item(
        self,
        workspace: Workspace,
        data: ItemInput,
        origin: str = "user",
        *,
        _transaction=None,
    ) -> WorkItem:
        self.validate_item(workspace, data, _transaction=_transaction)
        if data.supersedes_id:
            changed = {data.supersedes_id}
            for item in workspace.items:
                if item.item_id == data.supersedes_id:
                    item.status = "superseded"
            # Propagate stale dependencies instead of deleting earlier work.
            while True:
                affected = {
                    i.item_id
                    for i in workspace.items
                    if i.status == "active" and set(i.related_ids) & changed
                }
                if affected <= changed:
                    break
                changed |= affected
            for item in workspace.items:
                if item.item_id in changed and item.status == "active":
                    item.needs_review = True
        item = WorkItem(**data.model_dump(), origin=origin)
        if item.kind == "plan":
            # Existing supersedes lineage is the version authority, not a new
            # plan table. Never rewrite the earlier item or its content.
            version, cursor, seen = 1, data.supersedes_id, set()
            while cursor and cursor not in seen:
                seen.add(cursor)
                old = next((i for i in workspace.items if i.item_id == cursor), None)
                if not old:
                    break
                version += 1
                cursor = old.supersedes_id
            item.plan_version = version
        workspace.items.append(item)
        if item.kind == "question" and (
            workspace.focus.question == data.supersedes_id
            or not workspace.focus.question
            and sum(
                i.kind == "question" and i.status == "active" for i in workspace.items
            )
            == 1
        ):
            workspace.focus = ResearchFocus(question=item.item_id)
        if (
            item.kind == "plan"
            and workspace.focus.question in item.related_ids
            and (workspace.focus.plan == data.supersedes_id or not workspace.focus.plan)
        ):
            workspace.focus.plan = item.item_id
        return item

    def add_item(self, data: ItemInput, expected_revision: int) -> Workspace:
        return self._mutate(
            expected_revision,
            lambda w: self._append_item(w, data),
            sync_result=data.kind == "result",
        )

    def link_material(
        self,
        kind: str,
        source_id: str,
        source_fingerprint: str,
        related_id: str | None,
        expected_revision: int,
    ) -> Workspace:
        def change(workspace):
            source = next(
                (
                    s
                    for s in self.catalog()
                    if s["kind"] == kind and s["source_id"] == source_id
                ),
                None,
            )
            if (
                not source
                or not source["available"]
                or source.get("importable") is False
            ):
                raise WorkspaceError(
                    "来源不存在、正文为空或结果指标无效，不能关联；请先核对原材料"
                )
            if source["fingerprint"] != source_fingerprint:
                raise RevisionConflict("来源材料已有变化，请刷新并重新核对后关联")
            target_kind = {
                "idea": "question",
                "gap": "question",
                "method": "plan",
                "experiment_plan": "plan",
                "experiment_result": "result",
            }[kind]
            existing = next(
                (
                    i
                    for i in workspace.items
                    if i.status == "active"
                    and i.source_ref
                    and i.source_ref.kind == kind
                    and i.source_ref.source_id == source_id
                ),
                None,
            )
            if (
                existing
                and existing.source_ref.fingerprint == source_fingerprint
                and not existing.needs_review
            ):
                # One source record cannot silently move to a different branch.
                if related_id and related_id not in existing.related_ids:
                    raise WorkspaceError(
                        "该材料已关联到另一主线，请在原记录中明确修订，不重复计数"
                    )
                if existing.kind == "question":
                    workspace.focus = ResearchFocus(question=existing.item_id)
                elif existing.kind == "plan":
                    workspace.focus = ResearchFocus(
                        question=related_id or workspace.focus.question,
                        plan=existing.item_id,
                    )
                return
            refs = [related_id] if related_id else []
            data = {
                "kind": target_kind,
                "title": source["title"],
                "content": source["content"],
                "paper_ids": source["paper_ids"],
                "related_ids": refs,
                "supersedes_id": existing.item_id if existing else None,
            }
            if kind == "experiment_result":
                plan = next(
                    (i for i in workspace.items if i.item_id == related_id), None
                )
                if (
                    not plan
                    or not plan.source_ref
                    or plan.source_ref.kind != "experiment_plan"
                    or plan.source_ref.source_id != source_id
                ):
                    raise WorkspaceError(
                        "结果只能关联到同一实验任务的方案，不能把别的实验借作证据"
                    )
                data.update(
                    result_status=source["result_status"], metrics=source["metrics"]
                )
            item = self._append_item(
                workspace, ItemInput(**data), origin="linked_material"
            )
            item.source_ref = SourceRef(
                kind=kind, source_id=source_id, fingerprint=source_fingerprint
            )
            if item.kind == "question":
                workspace.focus = ResearchFocus(question=item.item_id)
            elif item.kind == "plan":
                workspace.focus = ResearchFocus(
                    question=related_id or "", plan=item.item_id
                )

        return self._mutate(expected_revision, change)

    def _sync_bound_result(self, workspace, conn) -> None:
        """One transaction for a linked task's result and its compatibility row.

        Only explicit result submission writes; reading, chatting and source
        adoption never mutate experiments or Claim verification state.
        """
        from datetime import datetime
        from core.knowledge.schema import ExperimentStatus

        item = workspace.items[-1]
        by_id = {i.item_id: i for i in workspace.items}
        plans = [
            by_id[ref]
            for ref in item.related_ids
            if ref in by_id and by_id[ref].kind == "plan"
        ]
        if (
            len(plans) != 1
            or not plans[0].source_ref
            or plans[0].source_ref.kind != "experiment_plan"
        ):
            return
        exp_id = plans[0].source_ref.source_id
        prior = [
            i
            for i in workspace.items[:-1]
            if i.status == "active"
            and i.source_ref
            and i.source_ref.kind == "experiment_result"
            and i.source_ref.source_id == exp_id
        ]
        if prior:
            raise WorkspaceError(
                "该任务已有结果，请修订原结果以保留版本，不能新增记录覆盖"
            )
        exp = self.store.get_experiment(exp_id)
        exp.status = {
            "completed": ExperimentStatus.COMPLETED,
            "failed": ExperimentStatus.FAILED,
            "inconclusive": ExperimentStatus.ANOMALY_DETECTED,
        }[item.result_status]
        exp.metrics = item.metrics
        exp.completed_at = datetime.now()
        exp.result_summary = "用户提交，未经系统独立复现：\n" + item.content
        exp.anomaly_notes = item.content if item.result_status != "completed" else None
        exp.config["user_claim_assessments"] = (
            {}
        )  # old assessments cannot survive new measurements
        conn.execute(
            "UPDATE experiments SET content=?,status=? WHERE experiment_id=?",
            (exp.model_dump_json(), exp.status.value, exp_id),
        )
        item.source_ref = SourceRef(
            kind="experiment_result",
            source_id=exp_id,
            fingerprint=fingerprint(experiment_result_payload(exp)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO kv_store (key,value,updated_at) VALUES (?,?,?)",
            ("writing.results_need_review", "true", now()),
        )

    def record_guide(
        self,
        area: Area,
        text: str,
        draft: GuideDraft,
        mode: str,
        expected_revision: int,
    ) -> Workspace:
        from core.workspace.conversations import ConversationService

        ConversationService(self.project_id, self.db_path).record_legacy_draft(
            area, text, draft, mode, expected_revision
        )
        return self.load()

    def apply_suggestion(self, suggestion_id: str, expected_revision: int) -> Workspace:
        from core.workspace.conversations import ConversationService

        conversations = ConversationService(self.project_id, self.db_path)
        conversations.apply(conversations.legacy_id, suggestion_id, expected_revision)
        return self.load()

    def plan_quality(self, item, *, needs_review=False):
        """Assess an already project-scoped record/draft without changing it."""
        from core.workspace.plan_quality import assess_plan
        from core.workspace.plan_schema import ResearchPlan

        data = item if isinstance(item, dict) else item.model_dump(mode="json")
        plan = ResearchPlan.model_validate(data["plan"]) if data.get("plan") else None
        workspace = self.load(include_chat=False)
        stale = needs_review or data.get("needs_review", False) or data.get("status", "active") != "active"
        if plan:
            question = next((i for i in workspace.items if i.item_id == plan.question_id and i.kind == "question"), None)
            stale = stale or not question or question.needs_review or question.status != "active" or question.item_id not in data.get("related_ids", [])
        papers = {p.paper_id: p for p in self.store.list_papers()}
        cited = {pid for section in plan.sections for st in section.statements for pid in st.paper_ids} if plan else set()
        chunks = {pid: self.store.get_paper_chunks(pid) for pid in cited if pid in papers}
        return assess_plan(plan, papers, chunks,
                           target_id=data.get("item_id") or data["suggestion_id"],
                           workspace_revision=workspace.revision, needs_review=bool(stale))

    def paper_summaries(self) -> list[dict]:
        return [
            {
                "paper_id": p.paper_id,
                "title": p.title,
                "year": p.year,
                "authors": p.authors,
                "abstract": (p.abstract or "")[:1800],
                "doi": p.doi,
                "url": p.url,
                "scope": "有摘要，未在工作台确认全文" if p.abstract else "仅元信息",
                "reading_scope": p.metadata.get("reading_scope"),
                "relevance": p.metadata.get("relevance", ""),
                "teaching": bool(p.metadata.get("teaching")),
            }
            for p in self.store.list_papers()
        ]

    def view(
        self, workspace: Workspace | None = None, *, workflow=None, include_chat=True
    ) -> dict:
        from core.workspace.plans import completeness

        workflow = workflow or self.workflow(workspace)
        workspace = workspace or self.load(
            catalog=workflow["materials"], include_chat=include_chat
        )
        active = [
            i for i in workspace.items if i.status == "active" and not i.needs_review
        ]
        counts = {kind: sum(i.kind == kind for i in active) for kind in KIND_LABELS}
        papers = self.paper_summaries()
        next_step = (
            "先记录目标、已有基础和可用资源，再围绕一个具体问题阅读文献。"
            if not workspace.brief.goal
            else (
                "先补充或导入相关论文；可以通过现有检索与导入页面操作。"
                if not papers
                else (
                    "结合论文中的具体局限，保存一个待检验的研究问题。"
                    if not counts["question"]
                    else (
                        "为当前问题制定方法方案，写清比较条件和支持／反驳标准。"
                        if not counts["plan"]
                        else "审阅、修改或导出方案；本轮不进入实验执行和后续写作。"
                    )
                )
            )
        )
        return {
            "plan_reviews": {
                i.item_id: completeness(i.plan)
                for i in workspace.items
                if i.kind == "plan"
            },
            "workspace": workspace.model_dump(mode="json"),
            "papers": papers,
            "counts": counts,
            "next_step": workflow["continuation"].get("hint", next_step),
            "workflow": workflow,
            "product_stage": workflow["product_stage"],
            "product_boundary": workflow["product_boundary"],
            "needs_review_count": sum(
                i.status == "active" and i.needs_review for i in workspace.items
            ),
            "capabilities": {
                "records": "已接入",
                "guide": "已接入：按系统配置使用模型或规则指导",
                "literature": "复用现有论文库；检索与导入使用现有页面",
                "reading": "阅读指导已接入；自动逐段精读待迁入",
                "experiments": "本轮止于研究方案，不开放实验执行与结果新增；历史记录可查看、导出",
                "writing": "本轮不进入论文写作；历史稿件、提纲和版本仍可查看、导出",
            },
        }

    def export_markdown(self) -> str:
        from core.workspace.plans import METHODS, export_plan_content

        workspace = self.load()
        papers = {p.paper_id: p for p in self.store.list_papers()}
        records = {i.item_id: i for i in workspace.items}
        name = self.store.get_kv("project.display_name", "") or self.store.get_kv("research.topic", "未命名研究项目")
        lines = [
            "# 科研工作台记录",
            "",
            f"项目：{name} · 材料版本：{workspace.revision}",
            "",
        ]
        flow = self.workflow(workspace)["flow"]
        lines.extend(
            [
                "当前研究问题："
                + (flow["question"]["title"] if flow["question"] else "待选择"),
                "当前方案：" + (flow["plan"]["title"] if flow["plan"] else "待选择"),
                "说明：保存记录与生成稿件不代表实验已经执行或结论已验证。",
                "",
            ]
        )
        for field, label in (
            ("goal", "目标"),
            ("background", "已有基础"),
            ("resources", "资源"),
            ("time_budget", "时间"),
        ):
            lines.append(f"- {label}：{getattr(workspace.brief, field) or '尚未填写'}")
        for item in workspace.items:
            lines.extend(
                [
                    "",
                    f"## {KIND_LABELS[item.kind]}：{item.title}",
                    "",
                    f"状态：{'当前记录' if item.status == 'active' else '已被修订的历史记录'} · {'待复核' if item.needs_review else '未发现资料变更'}",
                    "",
                    export_plan_content(item, {pid: str(n) for n, pid in enumerate(item.paper_ids, 1)}),
                ]
            )
            if item.kind == "plan":
                lines.extend(
                    [
                        "",
                        f"方案版本：{item.plan_version if item.plan_version is not None else '历史记录，未标版'}；修订自：{records[item.supersedes_id].title if item.supersedes_id in records else '历史记录已不可用' if item.supersedes_id else '无'}",
                    ]
                )
                if item.plan:
                    lines.append(
                        f"方案类型：{METHODS[item.plan.method]}；关联问题：{records[item.plan.question_id].title if item.plan.question_id in records else '待核对'}"
                    )
            if item.result_status:
                lines.extend(
                    [
                        "",
                        f"用户提交的运行状态：{item.result_status}",
                        json.dumps(item.metrics, ensure_ascii=False),
                    ]
                )
            if item.assessment:
                lines.extend(["", f"用户判断（未经系统独立验证）：{item.assessment}"])
            if item.related_ids:
                lines.extend(["", "关联记录：" + "、".join(records[rid].title if rid in records else "记录已不可用" for rid in item.related_ids)])
            if item.source_ref:
                lines.extend(
                    [
                        "",
                        "来源材料：已关联的项目资料；保留原始记录状态。",
                    ]
                )
            for n, pid in enumerate(item.paper_ids, 1):
                p = papers.get(pid)
                lines.append(
                    f"\n[{n}] 来源：{p.title if p else '来源未找到，请核对项目资料'}；DOI：{p.doi if p and p.doi else '未提供'}"
                )
        return "\n".join(lines)
