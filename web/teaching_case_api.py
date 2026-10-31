"""Teaching endpoints adapt the existing project/conversation services."""

import sqlite3
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from core.workspace.conversations import (
    ConversationService,
    ConversationNotFound,
    ConversationStorageError,
)
from core.workspace.service import WorkspaceError, RevisionConflict
from core.workspace.start import ConversationStarter
from core.workspace.teaching import (
    CaseStart,
    CaseChoice,
    CaseFork,
    TeachingService,
    start_case,
    fork_case,
)
from core.workspace.teaching_packages import catalog


def build_teaching_router(configuration, require_project, register_project, owner_id=None):
    router = APIRouter(prefix="/api", tags=["teaching-cases"])
    cfg = lambda: configuration() if callable(configuration) else configuration

    def starter():
        return ConversationStarter(cfg().paths, require_project, register_project,
                                   owner_id=owner_id() if owner_id else None)

    def service(pid):
        require_project(pid)
        return TeachingService(ConversationService(pid, cfg().paths.project_db(pid)))

    def invoke(fn):
        try:
            return fn()
        except WorkspaceError as exc:
            raise HTTPException(
                (
                    404
                    if isinstance(exc, ConversationNotFound)
                    else (
                        409
                        if isinstance(exc, RevisionConflict)
                        else 503 if isinstance(exc, ConversationStorageError) else 422
                    )
                ),
                str(exc),
            ) from exc
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(
                503, "教学内容暂时无法保存，请用原 request_id 核对重试"
            ) from exc

    @router.get("/teaching-cases")
    def directory():
        return {"cases": catalog(), "offline": True}

    @router.post("/teaching-cases/start")
    def start(request: CaseStart):
        return invoke(lambda: start_case(starter(), request))

    base = "/projects/{project_id}/conversations/{conversation_id}/teaching"

    @router.get(base)
    def progress(project_id: str, conversation_id: str):
        return invoke(lambda: service(project_id).view(conversation_id))

    @router.post(base + "/choices")
    def choose(project_id: str, conversation_id: str, request: CaseChoice):
        return invoke(lambda: service(project_id).choose(conversation_id, request))

    @router.post(base + "/fork")
    def fork(project_id: str, conversation_id: str, request: CaseFork):
        return invoke(
            lambda: fork_case(
                starter(), service(project_id).chat, conversation_id, request
            )
        )

    @router.get(base + "/export")
    def export(project_id: str, conversation_id: str):
        text = invoke(lambda: service(project_id).export(conversation_id))
        return Response(
            text,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="teaching-case.md"'},
        )

    return router
