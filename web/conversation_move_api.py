"""Account-owned preflight, confirmation and read-only move resolution."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from core.workspace.schema import Contract
from core.workspace.service import WorkspaceError, RevisionConflict
from core.workspace.conversations import ConversationNotFound, ConversationStorageError
from core.workspace.conversation_moves import ConversationMover, ConversationMoved, MoveRefused, resolve
import sqlite3


class MoveConfirm(Contract):
    target_project_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    request_id:str=Field(min_length=1,max_length=128)
    expected_token:str=Field(min_length=64,max_length=64)
    acknowledge_history:bool


def moved_error(exc,paths,require_project):
    # Every hop is independently authorized. No destination IDs leak to a non-owner.
    pid=resolve(paths,exc.target,exc.cid,require_project)
    raise HTTPException(409,detail={'code':'conversation_moved','message':str(exc),'project_id':pid,'conversation_id':exc.cid}) from exc


def build_move_router(config,require_project,check_owner,active_user,guard):
    router=APIRouter(prefix='/api/projects',tags=['conversation-moves'])
    def mover(source):
        if not active_user():raise HTTPException(422,'本版移动仅在账号模式下提供')
        require_project(source)
        return ConversationMover(config().paths,require_project,check_owner)
    def fail(exc):
        if isinstance(exc,ConversationMoved):moved_error(exc,config().paths,require_project)
        raise HTTPException(503 if isinstance(exc,ConversationStorageError) else 404 if isinstance(exc,ConversationNotFound) else 409 if isinstance(exc,RevisionConflict) else 422,str(exc)) from exc
    @router.get('/{project_id}/conversations/{conversation_id}/move-precheck')
    def precheck(project_id:str,conversation_id:str,target_project_id:str=Query(pattern=r'^[A-Za-z0-9_-]{1,120}$')):
        try:return mover(project_id).precheck(project_id,target_project_id,conversation_id)
        except WorkspaceError as exc:fail(exc)
    @router.post('/{project_id}/conversations/{conversation_id}/move')
    def confirm(project_id:str,conversation_id:str,request:MoveConfirm):
        if request.acknowledge_history is not True:raise HTTPException(422,'须明确核对历史来源和移动范围')
        try:
            service=mover(project_id)
            with guard:
                try:
                    return {**service.confirm(project_id,request.target_project_id,conversation_id,request.request_id,request.expected_token),'move_state':'completed'}
                except MoveRefused as exc:
                    raise HTTPException(exc.result['status_code'],detail=exc.result) from exc
                except (ConversationStorageError,ConversationMoved):raise
                except WorkspaceError as exc:
                    # A reused ID may already have completed, e.g. a 409 for a
                    # different target. Never describe that as non-execution.
                    try:completed=service.status(project_id,conversation_id,request.request_id)
                    except ConversationNotFound:completed=None
                    if completed:
                        raise HTTPException(409,detail={**completed,'move_state':'completed','code':'move_already_completed','message':'原移动请求已完成，请核对当前归属'}) from exc
                    try:result=service.record_refusal(project_id,request.target_project_id,conversation_id,request.request_id,request.expected_token,exc)
                    except (sqlite3.Error,OSError) as storage:
                        raise ConversationStorageError('未能确认并保存拒绝结果，请保留原移动编号核对') from storage
                    raise HTTPException(result['status_code'],detail=result) from exc
        except WorkspaceError as exc:fail(exc)
    @router.get('/{project_id}/conversations/{conversation_id}/move-requests/{request_id}')
    def status(project_id:str,conversation_id:str,request_id:str):
        try:return mover(project_id).outcome(project_id,conversation_id,request_id)
        except WorkspaceError as exc:fail(exc)
    @router.get('/{project_id}/conversations/{conversation_id}/location')
    def location(project_id:str,conversation_id:str):
        try:
            service=mover(project_id)
            return {'project_id':resolve(service.paths,project_id,conversation_id,require_project),'conversation_id':conversation_id}
        except WorkspaceError as exc:fail(exc)
    return router
