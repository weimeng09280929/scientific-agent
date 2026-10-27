"""第五步 HTTP 接口：对话驱动的检索任务（启动 / 轮询 / 取消 / 重试 / 导出）。

- 发送或启动接口及时返回任务标识；前端轮询真实状态。
- 卡片启动走服务端绑定：卡片参数来自持久化消息，不信任调用方另传的目标。
- 能力声明：/api/conversation-capabilities 的 research_search 仅在本路由装配后为 true。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field

from core.research_tasks.service import (
    ResearchTaskService,
    SearchTaskRequest,
    TaskConflict,
    TaskError,
    TaskNotFound,
    STATUS_LABELS,
)
from core.research_tasks.synthesis import report_markdown
from core.research_tasks.detect import SearchIntent
from core.workspace.schema import Contract
from core.workspace.service import WorkspaceError


class RetryRequest(Contract):
    re_search: bool = Field(default=False)
    resume: bool = Field(default=False)  # partial 补跑：仅重试失败部分


class TaskStartResponse(Contract):
    pass


def build_research_task_router(config, require_project, task_factory):
    router = APIRouter(tags=["research-tasks"])

    def fail(exc: WorkspaceError):
        code = (
            404
            if isinstance(exc, TaskNotFound)
            else 409 if isinstance(exc, TaskConflict) else 503 if isinstance(exc, TaskError) else 422
        )
        raise HTTPException(code, str(exc)) from exc

    def service_for(project_id: str) -> ResearchTaskService:
        project = require_project(project_id)
        return task_factory(project)

    @router.post("/{project_id}/conversations/{conversation_id}/research-tasks")
    def start(project_id: str, conversation_id: str, request: SearchTaskRequest):
        service = service_for(project_id)
        try:
            if request.card_message_id and request.card_action_id:
                card = service.resolve_card(
                    conversation_id, request.card_message_id, request.card_action_id
                )
                # 可编辑范围卡片：允许调用方微调主题与年份，但卡片必须真实存在。
                if not request.topic.strip() and card.get("topic"):
                    request.topic = card["topic"]
                if request.year_from is None and card.get("year_from"):
                    request.year_from = card["year_from"]
                if request.year_to is None and card.get("year_to"):
                    request.year_to = card["year_to"]
            if not request.topic.strip():
                raise TaskError("缺少检索主题；请补充主题后再开始检索")
            view = service.start(conversation_id, request)
        except WorkspaceError as exc:
            fail(exc)
        return {
            "status": "search_started",
            "task_id": view["task_id"],
            "request_id": view["request_id"],
            "task": view,
        }

    @router.get("/{project_id}/conversations/{conversation_id}/research-tasks")
    def list_tasks(
        project_id: str,
        conversation_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
    ):
        service = service_for(project_id)
        try:
            return service.list(conversation_id, limit, offset)
        except WorkspaceError as exc:
            fail(exc)

    @router.get("/{project_id}/conversations/{conversation_id}/research-tasks/{task_id}")
    def task_state(project_id: str, conversation_id: str, task_id: str):
        service = service_for(project_id)
        try:
            view = service.get(task_id)
        except WorkspaceError as exc:
            fail(exc)
        if view["conversation_id"] != conversation_id:
            raise HTTPException(404, "检索任务不属于当前会话")
        return view

    @router.post("/{project_id}/conversations/{conversation_id}/research-tasks/{task_id}/cancel")
    def cancel(project_id: str, conversation_id: str, task_id: str):
        service = service_for(project_id)
        try:
            if service.get(task_id)["conversation_id"] != conversation_id:
                raise TaskNotFound("检索任务不属于当前会话")
            view = service.cancel(task_id)
        except WorkspaceError as exc:
            fail(exc)
        if view["conversation_id"] != conversation_id:
            raise HTTPException(404, "检索任务不属于当前会话")
        return view

    @router.post("/{project_id}/conversations/{conversation_id}/research-tasks/{task_id}/retry")
    def retry(project_id: str, conversation_id: str, task_id: str, request: RetryRequest):
        service = service_for(project_id)
        try:
            if service.get(task_id)["conversation_id"] != conversation_id:
                raise TaskNotFound("检索任务不属于当前会话")
            view = service.retry(
                task_id, re_search=request.re_search, resume=request.resume
            )
        except WorkspaceError as exc:
            fail(exc)
        if view["conversation_id"] != conversation_id:
            raise HTTPException(404, "检索任务不属于当前会话")
        return view

    @router.get("/{project_id}/research-tasks/{task_id}/export")
    def export(
        project_id: str,
        task_id: str,
        version: int | None = Query(default=None, ge=1),
    ):
        service = service_for(project_id)
        try:
            view = service.get(task_id)
        except WorkspaceError as exc:
            fail(exc)
        content = None
        if version is not None:
            try:
                entry = service.report_version(task_id, version)
            except WorkspaceError as exc:
                fail(exc)
            report = entry["report"]
            coverage = report.get("coverage") or {}
            saved_status = entry.get("status") or "unknown"
            # 历史版本按其交付时的状态渲染，不冒充当前状态。
            content = report_markdown(
                {
                    "topic": view["topic"],
                    "task_id": task_id,
                    "status": saved_status,
                    "status_label": STATUS_LABELS.get(saved_status, "历史交付状态未记录"),
                    "year_label": view.get("year_label") or "未限定年份",
                    "limits": coverage.get("limits") or {},
                    "queries": coverage.get("queries") or [],
                    "source_calls": coverage.get("source_calls") or [],
                    "report": report,
                    "report_versions": [v for v in (view.get("report_versions") or []) if v.get("version", 0) <= version],
                }
            )
            note = f"> 报告版本 v{version}（已保存快照）；交付于 {entry.get('delivered_at') or '历史记录未提供时间'}。"
            if not coverage:
                note += " 该版本未保存查询及来源调用快照，未用当前任务明细代替。"
            content = note + "\n\n" + content
        content = content or view.get("report_markdown") or (
            "任务尚未产出报告；当前状态：" + view["status_label"]
        )
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)[:100]
        suffix = f"-v{version}" if version is not None else ""
        return Response(
            content=content,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="research-task-{safe}{suffix}.md"'
            },
        )

    return router
