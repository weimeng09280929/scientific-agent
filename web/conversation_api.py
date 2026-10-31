"""Versioned project conversations; no research execution or layout changes."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from core.research_scope import normalize_public_domain
from core.research_tasks.detect import classify_search_request
from core.research_tasks.service import TaskConflict as _TaskConflict
from core.research_tasks.service import TaskError as _TaskError
from core.research_tasks.service import TaskNotFound as _TaskNotFound
from core.workspace.service import WorkspaceError, RevisionConflict
from core.workspace.conversations import (
    ConversationService,
    ConversationNotFound,
    ConversationStorageError,
)
from core.workspace.conversation_schema import (
    ConversationCreate,
    ConversationPatch,
    ConversationSend,
    ConversationRetry,
    ConversationRestart,
    ConversationApply,
)


def result_response(result):
    status = result["status"]
    code = (
        202
        if status in ("processing", "search_started")
        else 200 if status == "completed" else result.get("error_code") or 409
    )
    return JSONResponse(
        status_code=code,
        content={
            **result,
            **(
                {"detail": result.get("error") or "请求未完成，请明确重试"}
                if code >= 400
                else {}
            ),
        },
    )


def build_conversation_router(config, require_project, generator, task_starter=None):
    # Parent router already supplies /api/projects.
    router = APIRouter(tags=["project-conversations"])

    def service(project_id):
        project = require_project(project_id)
        return (
            ConversationService(project_id, config.paths.project_db(project_id)),
            project,
        )

    def fail(exc):
        from core.workspace.conversation_moves import ConversationMoved
        if isinstance(exc,ConversationMoved):
            from web.conversation_move_api import moved_error
            moved_error(exc,config.paths,require_project)
        raise HTTPException(
            status_code=(
                503
                if isinstance(exc, (ConversationStorageError, _TaskError))
                else (
                    404
                    if isinstance(exc, (ConversationNotFound, _TaskNotFound))
                    else (
                        409
                        if isinstance(exc, (RevisionConflict, _TaskConflict))
                        else 422
                    )
                )
            ),
            detail=str(exc),
        ) from exc

    @router.get("/{project_id}/call-audit")
    def call_audit(project_id: str, operation_id: str | None = Query(None,max_length=400),
                   limit: int = Query(30,ge=1,le=100), offset: int = Query(0,ge=0,le=1000000)):
        s,_ = service(project_id)
        from core.invocation_audit import read_audit
        try:
            return read_audit(s.db_path,project_id,operation_id=operation_id,limit=limit,offset=offset)
        except FileNotFoundError as exc:
            raise HTTPException(404,detail="项目数据库不存在，未创建替代数据库") from exc

    @router.get("/{project_id}/conversations")
    def list_conversations(
        project_id: str,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
    ):
        s, _ = service(project_id)
        try:
            return s.list(limit, offset)
        except WorkspaceError as exc:
            fail(exc)

    @router.post("/{project_id}/conversations")
    def create_conversation(project_id: str, request: ConversationCreate):
        s, _ = service(project_id)
        try:
            return {"conversation": s.create(request), "request_id": request.request_id}
        except WorkspaceError as exc:
            fail(exc)

    @router.get("/{project_id}/conversations/{conversation_id}")
    def get_conversation(project_id: str, conversation_id: str):
        s, _ = service(project_id)
        try:
            return {"conversation": s.get(conversation_id)}
        except WorkspaceError as exc:
            fail(exc)

    @router.patch("/{project_id}/conversations/{conversation_id}")
    def patch_conversation(
        project_id: str, conversation_id: str, request: ConversationPatch
    ):
        s, _ = service(project_id)
        try:
            return {"conversation": s.patch(conversation_id, request)}
        except WorkspaceError as exc:
            fail(exc)

    @router.get("/{project_id}/conversations/{conversation_id}/research-route")
    def research_route(project_id: str, conversation_id: str):
        s, project = service(project_id)
        try:
            return s.research_route(conversation_id, project.topic)
        except WorkspaceError as exc:
            fail(exc)

    @router.get("/{project_id}/conversations/{conversation_id}/messages")
    def messages(
        project_id: str,
        conversation_id: str,
        limit: int = Query(30, ge=1, le=100),
        after: int = Query(0, ge=0, le=9223372036854775807),
        before: int | None = Query(None, ge=1, le=9223372036854775807),
        tail: bool = False,
    ):
        s, _ = service(project_id)
        try:
            return s.messages(conversation_id, limit, after, before=before, tail=tail)
        except WorkspaceError as exc:
            fail(exc)

    @router.get("/{project_id}/conversations/{conversation_id}/suggestions")
    def suggestions(
        project_id: str,
        conversation_id: str,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
        recent: bool = False,
    ):
        s, _ = service(project_id)
        try:
            rows = s.suggestions(
                conversation_id, limit=limit + 1, offset=offset, recent=recent
            )
            return {
                "suggestions": rows[:limit],
                "next_offset": offset + limit if len(rows) > limit else None,
            }
        except WorkspaceError as exc:
            fail(exc)

    @router.post("/{project_id}/conversations/{conversation_id}/messages")
    def send(project_id: str, conversation_id: str, request: ConversationSend):
        s, project = service(project_id)
        try:
            if request.recovery_of is not None:
                raise WorkspaceError("恢复请求须使用服务端的新一轮入口")
            from core.workspace.teaching import teaching_identity, TeachingService

            if teaching_identity(s):
                return result_response(
                    TeachingService(s).free(conversation_id, request)
                )
            normalize_public_domain(project.domain)
            from core.workspace.plans import requested

            if (
                task_starter is not None
                and requested(request.content, request.plan_request) is None
            ):
                # 只识别明确的检索授权；普通问答、阅读材料、页面操作照常走讨论。
                intent = classify_search_request(request.content)
                if intent.is_search:
                    return result_response(
                        task_starter(project, conversation_id, request, intent)
                    )
            return result_response(
                s.send(conversation_id, request, project.topic, generator(project), search_available=task_starter is not None)
            )
        except (WorkspaceError, ValueError) as exc:
            fail(exc)

    @router.get("/{project_id}/conversations/{conversation_id}/requests/{request_id}")
    def request_state(project_id: str, conversation_id: str, request_id: str):
        s, _ = service(project_id)
        try:
            return s.request_view(conversation_id, request_id)
        except WorkspaceError as exc:
            fail(exc)

    @router.post(
        "/{project_id}/conversations/{conversation_id}/requests/{request_id}/retry"
    )
    def retry(
        project_id: str,
        conversation_id: str,
        request_id: str,
        request: ConversationRetry = ConversationRetry(),
    ):
        s, project = service(project_id)
        try:
            normalize_public_domain(project.domain)
            return result_response(
                s.retry(
                    conversation_id,
                    request_id,
                    project.topic,
                    generator(project),
                    request.expected_revision,
                    request.wait_for_completion,
                    search_available=task_starter is not None,
                )
            )
        except (WorkspaceError, ValueError) as exc:
            fail(exc)

    @router.post(
        "/{project_id}/conversations/{conversation_id}/requests/{request_id}/restart"
    )
    def restart(project_id: str, conversation_id: str, request_id: str,
                request: ConversationRestart):
        s, project = service(project_id)
        try:
            normalize_public_domain(project.domain)
            return result_response(s.restart(
                conversation_id, request_id, project.topic, generator(project),
                acknowledge_model_cost=request.acknowledge_model_cost,
                wait_for_completion=request.wait_for_completion,
                search_available=task_starter is not None,
            ))
        except (WorkspaceError, ValueError) as exc:
            fail(exc)

    @router.post(
        "/{project_id}/conversations/{conversation_id}/suggestions/{suggestion_id}/apply"
    )
    def apply(
        project_id: str,
        conversation_id: str,
        suggestion_id: str,
        request: ConversationApply = ConversationApply(),
    ):
        s, _ = service(project_id)
        try:
            return s.apply(conversation_id, suggestion_id, request.expected_revision)
        except WorkspaceError as exc:
            fail(exc)

    @router.post(
        "/{project_id}/conversations/{conversation_id}/messages/{message_id}/actions/{action_id}"
    )
    def guidance_action(
        project_id: str,
        conversation_id: str,
        message_id: str,
        action_id: str,
        request: ConversationApply = ConversationApply(),
    ):
        s, _ = service(project_id)
        try:
            return s.perform_action(
                conversation_id, message_id, action_id, request.expected_revision
            )
        except WorkspaceError as exc:
            fail(exc)

    @router.get("/{project_id}/conversations/{conversation_id}/guidance-actions")
    def guidance_actions(
        project_id: str,
        conversation_id: str,
        message_ids: str = Query(..., min_length=1, max_length=10000),
    ):
        s, _ = service(project_id)
        try:
            return {
                "actions": s.guidance_actions(conversation_id, message_ids.split(","))
            }
        except WorkspaceError as exc:
            fail(exc)

    return router
