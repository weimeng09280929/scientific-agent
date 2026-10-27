"""新工作台的独立接口；不触发旧 Pipeline 或执行实验。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field

from core.llm import LLMRegistry
from core.knowledge.schema import ArtifactType
from core.knowledge.store import StoreError
from core.workspace.guide import (
    STARTERS,
    build_guide_context,
    generate_guidance,
    rule_guidance,
)
from core.workspace.schema import Area, Brief, Contract, ItemInput, ResearchFocus
from core.workspace.service import RevisionConflict, WorkspaceError, WorkspaceService
from core.research_scope import normalize_public_domain, is_public_domain, SCOPE_NOTICE
from core.product_boundary import NOTICE, POST_PLAN_KINDS
from core.workspace.conversations import (
    ConversationService,
    ConversationNotFound,
    ConversationStorageError,
)
from core.workspace.conversation_schema import ConversationSend
from core.research_tasks.service import ResearchTaskService, SearchTaskRequest
from core.research_tasks.detect import classify_search_request
from web.research_task_api import build_research_task_router
from web.conversation_api import build_conversation_router, result_response
from core.workspace.plan_quality import PlanQualityReport

logger = logging.getLogger(__name__)


class BriefRequest(Contract):
    expected_revision: int = Field(ge=0)
    brief: Brief


class ItemRequest(Contract):
    expected_revision: int = Field(ge=0)
    item: ItemInput


class ApplyRequest(Contract):
    expected_revision: int = Field(ge=0)


class FocusRequest(Contract):
    expected_revision: int = Field(ge=0)
    focus: ResearchFocus


class LinkMaterialRequest(Contract):
    expected_revision: int = Field(ge=0)
    kind: Literal["idea", "gap", "method", "experiment_plan", "experiment_result"]
    source_id: str = Field(min_length=1, max_length=200)
    fingerprint: str = Field(min_length=64, max_length=64)
    related_id: str | None = None
    confirmed: Literal[True]


class GuideRequest(Contract):
    expected_revision: int = Field(ge=0)
    area: Area
    message: str = Field(min_length=1, max_length=6000)
    selected_item_id: str | None = None
    selected_paper_ids: list[str] = Field(default_factory=list, max_length=3)
    reading_purpose: Literal[
        "orientation", "method", "comparison", "innovation", "troubleshooting"
    ] = "orientation"
    request_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"\S"
    )


def build_workspace_router(
    config, require_project, registry_factory=None, workflow_provider=None
) -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["research-workspace"])

    def service_for(project_id: str):
        project = require_project(project_id)
        db_path = config.paths.project_db(project_id)
        if not db_path.is_file():
            raise HTTPException(
                status_code=404, detail="项目数据库不存在，未创建空白数据替代历史项目"
            )
        service = WorkspaceService(project_id, db_path, initialize_store=False)
        # Creation owns identity persistence; browsing historical records must not repair them.
        return service, project

    def view(service, project, workspace=None, *, include_chat=True):
        from core.workspace.teaching import teaching_identity
        from core.workspace.read_model import material_counts

        workflow = workflow_provider(project) if workflow_provider else None
        result = {
            **service.view(workspace, workflow=workflow, include_chat=include_chat),
            "project": {
                "topic": project.topic,
                "domain": project.domain,
                "display_name": getattr(project, "display_name", "") or project.topic,
            },
            "guide_mode": "rules" if config.dry_run else "model",
            "material_counts": material_counts(ConversationService(service.project_id,service.db_path)),
            "teaching_reference": service.store.get_kv("teaching.reference.v1"),
            "teaching": teaching_identity(
                ConversationService(service.project_id, service.db_path)
            ),
            "starters": STARTERS,
            "research_scope": {
                "supported": is_public_domain(project.domain),
                "notice": SCOPE_NOTICE,
            },
        }
        return result

    def fail(exc):
        raise HTTPException(
            status_code=(
                503
                if isinstance(exc, ConversationStorageError)
                else (
                    404
                    if isinstance(exc, ConversationNotFound)
                    else 409 if isinstance(exc, RevisionConflict) else 422
                )
            ),
            detail=str(exc),
        ) from exc

    def generator(project):
        def generate(area, text, context, purpose):
            from core.knowledge.domain_profile import domain_label

            context["domain"] = project.domain
            context["domain_label"] = domain_label(project.domain)
            if config.dry_run:
                return rule_guidance(area, text, context), "rules"
            registry = (
                registry_factory()
                if registry_factory
                else LLMRegistry.from_config(config)
            )
            return (
                generate_guidance(
                    registry, area, project.domain, text, context, purpose
                ),
                "model",
            )

        return generate

    # ---- 第五步：对话驱动的检索任务 ----

    def research_model_fn(project):
        """规则模式返回 None；模型模式经注册表调用（测试可注入 registry_factory）。"""
        if config.dry_run:
            return None
        registry = None

        def model_fn(kind, payload):
            # Listing saved tasks is read-only and must work without model keys.
            nonlocal registry
            if registry is None:
                registry = (
                    registry_factory()
                    if registry_factory
                    else LLMRegistry.from_config(config)
                )
            response = registry.complete("research_" + kind, prompt=payload["prompt"])
            return {"text": response.text}

        return model_fn

    def task_factory(project):
        db_path = config.paths.project_db(project.project_id)
        if not db_path.is_file():
            raise HTTPException(
                status_code=404, detail="项目数据库不存在，未创建空白数据替代历史项目"
            )
        conversations = ConversationService(project.project_id, db_path)
        return ResearchTaskService(
            project.project_id,
            db_path,
            conversations,
            model_fn=research_model_fn(project),
        )

    def task_starter(project, conversation_id, request, intent):
        """send 接口的检索分流：明确请求直接启动；意向不明确交付澄清卡片。"""
        tasks = task_factory(project)
        if intent.complete:
            start_request = SearchTaskRequest(
                content=request.content,
                topic=intent.topic,
                year_from=intent.year_from,
                year_to=intent.year_to,
                request_id=request.request_id,
            )
            view = tasks.start(conversation_id, start_request)
            return {
                "status": "search_started",
                "task_id": view["task_id"],
                "request_id": view["request_id"],
                "task": view,
            }
        return tasks.clarify(conversation_id, request, intent)

    router.include_router(
        build_research_task_router(config, require_project, task_factory)
    )
    router.include_router(
        build_conversation_router(
            config, require_project, generator, task_starter=task_starter
        )
    )

    @router.get("/{project_id}/workspace")
    def get_workspace(project_id: str, include_chat: bool = True):
        service, project = service_for(project_id)
        return view(service, project, include_chat=include_chat)

    @router.get("/{project_id}/workspace/plan-quality", response_model=PlanQualityReport)
    def plan_quality(
        project_id: str,
        item_id: str | None = Query(default=None, min_length=1, max_length=200),
        conversation_id: str | None = Query(default=None, min_length=1, max_length=200),
        suggestion_id: str | None = Query(default=None, min_length=1, max_length=200),
    ):
        if (item_id is None) == (suggestion_id is None) or bool(conversation_id) != bool(suggestion_id):
            raise HTTPException(422, "请指定一份正式方案，或指定同一会话中的方案草案")
        try:
            service, _ = service_for(project_id)
            if item_id:
                item = next((i for i in service.load(include_chat=False).items if i.item_id == item_id and i.kind == "plan"), None)
                if item is None:
                    raise ConversationNotFound("方案不存在或不属于当前项目")
                return service.plan_quality(item)
            chat = ConversationService(project_id, service.db_path)
            draft = next((s for s in chat.suggestions(conversation_id) if s["suggestion_id"] == suggestion_id and s["kind"] == "plan"), None)
            if draft is None:
                raise ConversationNotFound("方案草案不存在或不属于当前会话")
            return service.plan_quality(draft, needs_review=bool(draft.get("basis_changed", True)))
        except (WorkspaceError, ConversationNotFound, ConversationStorageError) as exc:
            fail(exc)
        except (StoreError, sqlite3.Error) as exc:
            raise HTTPException(503, "方案依据暂不可读取，原方案未改写，请核对资料后重试") from exc

    @router.get("/{project_id}/research-materials")
    def research_materials(project_id: str):
        """汇集已有思路与稿件，只读取真实存量，不启动模型或流水线。"""
        service, _ = service_for(project_id)
        latest = {}
        for artifact in service.store.list_artifacts():
            if artifact.artifact_type not in (
                ArtifactType.PAPER_DRAFT,
                ArtifactType.REVIEW_NOTE,
            ):
                continue
            previous = latest.get(artifact.artifact_group)
            if previous is None or artifact.version > previous.version:
                latest[artifact.artifact_group] = artifact
        root = config.paths.project_artifacts(project_id).resolve()
        artifacts = []
        for artifact in sorted(
            latest.values(), key=lambda a: a.created_at, reverse=True
        ):
            content = artifact.content
            issue = None
            if content is None and artifact.content_path:
                path = Path(artifact.content_path)
                if not path.is_absolute():
                    path = config.paths.root / path
                path = path.resolve()
                # 不允许元数据将浏览接口变成任意文件读取入口。
                if not path.is_relative_to(root) or path.suffix.lower() not in (
                    ".txt",
                    ".md",
                ):
                    issue = "产物路径不在当前项目文本产物目录，未读取"
                else:
                    try:
                        with path.open("r", encoding="utf-8") as stream:
                            content = stream.read(200001)
                    except (OSError, UnicodeError):
                        issue = "已有文件暂时无法读取，请检查文件是否存在及编码"
            artifacts.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "title": artifact.title,
                    "artifact_type": artifact.artifact_type.value,
                    "version": artifact.version,
                    "created_at": artifact.created_at.isoformat(),
                    "content": (content or "")[:200000],
                    "available": bool(content),
                    "truncated": len(content or "") > 200000,
                    "issue": issue
                    or (None if content else "只有产物元信息，尚无可阅读的正文"),
                }
            )
        return {
            "ideas": [i.model_dump(mode="json") for i in service.store.list_ideas()],
            "artifacts": artifacts,
        }

    @router.put("/{project_id}/workspace/brief")
    def save_brief(project_id: str, request: BriefRequest):
        service, project = service_for(project_id)
        try:
            workspace = service.save_brief(request.brief, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.post("/{project_id}/workspace/items")
    def add_item(project_id: str, request: ItemRequest):
        if request.item.kind in POST_PLAN_KINDS:
            require_project(project_id)
            raise HTTPException(status_code=422, detail=NOTICE)
        service, project = service_for(project_id)
        if (
            request.item.kind == "result"
            and getattr(project, "thread", None) is not None
            and project.thread.is_alive()
        ):
            raise HTTPException(
                status_code=409,
                detail="后台任务仍在运行或等待确认，结果尚未保存；请在任务结束后回填，避免覆盖正在使用的实验记录。",
            )
        try:
            workspace = service.add_item(request.item, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.put("/{project_id}/workspace/focus")
    def save_focus(project_id: str, request: FocusRequest):
        service, project = service_for(project_id)
        try:
            workspace = service.save_focus(request.focus, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.post("/{project_id}/workspace/link-material")
    def link_material(project_id: str, request: LinkMaterialRequest):
        if request.kind == "experiment_result":
            require_project(project_id)
            raise HTTPException(status_code=422, detail=NOTICE)
        service, project = service_for(project_id)
        try:
            workspace = service.link_material(
                request.kind,
                request.source_id,
                request.fingerprint,
                request.related_id,
                request.expected_revision,
            )
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.post("/{project_id}/workspace/guide")
    def guide(project_id: str, request: GuideRequest):
        project = require_project(project_id)
        if request.area == "writing":
            raise HTTPException(status_code=422, detail=NOTICE)
        try:
            normalize_public_domain(project.domain)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        service, project = service_for(project_id)
        if not request.message.strip():
            raise HTTPException(status_code=422, detail="请输入需要讨论的问题")
        try:
            conversations = ConversationService(
                project_id, config.paths.project_db(project_id)
            )
            payload = {
                "content": request.message,
                **request.model_dump(exclude={"message", "request_id"}),
            }
            if request.request_id is not None:
                payload["request_id"] = request.request_id
            result = conversations.send(
                conversations.legacy_id,
                ConversationSend(**payload),
                project.topic,
                generator(project),
                legacy=True,
            )
        except WorkspaceError as exc:
            fail(exc)
        if result["status"] != "completed":
            return result_response(result)
        return {
            **view(service, project),
            "conversation_id": result["conversation_id"],
            "request_id": result["request_id"],
            "request_status": result["status"],
        }

    @router.post("/{project_id}/workspace/suggestions/{suggestion_id}/apply")
    def apply(project_id: str, suggestion_id: str, request: ApplyRequest):
        reader, project = service_for(project_id)
        candidate = next(
            (
                item
                for item in reader.load().suggestions
                if item.suggestion_id == suggestion_id
            ),
            None,
        )
        if candidate is not None and candidate.kind in POST_PLAN_KINDS:
            raise HTTPException(status_code=422, detail=NOTICE)
        service = reader
        try:
            workspace = service.apply_suggestion(
                suggestion_id, request.expected_revision
            )
        except WorkspaceError as exc:
            fail(exc)
        return view(service, project, workspace)

    @router.get("/{project_id}/workspace/export")
    def export(project_id: str, format: Literal["md", "json"] = "md"):
        service, _ = service_for(project_id)
        content = (
            service.export_markdown()
            if format == "md"
            else service.load().model_dump_json(indent=2)
        )
        from core.workspace.teaching import export_header, teaching_identity

        teaching_chat = ConversationService(project_id, service.db_path)
        if format == "md":
            content = export_header(teaching_chat) + content
        elif teaching_identity(teaching_chat):
            import json

            content = json.dumps(
                {**json.loads(content), "teaching": teaching_identity(teaching_chat)},
                ensure_ascii=False,
                indent=2,
            )
        return Response(
            content=content,
            media_type="text/markdown" if format == "md" else "application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{project_id}-workspace.{format}"'
            },
        )

    return router
