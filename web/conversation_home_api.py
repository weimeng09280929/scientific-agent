"""Small HTTP additions for the main application; no alternative chat backend."""

from contextlib import closing
import json
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator

from core.research_scope import PUBLIC_DOMAINS
from core.workspace.schema import Contract, now
from core.workspace.start import ConversationStarter, StartConversation
from core.workspace.conversations import ConversationNotFound, ConversationStorageError
from core.workspace.paper_reading import PaperReadingTargetError, single_paper_target
from core.workspace.service import WorkspaceError, RevisionConflict


class ProjectName(Contract):
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("display_name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("项目名称不能为空白")
        return value.strip()


class ReadingTargetCheck(Contract):
    project_id: str | None = Field(default=None, max_length=120)
    content: str = Field(min_length=1, max_length=6000)
    selected_paper_ids: list[str] = Field(default_factory=list, max_length=3)


def build_home_router(configuration, require_project, register_project, owner_id=None):
    router = APIRouter(prefix="/api", tags=["conversation-home"])
    config = lambda: configuration() if callable(configuration) else configuration
    from web.teaching_case_api import build_teaching_router

    teaching = build_teaching_router(configuration, require_project, register_project, owner_id=owner_id)
    # Both routers already include /api: extend rather than add a second prefix.
    router.routes.extend(teaching.routes)

    @router.get("/conversation-capabilities")
    def capabilities():
        return {
            "version": 1,
            "project_conversations": True,
            "durable_start": True,
            "message_tail": True,
            "conversation_moves": bool(owner_id and owner_id()),
            "catalog_navigation": True,
            "conversation_search": True,
            "project_exports": True,
            "research_guidance": True,
            "research_discussion_route": True,
            "research_plan_templates": True,
            "background_plan": True,
            "upload_request_identity": True,
            "call_audit": True,
            "workspace_read_model": True,
            "research_methods": [
                {"value": "computational", "label": "计算研究"},
                {"value": "theoretical", "label": "理论研究"},
                {"value": "experimental", "label": "实验研究设计（仅规划）"},
            ],
            "research_search": True,
            "automatic_search": False,
            "case_content": True,
            "teaching_cases": True,
            "teaching_experience": 1,
            "max_stage": "design",
            "domains": [{"value": k, "label": v} for k, v in PUBLIC_DOMAINS.items()],
        }

    @router.post("/conversations/reading-target")
    def check_reading_target(request: ReadingTargetCheck):
        # Used before an attachment is uploaded. No model, material or draft
        # writes happen here. The message route resolves the target again.
        from core.knowledge import KnowledgeStore

        papers = []
        if request.project_id:
            require_project(request.project_id)
            papers = KnowledgeStore(config().paths.project_db(request.project_id), read_only=True).list_papers()
        try:
            single_paper_target(papers, request.selected_paper_ids, request.content, pending_attachment=True)
        except PaperReadingTargetError as exc:
            raise HTTPException(422, detail={"code": "paper_reading_target", "message": str(exc)}) from exc
        return {"ok": True}

    @router.post("/conversations/start")
    def start(request: StartConversation):
        try:
            return ConversationStarter(
                config().paths, require_project, register_project,
                owner_id=owner_id() if owner_id else None,
            ).start(request)
        except WorkspaceError as exc:
            from core.workspace.conversation_moves import ConversationMoved
            if isinstance(exc,ConversationMoved):
                from web.conversation_move_api import moved_error
                moved_error(exc,config().paths,require_project)
            code = (
                404
                if isinstance(exc, ConversationNotFound)
                else (
                    503
                    if isinstance(exc, ConversationStorageError)
                    else 409 if isinstance(exc, RevisionConflict) else 422
                )
            )
            raise HTTPException(code, str(exc)) from exc
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(
                503, "开始对话的存储暂时不可用，请保留原 request_id 后核对重试"
            ) from exc

    @router.patch("/projects/{project_id}/identity")
    def rename(project_id: str, request: ProjectName):
        state = require_project(project_id)
        db = config().paths.project_db(project_id)
        if not db.is_file():
            raise HTTPException(404, "项目数据库不存在，未创建空库替代原项目")
        try:
            with (
                closing(
                    sqlite3.connect(db.resolve().as_uri() + "?mode=rw", uri=True)
                ) as conn,
                conn,
            ):
                conn.execute(
                    "INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES (?,?,?)",
                    (
                        "project.display_name",
                        json.dumps(request.display_name, ensure_ascii=False),
                        now(),
                    ),
                )
        except sqlite3.Error as exc:
            raise HTTPException(503, "项目名称暂时无法保存，请稍后重试") from exc
        state.display_name = request.display_name
        return {"project_id": project_id, "display_name": request.display_name}

    return router
