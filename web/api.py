"""科研 Agent 系统 Web API。

启动方式：
    python -m web.api
    # 或：uvicorn web.api:app --host 0.0.0.0 --port 8000

提供 REST 接口供前端单页应用调用。研究身份、材料与执行上下文持久化；
运行中的线程和人工等待桥仍属于单个服务进程，不支持多 worker 共享任务。
Pipeline 在独立线程中异步执行，人工节点通过 HumanCallbackBridge 桥接到 REST 接口。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from fastapi.responses import JSONResponse
from typing import Any, Callable, Optional

# 确保项目根在 sys.path（python -m web.api 已保证，但直接运行脚本时需补充）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 启动前加载 .env
from runtime.cli import _load_env  # noqa: E402

_load_env()

# 本地回环永远直连：把回环地址并入进程级 NO_PROXY（保留既有条目，
# 不清空、不全局禁用代理）。避免本机模型网关 / 本服务自调用被代理劫持。
from core.tools.http import ensure_loopback_direct  # noqa: E402

ensure_loopback_direct()


def _set_global_seed() -> None:
    """设置全局随机种子，保证搜索类节点（MCTS 等）结果可复现。

    复现要求：随机种子与复现说明。
    默认 seed=42，可用环境变量 SRA_SEED 覆盖。
    注意：LLM 输出本身有随机性，本种子保证算法内随机操作（节点扩展、
    tie-breaking、采样扰动等）在相同 LLM 输入下可复现。
    """
    import random

    seed = int(os.environ.get("SRA_SEED", "42"))
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # numpy 非必需
        pass
    print(f"[SRA] 全局随机种子已设置: {seed}（可用 SRA_SEED 覆盖）", file=sys.stderr)


_set_global_seed()


def _guard_single_instance() -> None:
    """启动时检测端口冲突，防止多 uvicorn 实例分裂内存状态。

    多实例会导致 _PROJECTS / _BRIDGE（进程内存单例）互相不可见：
    人工节点提交失败、项目进度丢失。本函数在模块 import 时探测端口。

    兼容性：
    - python -m web.api：import 时端口尚未被自己绑定，能准确识别旧实例。
    - uvicorn web.api:app / --reload：worker 子进程 import 本模块时端口尚未被自己
      绑定，不会误判；若检测到旧实例则报错退出（reload supervisor 会反复重启，
      必须先清理旧实例）。
    - 测试/嵌入式场景：设 SRA_WEB_SKIP_GUARD=1 跳过。
    """
    if os.environ.get("SRA_WEB_SKIP_GUARD", "").lower() in ("1", "true", "yes"):
        return
    import socket

    port = int(os.environ.get("SRA_WEB_PORT") or os.environ.get("PORT") or "8001")
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        print(
            f"[FATAL] 端口 {port} 已被占用，检测到另一个 uvicorn 实例在运行。\n"
            "多实例会导致项目状态互相不可见（人工节点提交失败、进度丢失）。\n"
            "请先停止旧实例再启动：\n"
            f"  netstat -ano | findstr :{port}\n"
            "  taskkill /PID <pid> /F",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        probe.close()


_guard_single_instance()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi import FastAPI, HTTPException, UploadFile, File, Form  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from pydantic import Field, ValidationError  # noqa: E402
from core.experiment_results import SubmittedResult  # noqa: E402
from core.project_archive import archive_project_directories  # noqa: E402
from core.project_catalog import project_metadata, project_rows, project_page, recent_conversations, has_active_chat  # noqa: E402
from typing import Annotated
from fastapi import Query

from core.config import get_config  # noqa: E402
from core.product_boundary import inspect_boundary, public_boundary, historical_stage, NOTICE  # noqa: E402
from core.document_math import MATH_DOCUMENT_GUIDE, split_table_row  # noqa: E402
from core.research_scope import normalize_public_domain, is_public_domain  # noqa: E402
from core.knowledge import KnowledgeStore, ExperimentStatus  # noqa: E402
from core.llm.base import strip_think_tags  # noqa: E402
from core.llm import LLMRegistry  # noqa: E402
from core.orchestration.node import HumanRequest, HumanResponse  # noqa: E402
from core.state.lifecycle import LifecycleStage, StageStatus  # noqa: E402
from core.state.session import ProjectSession  # noqa: E402
from core.tools.journal_quality import enrich_paper_quality, build_pdf_url  # noqa: E402
from core.tools.doi_resolve import find_open_access_pdf, resolve_doi_by_title  # noqa: E402


# PDF 解析结果内存缓存（key = doi 或 title 前 120 字符 → pdf_url）
# 避免 list_papers 每刷新一次页面就重复发起 Crossref/Unpaywall 网络请求
_pdf_resolve_cache: dict[str, str] = {}
_pdf_resolve_lock = threading.Lock()
from core.tools.doi_resolve import find_open_access_pdf, resolve_doi_by_title  # noqa: E402
from runtime.pipeline import Pipeline, PipelineResult  # noqa: E402
from stages.common import RESEARCH_SEARCH_PREFS  # noqa: E402


# ===== 请求/响应模型 =====


class CreateProjectRequest(BaseModel):
    topic: str
    # 首版只开放计算机范围；空值兼容旧客户端，归入 computer_science。
    domain: str = ""


class TopicDiscoveryRequest(BaseModel):
    """方向推荐请求。"""
    interest: str
    domain: str = ""
class RunProjectRequest(BaseModel):
    force_writing: bool = False


class NoteRequest(BaseModel):
    text: str


class BatchDeleteProjectsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    project_ids: list[str] = Field(min_length=1, max_length=200)


class HumanResponseRequest(BaseModel):
    action: str = "continue"  # continue / abort / rollback
    text: Optional[str] = None
    selected_option: Optional[str] = None
    expected_node_id: Optional[str] = None
    # 检索偏好（可选）：年份区间 + 期刊关键词，在确认检索方向节点提交
    search_prefs: Optional[dict] = None


# ===== 人工节点桥接 =====


class HumanCallbackBridge:
    """跨线程的人工节点桥接。

    Pipeline 在工作线程中执行，遇到 HumanNode 时调用 callback 阻塞等待；
    主线程的 REST 接口接收用户响应后唤醒 callback，让 Pipeline 继续。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # project_id -> 等待中的人工请求描述（dict 形式，便于序列化）
        self._pending: dict[str, dict[str, Any]] = {}
        # project_id -> threading.Event，用于阻塞 callback
        self._events: dict[str, threading.Event] = {}
        # project_id -> HumanResponse，由 REST 接口注入
        self._responses: dict[str, HumanResponse] = {}

    def make_callback(
        self,
        project_id: str,
        on_pending: Optional[Callable[[], None]] = None,
        on_resume: Optional[Callable[[], None]] = None,
    ):
        def _cb(req: HumanRequest) -> HumanResponse:
            event = threading.Event()
            payload = {
                "prompt": req.prompt,
                "options": list(req.options) if req.options else [],
                "allow_free_text": req.allow_free_text,
                "context": dict(req.context) if req.context else {},
                "appeared_at": datetime.now().isoformat(),
            }
            with self._lock:
                self._pending[project_id] = payload
                self._events[project_id] = event
            # 通知外部（工作线程）项目进入人工等待状态
            if on_pending is not None:
                on_pending()
            # 仅用户明确提交才结束等待；长时间阅读/离开页面不等于中止。
            event.wait()
            if on_resume is not None:
                on_resume()
            with self._lock:
                resp = self._responses.pop(project_id, None)
                self._pending.pop(project_id, None)
                self._events.pop(project_id, None)
            if resp is None:
                return HumanResponse(action="abort")
            return resp

        return _cb

    def submit(self, project_id: str, response: HumanResponse) -> bool:
        """注入响应并唤醒 callback。返回是否成功（存在等待中的请求）。"""
        with self._lock:
            event = self._events.get(project_id)
            if event is None:
                return False
            self._responses[project_id] = response
        event.set()
        return True

    def get_pending(self, project_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            req = self._pending.get(project_id)
            return dict(req) if req else None

    def clear(self, project_id: str) -> None:
        with self._lock:
            self._pending.pop(project_id, None)
            self._responses.pop(project_id, None)
            event = self._events.pop(project_id, None)
        if event is not None:
            event.set()


# ===== 项目状态 =====


@dataclass
class ProjectState:
    project_id: str
    topic: str
    created_at: str
    # 研究领域（领域自适应）：决定各阶段 prompt 中「抽取什么对象 /
    # 关注什么指标 / 记录什么方法」的语义，材料只是可选值之一
    domain: str = ""
    display_name: str = ""  # Presentation only; never replaces the research topic.
    status: str = "created"  # created / running / pending_human / completed / failed / aborted
    summary: str = ""
    last_result: Optional[PipelineResult] = None
    error: Optional[str] = None
    thread: Optional[threading.Thread] = None
    node_history: list[dict] = field(default_factory=list)
    # 当前正在执行的节点 + 下一步候选（长任务实时进度提示）
    current_node: Optional[dict] = None
    next_nodes: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    recommendation: str = ""
    # 方法改进建议（实验成败评估 advice，逐条可执行；experiment_failed 后展示）
    advice: list[dict] = field(default_factory=list)
    # 关系发现子流程产出（从 discovery 子图 context 收集，可选）
    discovery: dict = field(default_factory=dict)
    # 方向推荐产出（从 topic_discovery 子图收集）
    topic_discovery: dict = field(default_factory=dict)
    # 当前运行模式：pipeline / discovery / topic_discovery
    run_mode: str = ""
    # 用户检索偏好（论文抓取范围）：{year_min, year_max, venue_hint}
    search_prefs: dict = field(default_factory=dict)


# ===== 全局状态 =====

logger = logging.getLogger("sra.api")
_CONFIG = get_config()
_PROJECTS: dict[str, ProjectState] = {}
_BRIDGE = HumanCallbackBridge()
_LOCK = threading.Lock()


def _recover_project_topic(pid: str) -> str:
    """服务重启后从磁盘恢复项目主题。

    优先级：
    1. KV `research.topic`（pipeline start/resume 时持久化，最准确）
    2. 调研报告 cross_validation_report 里的子问题（subquery 字段，带语义）
    3. 兜底空字符串（前端提示重新确认主题）
    """
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(pid),read_only=True)
        saved = store.get_kv("pipeline.context.v1", {}) or {}
        selected = (saved.get("data", {}) or {}).get("topic_discovery.selected_topic")
        if selected:
            return str(selected)
        topic = store.get_kv("research.topic", "")
        if topic:
            return str(topic)
        # 兜底：从交叉验证报告提取信息最丰富的子问题（去掉疑问后缀，保留主干）
        report = store.get_kv("cross_validation_report", {}) or {}
        for key in ("gaps", "consensus", "conflicts"):
            items = report.get(key) or []
            for it in items:
                sq = (it or {}).get("subquery") if isinstance(it, dict) else None
                if sq and str(sq).strip():
                    # 子问题语义最接近主题，直接作为恢复主题（保留完整可读）
                    return str(sq).strip()[:60]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _scan_existing_projects() -> None:
    """启动时从磁盘扫描已存在项目并恢复内存状态。

    项目数据（knowledge.db / snapshots）持久化在 projects/ 目录，
    但 ProjectState 是进程内存单例，服务重启即丢失。此函数让重启后
    旧项目仍可通过 Web 访问（论文/知识三元组/证据等数据按 project_id 读取）。
    topic 不持久化（ProjectSession 快照不含），恢复项目显示 topic 为空，
    重新运行前需在 UI 确认主题。
    """
    projects_dir = _CONFIG.paths.projects
    if not projects_dir.exists():
        return
    for pdir in sorted(projects_dir.iterdir()):
        if not pdir.is_dir() or pdir.is_symlink() or pdir.name in _PROJECTS:
            continue
        if not (pdir / "knowledge.db").exists():
            continue
        pid = pdir.name
        # Read persisted times, never make a historical project "new" on scan.
        catalog_entry = project_metadata(pdir / "knowledge.db", pid)
        created_at = catalog_entry["created_at"] or ""
        if catalog_entry["data_status"] != "available":
            _PROJECTS[pid] = ProjectState(project_id=pid, topic="", created_at=created_at,
                status="unavailable", summary="项目数据库不可读取，原目录保留；未自动修复或建立替代库。")
            continue
        # 从 session 快照推断运行状态
        status, summary = "created", ""
        try:
            boundary=inspect_boundary(_CONFIG.paths,pid)
            cur = historical_stage(boundary)
            done = [s for s in LifecycleStage.ordered()
                    if boundary["stage_statuses"].get(s.value) in ("done","blocked")]
            if all(boundary["stage_statuses"].get(s.value) == "done" for s in LifecycleStage.ordered()):
                status = "completed"
                summary = "服务重启后恢复：全流程已完成"
            elif done or cur != LifecycleStage.RESEARCH.value:
                status = "stopped"
                summary = f"服务曾重启，运行已中断；数据已保留（最近阶段: {cur}），可继续运行"
            else:
                summary = "服务重启后自动恢复（数据可查看，重新运行需确认主题）"
        except Exception:
            status, summary = "created", "服务重启后自动恢复"
        try:
            store = KnowledgeStore(_CONFIG.paths.project_db(pid),read_only=True)
            saved_run = store.get_kv("pipeline.context.v1", {}) or {}
            domain = store.get_kv("project.domain", "") or ""
        except Exception:
            logger.warning("项目 %s 的知识库无法读取，跳过恢复", pid)
            continue
        if not isinstance(saved_run, dict):
            saved_run = {}
        saved_history = saved_run.get("history", []) or []
        if saved_history and status == "created":
            status = "stopped"
            summary = "已有执行材料和节点历史已恢复，可从上次位置继续"
        if saved_history and saved_history[-1].get("status") == "failed" and status != "completed":
            status = "failed"
            summary = saved_history[-1].get("summary", "上次节点执行失败，可从失败处恢复")
        if status != "completed" and (saved_run.get("data", {}).get("experiment.outcome") or {}).get("recommendation") == "wait_for_user_results":
            status = "experiment_pending"
            summary = "历史实验任务与方法已恢复，仅供查看与导出；当前产品不继续实验或写作。"
        try:
            persisted_discovery = KnowledgeStore(_CONFIG.paths.project_db(pid),read_only=True).get_kv(
                "web.topic_discovery", {}
            ) or {}
        except Exception:
            persisted_discovery = {}
        if persisted_discovery and status == "created":
            status = "stopped"
            summary = "服务曾重启，方向推荐已保留；可查看推荐并继续项目"
        _PROJECTS[pid] = ProjectState(
            project_id=pid,
            topic=persisted_discovery.get("selected_topic") or _recover_project_topic(pid),
            created_at=created_at,
            status=status,
            summary=summary,
            topic_discovery=persisted_discovery,
            domain=domain,
            display_name=store.get_kv("project.display_name", "") or "",
            node_history=saved_history,
            run_mode="pipeline" if (saved_run.get("data", {}) or {}).get("topic_discovery.selected_topic") else (
                "topic_discovery" if saved_run.get("current_stage") == "topic_discovery"
                else store.get_kv("web.run_mode", "pipeline")),
            search_prefs=store.get_kv("web.search_prefs") or None,
        )


if os.environ.get("SRA_WEB_SKIP_PROJECT_SCAN") != "1":
    _scan_existing_projects()


# ===== Pipeline 工作线程 =====


def _set_state_status(state: ProjectState, status: str) -> None:
    """线程安全更新项目状态。"""
    with _LOCK:
        state.status = status
        if status == "pending_human":
            state.summary = "等待你的审核与确认，已有产出已保留"
        elif status == "running":
            state.summary = "后台任务运行中，浏览其他页面不会取消任务"


def _make_progress_callback(
    state: ProjectState,
) -> tuple[Callable[[list[dict]], None], Callable[[str, list[str]], None]]:
    """构造节点级实时进度回调对（完成回调 + 开始回调）。

    完成回调：每完成一个节点就把最新节点历史写入 state.node_history。
    开始回调：每开始一个节点就把当前节点 ID 与下一步候选写入 state。

    这是解决“页面长时间无反应”的关键：research 阶段真实运行需 15-25 分钟，
    若只在 pipeline 结束后才写 node_history，前端会一直空白。此回调让
    /status 轮询能实时看到节点进度，并展示「正在执行 / 下一步」避免干等。

    注意：只写 node_history / current_node，不改 state.status。status 由
    线程函数与 on_pending/on_resume（pending_human ↔ running）统一管理，
    避免回调在人工等待期间误覆盖状态。
    """
    def _on_progress(history: list[dict]) -> None:
        with _LOCK:
            state.node_history = history

    def _on_node_started(node_id: str, next_nodes: list[str]) -> None:
        with _LOCK:
            state.current_node = {
                "node_id": node_id,
                "next_nodes": next_nodes,
                "started_at": datetime.utcnow().isoformat(),
            }
            state.next_nodes = list(next_nodes)

    return _on_progress, _on_node_started


# ===== Pipeline 工作线程 =====


def _run_pipeline_thread(project_id: str, topic: str, resume: bool, force_writing: bool = False) -> None:
    """工作线程函数：执行 Pipeline 并更新项目状态。"""
    state = _PROJECTS.get(project_id)
    if state is None:
        return
    boundary = inspect_boundary(_CONFIG.paths,project_id,pending=_BRIDGE.get_pending(project_id),observed_status=state.status,observed_history=state.node_history)
    if boundary["restricted"] or boundary["reason"] == "design_generated":
        return  # Never repair or restart an out-of-scope historical checkpoint.
    try:
        _set_state_status(state, "running")
        pipeline = Pipeline(config=_CONFIG)
        # dry_run 模式下也使用桥接回调，让用户能介入；非 dry_run 同样使用桥接
        # 人工等待期间将 status 置为 pending_human，前端可明确感知
        _progress_cbs = _make_progress_callback(state)
        state.run_mode = "pipeline"
        KnowledgeStore(_CONFIG.paths.project_db(project_id)).save_kv("web.run_mode", "pipeline")
        human_cb = _BRIDGE.make_callback(
            project_id,
            on_pending=lambda: _set_state_status(state, "pending_human"),
            on_resume=lambda: _set_state_status(state, "running"),
        )
        result: PipelineResult = pipeline.run_pipeline(
            project_id=project_id,
            topic=topic,
            human_callback=human_cb,
            resume=resume,
            on_progress=_progress_cbs[0],
            on_node_started=_progress_cbs[1],
            force_writing=force_writing,
            initial_ctx={RESEARCH_SEARCH_PREFS: state.search_prefs} if state.search_prefs else None,
            domain=state.domain,
        )
        state.last_result = result
        state.status = result.status
        state.summary = result.summary
        state.recommendation = result.recommendation
        # 方法改进建议：从实验成败评估 outcome.advice 透传给前端展示
        state.advice = (result.experiment_outcome or {}).get("advice", []) or []
        state.node_history = result.node_history or []
        state.current_node = None
        state.next_nodes = []
    except Exception as e:  # noqa: BLE001
        state.status = "failed"
        state.error = f"{type(e).__name__}: {e}"
        state.summary = f"Pipeline 执行异常: {e}"



def _run_topic_discovery_thread(project_id: str, interest: str, resume: bool) -> None:
    """工作线程函数：执行方向推荐并更新项目状态。"""
    state = _PROJECTS.get(project_id)
    if state is None:
        return
    boundary = inspect_boundary(_CONFIG.paths,project_id,pending=_BRIDGE.get_pending(project_id),observed_status=state.status,observed_history=state.node_history)
    if boundary["restricted"] or boundary["reason"] == "design_generated":
        return
    try:
        _set_state_status(state, "running")
        state.run_mode = "topic_discovery"
        KnowledgeStore(_CONFIG.paths.project_db(project_id)).save_kv("web.run_mode", "topic_discovery")
        pipeline = Pipeline(config=_CONFIG)
        human_cb = _BRIDGE.make_callback(
            project_id,
            on_pending=lambda: _set_state_status(state, "pending_human"),
            on_resume=lambda: _set_state_status(state, "running"),
        )
        def _publish_recommendations(recs: list[dict]) -> None:
            """推荐就绪时提前写入 state，供 pending_human 状态下前端展示卡片。"""
            with _LOCK:
                state.topic_discovery = {
                    **state.topic_discovery,
                    "recommendations": recs,
                    "interest": interest,
                }
            KnowledgeStore(_CONFIG.paths.project_db(project_id)).save_kv(
                "web.topic_discovery", state.topic_discovery
            )

        _progress_cbs = _make_progress_callback(state)
        result: PipelineResult = pipeline.run_topic_discovery(
            project_id=project_id,
            interest=interest,
            human_callback=human_cb,
            auto_research=False,
            resume=resume,
            on_recommendations=_publish_recommendations,
            on_progress=_progress_cbs[0],
            on_node_started=_progress_cbs[1],
            domain=state.domain,
        )
        state.last_result = result
        state.status = result.status
        state.summary = result.summary
        state.recommendation = result.recommendation
        state.node_history = result.node_history or []
        state.current_node = None
        state.next_nodes = []
        # 存储方向推荐完整数据（推荐列表 + 选择结果）
        # 失败/中止结果通常没有 extra；不可擦掉已发布的真实推荐。
        if result.extra:
            state.topic_discovery = {**state.topic_discovery, **result.extra}
        if state.topic_discovery:
            KnowledgeStore(_CONFIG.paths.project_db(project_id)).save_kv(
                "web.topic_discovery", state.topic_discovery
            )
        # 若用户已选定主题，自动把所选主题写入 state.topic，并启动 research pipeline
        selected = (result.extra or {}).get("selected_topic", "") or ""
        if result.status == "completed" and selected.strip():
            boundary = inspect_boundary(_CONFIG.paths,project_id,pending=_BRIDGE.get_pending(project_id),observed_status=state.status,observed_history=state.node_history)
            if boundary["restricted"] or boundary["reason"] == "design_generated":
                return
            with _LOCK:
                state.topic = selected.strip()
                state.run_mode = "pipeline"
            # 在新线程中异步启动 research pipeline，避免阻塞 topic_discovery 线程
            resume2 = True  # 复用已有 project_dir 中的 snapshots
            pipeline_thread = threading.Thread(
                target=_run_pipeline_thread,
                args=(project_id, selected.strip(), resume2, False),
                daemon=True,
            )
            with _LOCK:
                # Track the new worker, not the topic-discovery worker that is
                # about to exit; otherwise resume may mistake a live wait for
                # an orphan and clear its human confirmation bridge.
                state.thread = pipeline_thread
                state.status = "running"
                pipeline_thread.start()
    except Exception as e:  # noqa: BLE001
        state.status = "failed"
        state.error = f"{type(e).__name__}: {e}"
        state.summary = f"TopicDiscovery 执行异常: {e}"


def _extract_topic_discovery_summary(node_history: list[dict]) -> dict:
    """从节点历史提取方向推荐产出摘要。"""
    summary = {
        "trends_fetched": False,
        "emerging_count": 0,
        "stable_count": 0,
        "saturated_count": 0,
        "recommendations_count": 0,
        "selected_topic": "",
        "nodes": [],
    }
    for h in node_history or []:
        node_id = h.get("node_id", "")
        if node_id in ("trend_fetch", "trend_analysis", "topic_recommend", "topic_select"):
            summary["nodes"].append({
                "node_id": node_id,
                "status": h.get("status"),
                "summary": h.get("summary", ""),
            })
            if node_id == "trend_fetch":
                summary["trends_fetched"] = True
            if node_id == "trend_analysis":
                # 从 summary 提取数量
                s = h.get("summary", "")
                try:
                    if "个新兴方向" in s:
                        summary["emerging_count"] = int(s.split("个新兴方向")[0].split("：")[-1])
                    if "个稳定方向" in s:
                        summary["stable_count"] = int(s.split("个稳定方向")[0].split("，")[-1])
                    if "个饱和方向" in s:
                        summary["saturated_count"] = int(s.split("个饱和方向")[0].split("，")[-1])
                except (IndexError, ValueError):
                    pass
            if node_id == "topic_recommend":
                s = h.get("summary", "")
                try:
                    if "个推荐主题" in s:
                        summary["recommendations_count"] = int(s.split("生成")[1].split("个")[0])
                except (IndexError, ValueError):
                    pass
            if node_id == "topic_select":
                s = h.get("summary", "")
                # 用户选择的主题会通过 context 写入，这里只是标记
                summary["selected_topic"] = ""
    return summary


# ===== FastAPI 应用 =====


app = FastAPI(title="科研 Agent 系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


STATIC_DIR = Path(__file__).resolve().parent / "static"


# ===== 接口实现 =====


def _public_domain(domain: str) -> str:
    try:
        return normalize_public_domain(domain)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _writing_artifact_count(store) -> int:
    from core.knowledge.schema import ArtifactType
    latest = {}
    for artifact in store.list_artifacts():
        if artifact.artifact_type in (ArtifactType.PAPER_DRAFT, ArtifactType.REVIEW_NOTE):
            old = latest.get(artifact.artifact_group)
            if old is None or artifact.version > old.version:
                latest[artifact.artifact_group] = artifact
    return sum(bool(a.content or a.content_path) for a in latest.values())


def _engine_continuation(state: ProjectState, store=None, stage_statuses=None) -> dict:
    """只描述当前可执行动作；浏览状态不自动启动任务或确认科研结论。"""
    boundary=inspect_boundary(_CONFIG.paths,state.project_id,pending=_BRIDGE.get_pending(state.project_id),observed_status=state.status,observed_history=state.node_history)
    if boundary["restricted"]:
        return {"action":"view_only","label":"查看与导出历史材料","page":"dashboard","hint":boundary["message"],
                "stage":boundary.get("execution_stage",""),"run_mode":state.run_mode or "pipeline","product_boundary":public_boundary(boundary)}
    store = store or KnowledgeStore(_CONFIG.paths.project_db(state.project_id),read_only=True)
    saved = store.get_kv("pipeline.context.v1", {}) or {}
    data = saved.get("data", {}) or {}
    history = saved.get("history", []) or state.node_history or []
    last = history[-1] if history else {}
    td = {**(store.get_kv("web.topic_discovery", {}) or {}), **state.topic_discovery}
    selected = data.get("topic_discovery.selected_topic") or td.get("selected_topic")
    mode = "pipeline" if selected else (
        "topic_discovery" if saved.get("current_stage") == "topic_discovery"
        else state.run_mode or store.get_kv("web.run_mode", "pipeline"))
    stage = saved.get("current_stage") or ("topic_discovery" if mode == "topic_discovery" else "research")
    info = {"action": "resume", "label": "继续当前任务", "page": "progress", "run_mode": mode,
            "stage": stage, "node_id": saved.get("current_node_id") or last.get("node_id"),
            "hint": "从已保存的阶段继续；已完成的材料保留，未完成或失败的请求可能需要重试。"}
    if not is_public_domain(state.domain):
        return {**info, "action": "view_only", "label": "查看历史材料", "page": "dashboard", "hint": "该历史领域暂不开放新运行，已有材料与记录保留。"}
    live = state.thread is not None and state.thread.is_alive()
    if live:
        pending = _BRIDGE.get_pending(state.project_id)
        return {**info, "action": "confirm" if pending else "running", "label": "前往确认" if pending else "查看运行进度",
                "page": "human" if pending else "progress", "hint": "等待你审核，浏览器可以关闭。" if pending else "任务正在运行，不会重复启动。"}
    done = stage_statuses and all(stage_statuses.get(s.value) == "done" for s in LifecycleStage.ordered())
    if boundary["reason"]=="design_generated":
        return {**info,"action":"materials","page":"experiments","label":"确认并审阅已有方案","hint":"方法文档已保留；请确认研究问题及关联方案后审阅或导出，不启动后续阶段。"}
    if done or (state.status == "completed" and mode != "topic_discovery"):
        hint = "后台流程已结束，可核对与导出已有材料；不代表创新已经成立。"
        if store.get_kv("writing.results_need_review"):
            hint = "终稿保存后结果有更新，已有稿件未自动改写。请核对新结果与稿件差异后人工修订，不能将旧稿视为最新结论。"
        return {**info, "action": "materials", "label": "核对已有问题与方案", "page": "experiments", "hint": "后台材料任务已结束，请核对已有研究问题与方案；本轮止于方案，不继续写作。"}
    outcome = data.get("experiment.outcome", {}) or {}
    if state.status == "experiment_pending" or outcome.get("recommendation") == "wait_for_user_results":
        return {**info, "action": "view_only", "label": "查看历史实验材料", "page": "experiments", "hint": NOTICE,
                "product_boundary": {**public_boundary(boundary), "restricted": True, "reason": "legacy_stage_restricted", "execution_stage": "experiment"}}
    if mode == "topic_discovery":
        info.update(label="继续选题探索", hint="恢复选题探索及主题确认，不跳过选题直接启动主科研流程。")
    if last.get("status") == "pending_human":
        info.update(label="恢复待确认事项", hint="重新打开上次的确认节点，保留已有材料，不会自动替你确认。",restore_gate=True)
    return info


def _workspace_workflow(state: ProjectState, store=None, stage_statuses=None, *, include_materials=False) -> dict:
    from core.workspace.service import WorkspaceService
    execution = _engine_continuation(state,store,stage_statuses)
    service = WorkspaceService(state.project_id,_CONFIG.paths.project_db(state.project_id),initialize_store=False)
    workflow = service.workflow(execution=execution)
    if not include_materials:
        workflow["materials"] = [{key:value for key,value in source.items() if key!="content"} for source in workflow["materials"]]
    return workflow


def _continuation(state: ProjectState, store=None, stage_statuses=None) -> dict:
    return _workspace_workflow(state,store,stage_statuses)["continuation"]


@app.post("/api/projects")
def create_project(req: CreateProjectRequest) -> dict:
    """创建新项目，返回 project_id。"""
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic 不能为空")
    domain = _public_domain(req.domain)
    project_id = f"proj_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    state = ProjectState(
        project_id=project_id,
        topic=topic,
        created_at=datetime.utcnow().isoformat(),
        domain=domain,
    )
    with _LOCK:
        _PROJECTS[project_id] = state
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
    store.save_kv("research.topic", topic)
    store.save_kv("project.domain", state.domain)
    store.save_kv("project.created_at", state.created_at)
    return {
        "project_id": project_id,
        "topic": topic,
        "created_at": state.created_at,
        "domain": state.domain,
    }


@app.get("/api/projects")
def list_projects(
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
    include_project_id: str | None = None,
) -> dict:
    """Bounded catalog for new clients; no-query legacy response is preserved."""
    with _LOCK:
        _scan_existing_projects()
        states = list(_PROJECTS.values())
    page = project_page(project_rows(_CONFIG.paths, states), limit=limit, offset=offset,
                        q=q, include_project_id=include_project_id)
    # New paged clients only: derive shared progress for the visible project
    # page, not every project's conversations or full history on each sidebar read.
    if limit is not None:
        from core.workspace.read_model import progress
        from core.workspace.service import WorkspaceService
        for row in [*page["projects"], *([page["current_project"]] if page["current_project"] else [])]:
            if row.get("data_status") != "available":
                continue
            try:
                row["research_progress"] = progress(WorkspaceService(row["project_id"], _CONFIG.paths.project_db(row["project_id"]), initialize_store=False).workflow())
            except Exception:  # Read failure is not permission to initialize/repair a historical DB.
                row["research_progress"] = {"label": "研究材料暂不可读取，请核对", "planReady": False}
    return page


@app.get("/api/conversations/recent")
def list_recent_conversations(
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    include_project_id: str | None = None,
    include_conversation_id: str | None = None,
    q: Annotated[str, Query(max_length=200)] = "",
) -> dict:
    with _LOCK:
        _scan_existing_projects()
        states = list(_PROJECTS.values())
    projects = project_rows(_CONFIG.paths, states)
    result = recent_conversations(_CONFIG.paths, projects, limit=limit, offset=offset,q=q)
    result["current_conversation"] = None
    selected = next((p for p in projects if p["project_id"] == include_project_id and p["data_status"] == "available"), None)
    if selected and include_conversation_id:
        from core.workspace.conversations import ConversationService
        try:
            c = ConversationService(include_project_id, _CONFIG.paths.project_db(include_project_id)).get(include_conversation_id)
            result["current_conversation"] = {**c, "project_name": selected["display_name"], "project_topic": selected["topic"]}
        except ValueError:
            pass  # Bad/deleted selection never invents a replacement conversation.
    return result


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    result = delete_projects_batch(BatchDeleteProjectsRequest(project_ids=[project_id]))
    return {"project_id": project_id, "deleted": True, "recoverable": True, "archive_id": result["archive_id"]}


@app.post("/api/projects/batch-delete")
def delete_projects_batch(req: BatchDeleteProjectsRequest) -> dict:
    """Preflight the whole selection, archive files, then remove active entries."""
    ids = req.project_ids
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="项目编号重复，本批未删除")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", pid) for pid in ids):
        raise HTTPException(status_code=422, detail="项目编号不合法，本批未删除")
    with _LOCK:
        for pid in ids:
            state = _PROJECTS.get(pid)
            if state is None:
                raise HTTPException(status_code=404, detail=f"项目不存在：{pid}；本批未删除，请刷新列表")
            if (state.status in ("running", "pending_human") or state.thread and state.thread.is_alive()
                    or _METHOD_REGEN_JOBS.get(pid, {}).get("status") == "running"):
                raise HTTPException(status_code=409, detail=f"项目 {pid} 正在运行或等待确认；本批未删除")
            try:
                if has_active_chat(_CONFIG.paths.project_db(pid), pid):
                    raise HTTPException(409, f"项目 {pid} 对话仍在生成；本批未归档，请完成后再试")
            except ValueError as exc:
                raise HTTPException(409, f"项目 {pid} 的对话运行状态暂不可核对；本批未归档") from exc
        try:
            archive_id = archive_project_directories(_CONFIG.paths, ids)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (RuntimeError, OSError) as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        for pid in ids:
            _PROJECTS.pop(pid, None)
            _METHOD_REGEN_JOBS.pop(pid, None)
            _BRIDGE.clear(pid)
    return {"deleted": ids, "recoverable": True, "archive_id": archive_id,
            "message": f"已将 {len(ids)} 个项目移入本机可恢复归档，不会直接抹除研究文件。"}


@app.get("/api/projects/{project_id}/status")
def get_status(project_id: str) -> dict:
    state = _require_project(project_id)
    # 当前阶段：从 session 读取（若已启动）
    current_stage = ""
    stage_statuses: dict[str, str] = {}
    boundary = inspect_boundary(_CONFIG.paths,project_id)
    current_stage = historical_stage(boundary)
    stage_statuses = {s.value:boundary["stage_statuses"].get(s.value,"not_started") for s in LifecycleStage.ordered()}

    # 产出物计数
    counts = {"papers": 0, "ideas": 0, "claims": 0, "experiments": 0, "evidence": 0,
              "entities": 0, "properties": 0, "methods": 0, "gaps": 0, "report_gaps": 0, "writing_artifacts": 0}
    notes_count = None
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        counts["papers"] = len(store.list_papers())
        counts["ideas"] = len(store.list_ideas())
        counts["claims"] = len(store.list_claims())
        counts["experiments"] = len(store.list_experiments())
        counts["evidence"] = store.evidence_stats()["total"]
        mstats = store.material_stats()
        counts["entities"] = mstats["total_materials"]
        # 兼容字段：前端/下载模板沿用 materials 键名，语义为「研究对象」
        counts["materials"] = mstats["total_materials"]
        counts["properties"] = mstats["total_properties"]
        counts["synthesis"] = mstats["total_synthesis"]
        counts["gaps"] = store.gap_stats()["total"]
        # 导航「研究现状」统计报告内的问题，而非全部结构化研究缺口。
        report = store.get_kv("cross_validation_report") or {}
        counts["report_gaps"] = len(report.get("gaps") or [])
        counts["writing_artifacts"] = _writing_artifact_count(store)
        notes_count = len(_merged_project_notes(store, state))
    except Exception:  # noqa: BLE001
        pass

    pending = _BRIDGE.get_pending(project_id)

    # 节点级进度（如 paper_fetch 的检索进度条），读不到则不返回该字段
    node_progress = None
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        node_progress = store.get_kv("web.paper_fetch_progress")
    except Exception:  # noqa: BLE001
        node_progress = None

    with _LOCK:
        status_value = state.status
        pending = _BRIDGE.get_pending(project_id)
    workflow = _workspace_workflow(state,stage_statuses=stage_statuses)
    from core.workspace.read_model import material_counts
    from core.workspace.conversations import ConversationService
    try:
        saved_material_counts = material_counts(ConversationService(project_id, _CONFIG.paths.project_db(project_id)))
    except Exception:
        saved_material_counts = {}
    return {
        "project_id": project_id,
        "topic": state.topic,
        "domain": state.domain,
        "created_at": state.created_at,
        "status": status_value,
        "run_mode": state.run_mode,
        "summary": state.summary,
        "recommendation": state.recommendation,
        "advice": state.advice,
        "error": state.error,
        "current_stage": current_stage,
        "stage_statuses": stage_statuses,
        "node_history": state.node_history,
        "current_node": state.current_node,
        "next_nodes": state.next_nodes,
        "counts": counts,
        "notes_count": notes_count,
        "pending_human": pending,
        "search_prefs": state.search_prefs,
        "topic_discovery": state.topic_discovery,
        "node_progress": node_progress,
        "product_stage":workflow["product_stage"],
        "product_boundary":workflow["product_boundary"],
        "continuation": workflow["continuation"],
        "workflow": workflow,
        "material_counts": saved_material_counts,
    }


@app.post("/api/projects/{project_id}/resume")
def resume_project_task(project_id: str) -> dict:
    """统一继续入口：选题/主流程自动分流，运行中与等待结果时不重复启动。"""
    state = _require_project(project_id)
    status_view = get_status(project_id)
    info = {**status_view["continuation"],"product_stage":status_view["product_stage"],"product_boundary":status_view["product_boundary"]}
    if info["action"] != "resume":
        return {"project_id": project_id, **info, "message": info["hint"]}
    with _LOCK:
        if state.thread is not None and state.thread.is_alive():
            return {"project_id": project_id, **info, "action": "running", "page": "progress", "message": "任务已在运行，不会重复启动。"}
        if state.status in ("running", "pending_human"):
            state.status = "stopped"
            _BRIDGE.clear(project_id)
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
    saved = store.get_kv("pipeline.context.v1", {}) or {}
    selected = (saved.get("data", {}) or {}).get("topic_discovery.selected_topic")
    if selected:
        state.topic = selected
    if info["run_mode"] == "topic_discovery":
        td = store.get_kv("web.topic_discovery", {}) or state.topic_discovery
        interest = td.get("interest") or (saved.get("data", {}) or {}).get("topic_discovery.interest") or state.topic
        result = run_topic_discovery(project_id, TopicDiscoveryRequest(interest=interest, domain=state.domain))
    else:
        result = run_project(project_id)
    if result.get("started") is False:
        return result
    return {**result, **info, "action": "started", "message": "已从保存的进度继续，最多推进到研究方案；需要确认时仍由你决定。"}


@app.post("/api/projects/{project_id}/run")
def run_project(project_id: str, req: Optional[RunProjectRequest] = None) -> dict:
    """启动/继续 pipeline（异步执行，立即返回）。

    网页不接受 force_writing；演示与真实模式均止于研究方案。
    """
    state = _require_project(project_id)
    if req and req.force_writing:
        raise HTTPException(status_code=422,detail=NOTICE)
    boundary=inspect_boundary(_CONFIG.paths,project_id,pending=_BRIDGE.get_pending(project_id),observed_status=state.status,observed_history=state.node_history)
    workflow=_workspace_workflow(state)
    info=workflow["continuation"]
    if boundary["restricted"] or boundary["reason"]=="design_generated" or info["action"] in ("plan_ready","review_record"):
        return {"project_id":project_id,**info,"message":info["hint"],"resumed":False,"force_writing":False,"started":False,"product_stage":workflow["product_stage"],"product_boundary":workflow["product_boundary"]}
    _public_domain(state.domain)
    if state.status == "running":
        raise HTTPException(status_code=409, detail="pipeline 正在运行中")
    if state.status == "pending_human":
        raise HTTPException(
            status_code=409,
            detail="当前等待人工响应，请先提交 human-response 或中止",
        )
    if not state.topic.strip():
        raise HTTPException(
            status_code=400,
            detail="项目主题为空（服务重启后恢复的项目不持久化 topic），请重新创建项目或补充主题",
        )
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
    resume = state.status != "created" or bool(store.get_kv("pipeline.context.v1"))
    force_writing = (req.force_writing if req else False)
    thread = threading.Thread(
        target=_run_pipeline_thread,
        args=(project_id, state.topic, resume, force_writing),
        daemon=True,
    )
    with _LOCK:
        if state.status in ("running", "pending_human") or (state.thread is not None and state.thread.is_alive()):
            raise HTTPException(status_code=409, detail="已有任务正在运行或等待确认，不能重复启动")
        state.thread = thread
        state.error = None
        state.status = "running"
        state.run_mode = "pipeline"
        try:
            thread.start()
        except Exception:
            state.thread = None
            state.status = "failed"
            raise
    msg = "pipeline 已启动"
    if force_writing:
        msg = "pipeline 已启动（强制写作模式：绕过实验成败判断）"
    return {"project_id": project_id, "message": msg, "resumed": resume, "force_writing": force_writing,"product_stage":workflow["product_stage"],"product_boundary":workflow["product_boundary"]}




@app.post("/api/projects/{project_id}/run-topic-discovery")
def run_topic_discovery(project_id: str, req: TopicDiscoveryRequest) -> dict:
    """启动方向推荐（topic_discovery 子图）。

    在用户给定主题之前，主动分析领域趋势、推荐研究主题。
    异步执行，推荐结果通过 /recommendations 接口查询，
    用户选择通过 /human-response 接口提交。
    """
    state = _require_project(project_id)
    boundary=inspect_boundary(_CONFIG.paths,project_id,pending=_BRIDGE.get_pending(project_id),observed_status=state.status,observed_history=state.node_history)
    if boundary["restricted"] or boundary["reason"]=="design_generated":
        info=_continuation(state)
        return {"project_id":project_id,**info,"message":info["hint"],"started":False,"product_boundary":public_boundary(boundary)}
    interest = (req.interest or "").strip()
    if not interest:
        raise HTTPException(status_code=400, detail="interest 不能为空")
    if state.status == "running":
        raise HTTPException(status_code=409, detail="任务正在运行中")
    if state.status == "pending_human":
        raise HTTPException(
            status_code=409,
            detail="当前等待人工响应，请先提交 human-response 或中止",
        )
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
    resume = state.status != "created" or bool(store.get_kv("pipeline.context.v1"))
    # 不允许通过该接口把历史非计算机项目静默改为另一领域。
    _public_domain(state.domain)
    domain = _public_domain((req.domain or "").strip() or state.domain)
    state.domain = domain  # 记录/更新研究领域
    thread = threading.Thread(
        target=_run_topic_discovery_thread,
        args=(project_id, interest, resume),
        daemon=True,
    )
    with _LOCK:
        if state.status in ("running", "pending_human") or (state.thread is not None and state.thread.is_alive()):
            raise HTTPException(status_code=409, detail="已有任务正在运行或等待确认，不能重复启动")
        state.thread = thread
        state.error = None
        state.topic = interest
        state.status = "running"
        state.run_mode = "topic_discovery"
        try:
            thread.start()
        except Exception:
            state.thread = None
            state.status = "failed"
            raise
    return {"project_id": project_id, "message": "topic_discovery 已启动", "interest": interest, "domain": domain}


@app.get("/api/projects/{project_id}/recommendations")
def get_recommendations(project_id: str) -> dict:
    """获取方向推荐产出（推荐主题列表 + 趋势分析摘要）。"""
    state = _require_project(project_id)
    return {
        "project_id": project_id,
        "topic_discovery_summary": state.topic_discovery,
        "run_mode": state.run_mode,
    }


@app.get("/api/projects/{project_id}/papers")
def list_papers(project_id: str) -> dict:
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        papers = store.list_papers()
    except Exception as e:  # noqa: BLE001
        return {"papers": [], "error": f"读取失败: {e}"}

    result = []
    for p in papers:
        # 即时补充期刊质量字段（对旧数据兜底：入库时未填充的在此补上）
        # 注意：这里只做本地构造（arxiv_id → pdf、已有 doi → doi.org），
        # 不发起 OpenAlex/Crossref 网络请求（避免接口阻塞超时）。
        # DOI 反查 + OA PDF 查找由后台补全脚本（_backfill_pdf_quality.py）持久化写回 DB。
        meta = p.metadata or {}
        if "impact_factor" not in meta:
            # 旧论文 metadata 里没有质量字段，即时补充
            tmp = {
                "title": p.title or "",
                "authors": p.authors or [],
                "year": p.year,
                "venue": p.venue or "",
                "arxiv_id": p.arxiv_id or "",
                "doi": p.doi or meta.get("doi", ""),
                "pdf_url": meta.get("pdf_url", ""),
            }
            try:
                enrich_paper_quality(tmp)
                # PDF 链接：已有直链 / arxiv_id 直接构造；有 DOI 用 doi.org 兜底
                # （读进程内缓存，命中则直接使用；未命中只做本地构造，不联网）
                if not (tmp.get("pdf_url") or "").strip():
                    tmp["pdf_url"] = build_pdf_url(tmp)
                    if not tmp["pdf_url"] and (tmp.get("doi") or "").strip():
                        tmp["pdf_url"] = f"https://doi.org/{tmp['doi']}"
            except Exception:  # noqa: BLE001
                pass
            meta = {**meta, "impact_factor": tmp.get("impact_factor", 0.0),
                    "cas_zone": tmp.get("cas_zone", ""),
                    "cas_subcategory": tmp.get("cas_subcategory", ""),
                    "is_top_journal": tmp.get("is_top_journal", False),
                    "pdf_url": tmp.get("pdf_url", ""),
                    "doi": tmp.get("doi", p.doi or "")}

        result.append({
            "paper_id": p.paper_id,
            "title": p.title,
            "authors": p.authors,
            "year": p.year,
            "venue": p.venue,
            "arxiv_id": p.arxiv_id,
            "abstract": p.abstract,
            "url": p.url or (f"https://arxiv.org/abs/{p.arxiv_id}" if p.arxiv_id else None),
            "doi": p.doi or meta.get("doi", ""),
            "doi_url": (f"https://doi.org/{p.doi or meta.get('doi', '')}" if (p.doi or meta.get("doi", "")) else None),
            "pdf_path": p.pdf_path,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "source_stage": p.source_stage,
            "reading_scope": meta.get("reading_scope", ""),
            "parse_error": meta.get("parse_error"),
            # 检索证据链字段（审计溯源：来源 / doc_id / 证据分 / 命中子问题）
            "source": meta.get("source", ""),
            "source_subquery": meta.get("source_subquery", ""),
            "doc_id": meta.get("doc_id", ""),
            "offset": meta.get("offset", 0),
            "evidence_score": meta.get("evidence_score", 0.0),
            "relevance_score": meta.get("relevance_score", 0.0),
            "relevance_reason": meta.get("relevance_reason", ""),
            # 期刊质量指标（journal_quality 模块填充）
            "impact_factor": meta.get("impact_factor", 0.0),
            "cas_zone": meta.get("cas_zone", ""),
            "cas_subcategory": meta.get("cas_subcategory", ""),
            "is_top_journal": meta.get("is_top_journal", False),
            "pdf_url": meta.get("pdf_url", ""),
        })
    return {"papers": result}


@app.get("/api/projects/{project_id}/materials")
def list_materials(project_id: str) -> dict:
    """获取领域知识库（Task 2：研究对象-评价指标-方法条件三元组）。

    返回按研究对象聚合的知识：每个对象含其评价指标与方法条件，
    均带来源论文与证据片段（可溯源）。满足「知识抽取结构化」要求。

    数据标准化（core/knowledge/normalize.py）：
    - 性能指标统一映射（ZT/zT/figure_of_merit → 热电优值，带标准符号/单位/类别）
    - 合成方法归类（固相法/熔融法/烧结法/计算模拟…）
    - 研究对象分类（按领域自动归类，如材料体系 / 细胞系 / 模型类别…）
    """
    from core.knowledge.normalize import (
        categorize_material,
        classify_method,
        normalize_property,
    )

    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        materials = store.list_materials(limit=500)
        stats = store.material_stats()
        # 聚合：研究对象 → 评价指标列表 + 方法条件列表
        properties = store.list_material_properties()
        synthesis = store.list_material_synthesis()
        props_by_mat: dict[str, list[dict]] = {}
        for p in properties:
            norm = normalize_property(p.property_name, p.property_name_cn)
            props_by_mat.setdefault(p.material_id, []).append({
                "property_name": p.property_name,
                "property_name_cn": p.property_name_cn,
                # 标准化字段
                "norm_key": norm["key"],
                "norm_cn": norm["cn"],
                "symbol": norm["symbol"],
                "unit": norm["unit"],
                "category": norm["category"],
                "dimension": norm.get("dimension", "other"),
                "value": p.value,
                "value_num": p.value_num,
                "condition": p.condition,
                # 深度分析字段（机制/证据/数据类型）
                "mechanism": p.mechanism,
                "impact_on_target": p.impact_on_target,
                "evidence_level": p.evidence_level or "E",
                "evidence_count": p.evidence_count,
                "data_type": p.data_type,
                "test_temperature": p.test_temperature,
                "source_type": p.source_type,
                "paper_title": p.paper_title,
                "confidence": p.confidence,
                "source_snippet": p.source_snippet,
            })
        syn_by_mat: dict[str, list[dict]] = {}
        for s in synthesis:
            mth = classify_method(s.method)
            syn_by_mat.setdefault(s.material_id, []).append({
                "method": s.method,
                # 标准化字段
                "method_category": mth["category"],
                "method_label": mth["label"],
                "precursors": s.precursors,
                "temperature": s.temperature,
                "pressure": s.pressure,
                "atmosphere": s.atmosphere,
                "duration": s.duration,
                "steps": s.steps,
                # 深度分析字段（完整实验参数 + 分步流程 + 风险 + 可复现性 + 证据）
                "precursor_ratio": s.precursor_ratio,
                "solvent": s.solvent,
                "heating_rate": s.heating_rate,
                "ph": s.ph,
                "stirring": s.stirring,
                "aging_time": s.aging_time,
                "drying_temperature": s.drying_temperature,
                "calcination_temperature": s.calcination_temperature,
                "calcination_time": s.calcination_time,
                "cooling_method": s.cooling_method,
                "post_treatment": s.post_treatment,
                "equipment": s.equipment,
                "yield": s.yield_,
                "phase_purity": s.phase_purity,
                "particle_size": s.particle_size,
                "workflow_steps": s.workflow_steps,
                "risks": s.risks,
                "reproducibility_score": s.reproducibility_score,
                "evidence_level": s.evidence_level or "E",
                "evidence_count": s.evidence_count,
                "paper_title": s.paper_title,
                "confidence": s.confidence,
                "source_snippet": s.source_snippet,
            })
        items = []
        prop_cat_counter: dict[str, int] = {}
        method_cat_counter: dict[str, int] = {}
        material_cat_counter: dict[str, int] = {}
        for m in materials:
            mat_cat = categorize_material(m.name, m.formula)
            material_cat_counter[mat_cat] = material_cat_counter.get(mat_cat, 0) + 1
            props = props_by_mat.get(m.material_id, [])
            syns = syn_by_mat.get(m.material_id, [])
            for pr in props:
                prop_cat_counter[pr["category"]] = prop_cat_counter.get(pr["category"], 0) + 1
            for sy in syns:
                method_cat_counter[sy["method_category"]] = method_cat_counter.get(sy["method_category"], 0) + 1
            items.append({
                "material_id": m.material_id,
                "name": m.name,
                "formula": m.formula,
                "category": mat_cat,
                "crystal_structure": m.crystal_structure,
                "space_group": m.space_group,
                "lattice_parameters": m.lattice_parameters,
                "symmetry": m.symmetry,
                "composition": m.composition,
                "paper_id": m.paper_id,
                "paper_title": m.paper_title,
                "source_paper_ids": m.metadata.get("source_paper_ids", []),
                "confidence": m.confidence,
                "source_snippet": m.source_snippet,
                "properties": props,
                "synthesis": syns,
            })
        # ===== 研究对象间关联（同体系 / 共指标 / 共方法 / 同论文）=====
        # 关联规则（任一命中即关联，附带关联类型与理由）：
        #   1. same_system  ：对象类别相同（同体系 / 同类别）
        #   2. same_property：报告了相同的归一化性能指标（如都测热电优值）
        #   3. same_method  ：合成方法类别相同（如都用固相法）
        #   4. same_paper   ：出自同一篇论文
        # 每条关联含 {material_id, name, formula, relation, reason}，
        # 供前端知识卡片「关联对象」区块展示（点击跳转）。
        mat_index: dict[str, dict] = {}
        for it in items:
            mat_index[it["material_id"]] = it
        for it in items:
            rels: list[dict] = []
            seen_rel: set[str] = set()
            it_props = {p["norm_key"] for p in it.get("properties", [])}
            it_methods = {s["method_category"] for s in it.get("synthesis", [])}
            it_papers = set(it.get("source_paper_ids") or [])
            if it.get("paper_id"):
                it_papers.add(it["paper_id"])
            for other in items:
                if other["material_id"] == it["material_id"]:
                    continue
                oid = other["material_id"]
                relation, reason = "", ""
                if it.get("category") and other.get("category") and it["category"] == other["category"]:
                    relation, reason = "same_system", f"同属「{it['category']}」体系"
                elif it_props & {p["norm_key"] for p in other.get("properties", [])}:
                    shared = it_props & {p["norm_key"] for p in other.get("properties", [])}
                    relation, reason = "same_property", f"共报性能指标：{next(iter(shared))}"
                elif it_methods & {s["method_category"] for s in other.get("synthesis", [])}:
                    shared = it_methods & {s["method_category"] for s in other.get("synthesis", [])}
                    relation, reason = "same_method", f"共用合成方法：{next(iter(shared))}"
                elif it_papers & set(other.get("source_paper_ids") or []):
                    relation, reason = "same_paper", "出自同一篇论文"
                if relation and oid not in seen_rel:
                    seen_rel.add(oid)
                    rels.append({
                        "material_id": oid,
                        "name": other.get("name", ""),
                        "formula": other.get("formula", ""),
                        "relation": relation,
                        "reason": reason,
                    })
            # 按关联类型排序（体系 > 性能 > 方法 > 论文），最多展示 6 条
            rel_order = {"same_system": 0, "same_property": 1, "same_method": 2, "same_paper": 3}
            rels.sort(key=lambda r: rel_order.get(r["relation"], 9))
            it["related_materials"] = rels[:6]
    except Exception as e:  # noqa: BLE001
        return {"materials": [], "stats": {}, "error": f"读取失败: {e}"}
    return {
        "materials": items,
        "stats": stats,
        "aggregation": {
            "property_categories": prop_cat_counter,
            "method_categories": method_cat_counter,
            "material_categories": material_cat_counter,
        },
    }


@app.get("/api/projects/{project_id}/gaps")
def list_research_gaps(project_id: str) -> dict:
    """获取研究缺口清单（Task 3：Research Gap 识别）。

    由 ResearchGapIdentifyAgent 在 cross_validate 之后生成并落库，
    每条 Gap 含类型（矛盾结论/未被探索方向/缺失知识连接）、
    证据链（可溯源 paper_id + snippet）、可操作性、优先级，
    满足「文献溯源完整性」要求，供前端「研究缺口」页展示。
    """
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        gaps = store.list_research_gaps(limit=200)
        stats = store.gap_stats()
    except Exception as e:  # noqa: BLE001
        return {"gaps": [], "stats": {"total": 0, "by_type": {}},
                "error": f"读取失败: {e}"}
    return {
        "gaps": [
            {
                "gap_id": g.gap_id,
                "gap_type": g.gap_type,
                "statement": g.statement,
                "detail": g.detail,
                "evidence": g.evidence,
                "db_evidence": g.db_evidence,
                "related_materials": g.related_materials,
                "actionability": g.actionability,
                "priority": g.priority,
                "source": g.source,
                "suggested_actions": g.suggested_actions,
                "subquery": g.subquery,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
            for g in gaps
        ],
        "stats": stats,
    }


@app.get("/api/projects/{project_id}/conflicts")
def list_research_conflicts(project_id: str) -> dict:
    """获取文献冲突清单（交叉验证落库，供 Claim 冲突可视化与论文溯源）。

    每条冲突：冲突陈述 + 立场证据（support/refute 双方来源，可点击跳转
    论文页溯源）+ 处置建议 + 置信度 + 来源子问题。

    自动裁决建议（会议纪要要求「同一结论冲突时按期刊等级/文献新旧二次验证」）：
    对每条冲突的 support/refute 双方按「期刊等级（IF/分区）> 文献新度」加权打分，
    给出 auto_verdict 建议采纳方；若已有人工裁决（metadata.adjudication），
    返回人工裁决结果供前端标记。
    """
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        conflicts = store.list_research_conflicts(limit=200)
        stats = store.conflict_stats()
    except Exception as e:  # noqa: BLE001
        return {"conflicts": [], "stats": {"total": 0, "papers": 0},
                "error": f"读取失败: {e}"}

    # 论文质量速查表（paper_id → {if, year, zone}），用于自动裁决打分
    quality_map: dict[str, dict] = {}
    try:
        for p in store.list_papers():
            md = p.metadata or {}
            quality_map[p.paper_id] = {
                "if": float(md.get("impact_factor") or 0.0),
                "year": p.year or 0,
                "zone": md.get("cas_zone") or "",
            }
    except Exception:  # noqa: BLE001
        pass

    def _side_score(sources: list[dict]) -> tuple[float, str]:
        """按「期刊等级(IF) 60% + 分区 20% + 文献新度 20%」给一方证据加权打分。"""
        if not sources:
            return 0.0, "无证据"
        total_if, total_year, n = 0.0, 0, 0
        zone_boost = 0.0
        for s in sources:
            pid = s.get("paper_id", "")
            q = quality_map.get(pid, {})
            total_if += float(q.get("if") or 0.0)
            total_year += int(q.get("year") or 0)
            zone = q.get("zone") or ""
            if zone in ("1",):
                zone_boost += 0.2
            elif zone in ("2",):
                zone_boost += 0.1
            n += 1
        if n == 0:
            return 0.0, "无证据"
        avg_if = total_if / n
        avg_year = total_year / n
        # IF 归一化（顶刊 ~30 计满分）；年份越新越加分（2025 计满分）
        if_score = min(avg_if / 30.0, 1.0) * 0.6
        year_score = min(max((avg_year - 2010) / 15.0, 0.0), 1.0) * 0.2
        zone_score = min(zone_boost / n, 1.0) * 0.2
        score = if_score + year_score + zone_score
        return round(score, 3), f"{avg_if:.1f} / {avg_year or '?'} 年 / {n} 篇"

    items = []
    for c in conflicts:
        sources = c.sources or []
        support = [s for s in sources if s.get("stance") == "support"]
        refute = [s for s in sources if s.get("stance") == "refute"]
        sup_score, sup_info = _side_score(support)
        ref_score, ref_info = _side_score(refute)
        md = c.metadata or {}
        adjudication = md.get("adjudication") or {}
        if adjudication.get("verdict"):
            auto_verdict = adjudication["verdict"]
            auto_reason = adjudication.get("reason") or "人工裁决"
            auto_by = adjudication.get("by") or "user"
        else:
            if sup_score > ref_score and sup_score > 0:
                auto_verdict = "adopt_support"
            elif ref_score > sup_score and ref_score > 0:
                auto_verdict = "adopt_refute"
            elif sup_score == 0 and ref_score == 0:
                auto_verdict = "unknown"
            else:
                auto_verdict = "suspect"
            auto_reason = (
                f"自动建议：support 方证据质量 {sup_score}（{sup_info}），"
                f"refute 方 {ref_score}（{ref_info}）"
            )
            auto_by = "auto"
        items.append({
            "conflict_id": c.conflict_id,
            "claim": c.claim,
            "sources": sources,
            "resolution": c.resolution,
            "confidence": c.confidence,
            "subquery": c.subquery,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            # 自动/人工裁决
            "auto_verdict": auto_verdict,
            "auto_reason": auto_reason,
            "adjudicated": bool(adjudication.get("verdict")),
            "adjudication_by": auto_by,
            # 双方证据质量详情（前端展示打分依据）
            "support_score": sup_score,
            "support_info": sup_info,
            "refute_score": ref_score,
            "refute_info": ref_info,
        })
    return {"conflicts": items, "stats": stats}


class ConflictAdjudicateRequest(BaseModel):
    """冲突裁决请求。"""
    verdict: str = "suspect"  # adopt_support / adopt_refute / suspect
    note: str = ""


@app.post("/api/projects/{project_id}/conflicts/{conflict_id}/adjudicate")
def adjudicate_conflict(project_id: str, conflict_id: str, req: ConflictAdjudicateRequest) -> dict:
    """人工裁决文献冲突（会议纪要要求：冲突文献二选一/剔除，用户可覆盖自动建议）。

    裁决结果写回 conflict.metadata.adjudication：
    - adopt_support：采纳 support 方结论（refute 方标记低可信）
    - adopt_refute：采纳 refute 方结论
    - suspect：双方存疑，需进一步检索
    """
    _require_project(project_id)
    verdict = (req.verdict or "suspect").strip()
    if verdict not in ("adopt_support", "adopt_refute", "suspect"):
        raise HTTPException(status_code=400, detail=f"无效裁决: {verdict}")
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
        conf = store.get_research_conflict(conflict_id)
        if conf is None:
            raise HTTPException(status_code=404, detail="冲突不存在")
        md = dict(conf.metadata or {})
        md["adjudication"] = {
            "verdict": verdict,
            "reason": (req.note or "").strip() or None,
            "by": "user",
            "at": datetime.utcnow().isoformat(),
        }
        from core.knowledge.schema import ResearchConflict
        updated = ResearchConflict(
            conflict_id=conf.conflict_id,
            claim=conf.claim,
            sources=conf.sources,
            resolution=conf.resolution,
            confidence=conf.confidence,
            subquery=conf.subquery,
            metadata=md,
            created_at=conf.created_at,
            source_stage=conf.source_stage,
        )
        store.save_research_conflict(updated)
        return {
            "conflict_id": conflict_id,
            "verdict": verdict,
            "message": f"裁决已保存：{'采纳 support 方' if verdict == 'adopt_support' else '采纳 refute 方' if verdict == 'adopt_refute' else '双方存疑待进一步检索'}",
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"裁决保存失败: {e}") from e


@app.get("/api/projects/{project_id}/evidence")
def list_evidence(project_id: str, limit: int = Query(10, ge=1, le=30), offset: int = Query(0, ge=0)) -> dict:
    """获取检索证据链（审计轨迹：query → source → 命中 → paper 关联）。

    文献调研可溯源：Sciverse 调用记录天然构成证据链。
    每条记录：触发子问题、数据源、命中证据标题、外部 ID（doc_id/arxiv_id）、
    证据分、片段摘要，以及是否关联到已入库论文。
    """
    _require_project(project_id)
    if not _CONFIG.paths.project_db(project_id).is_file():
        raise HTTPException(status_code=404, detail="项目数据库不存在，未创建空白数据替代历史项目")
    from core.workspace.conversations import ConversationService
    from core.workspace.read_model import search_audits
    # Keep the old entries/stats contract while exposing the new task's own
    # source calls and server-authored coverage. No migration or model call.
    audit = search_audits(ConversationService(project_id, _CONFIG.paths.project_db(project_id)), limit=limit, offset=offset)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        entries = store.list_evidence(limit=500)
        stats = store.evidence_stats()
    except Exception as e:  # noqa: BLE001
        return {
            "entries": [],
            "stats": {"total": 0, "by_source": {}, "linked": 0},
            "error": f"读取失败: {e}",
            "conversation_search": audit,
        }
    return {"entries": entries, "stats": stats, "conversation_search": audit}


@app.get("/api/projects/{project_id}/unlinked-papers")
def list_unlinked_papers(project_id: str) -> dict:
    """列出未入库论文候选（检索命中但未关联入库的证据，按 external_id/title 去重）。

    检索阶段每条子问题按固定配额抓取（Sciverse 10 + arXiv 3 + S2 2），
    其中被相关性筛选或去重剔除的候选保留在证据链（paper_id 为空）。
    前端可展示为「未入库论文」，支持用户手动补录入库。

    每篇候选附带未入库原因（reason）：
    - score_rejected: 相关性打分 < 0.5 被 filter 剔除（Sciverse 用语义检索分，
      arXiv/S2 用 LLM 评估分）
    - dedup_merged:   与已入库论文重复（同 external_id / 同 title），去重时合并
    """
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        entries = store.list_unlinked_evidence(limit=500)
        papers_all = store.list_papers()
    except Exception as e:  # noqa: BLE001
        return {"papers": [], "error": f"读取失败: {e}"}

    # 已入库论文的 arxiv_id / title / doc_id(S2/Sciverse) / DOI 集合（用于判定去重合并）
    paper_ax: set[str] = set()
    paper_titles: set[str] = set()
    paper_doc_ids: set[str] = set()
    paper_dois: set[str] = set()
    paper_id_by_ax: dict[str, str] = {}
    paper_id_by_title: dict[str, str] = {}
    paper_id_by_doc: dict[str, str] = {}
    paper_id_by_doi: dict[str, str] = {}
    for p in papers_all:
        ax = (p.arxiv_id or "").strip()
        tl = (p.title or "").strip().lower()
        md = p.metadata or {}
        doc = (md.get("doc_id") or "").strip()
        doi = (p.doi or md.get("doi") or "").strip().lower()
        if ax:
            paper_ax.add(ax)
            paper_id_by_ax.setdefault(ax, p.paper_id)
        if tl:
            paper_titles.add(tl)
            paper_id_by_title.setdefault(tl, p.paper_id)
        if doc:
            paper_doc_ids.add(doc)
            paper_id_by_doc.setdefault(doc, p.paper_id)
        if doi:
            paper_dois.add(doi)
            paper_id_by_doi.setdefault(doi, p.paper_id)

    # 按 external_id（或 title）去重聚合：同一候选可能被多个子问题命中
    seen: dict[str, dict] = {}
    for e in entries:
        key = (e.get("external_id") or "").strip() or \
            (e.get("title") or "").strip().lower()
        if not key:
            continue
        if key not in seen:
            seen[key] = {
                "external_id": e.get("external_id", ""),
                "title": e.get("title", ""),
                "source": e.get("source", ""),
                "snippet": e.get("snippet", ""),
                "subquery": e.get("subquery", ""),
                "evidence_score": e.get("evidence_score", 0.0),
                "hit_count": 1,
                "log_ids": [e.get("log_id", "")],
                "reason": "",
                "reason_detail": "",
            }
        else:
            seen[key]["hit_count"] += 1
            if e.get("log_id"):
                seen[key]["log_ids"].append(e.get("log_id", ""))
            if not seen[key]["snippet"]:
                seen[key]["snippet"] = e.get("snippet", "")

    # 二次去重：同一论文被多源命中（arXiv ID ≠ S2 ID）会生成两个 key，
    # 按标题精确合并为一张候选卡，避免「未入库论文」列表出现重复项
    def _is_arxiv_id(v: str) -> bool:
        return bool(re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", (v or "").strip()))

    by_title: dict[str, dict] = {}
    deduped: list[dict] = []
    for p in seen.values():
        tkey = (p["title"] or "").strip().casefold()
        hit = by_title.get(tkey) if tkey else None
        if hit is None:
            if tkey:
                by_title[tkey] = p
            deduped.append(p)
            continue
        # external_id 优先保留 arXiv ID（可直接还原 PDF 直链），S2/Sciverse ID 次之
        if not hit["external_id"] or (
            _is_arxiv_id(p["external_id"]) and not _is_arxiv_id(hit["external_id"])
        ):
            hit["external_id"] = p["external_id"]
            hit["source"] = p["source"] or hit["source"]
        hit["hit_count"] += p["hit_count"]
        hit["log_ids"].extend(p["log_ids"])
        hit["evidence_score"] = max(
            float(hit["evidence_score"] or 0), float(p["evidence_score"] or 0)
        )
        if not hit["snippet"]:
            hit["snippet"] = p["snippet"]

    # 判定每篇候选的未入库原因
    for p in deduped:
        ax = (p["external_id"] or "").strip()
        tl = (p["title"] or "").strip().lower()
        dup_pid = (
            paper_id_by_ax.get(ax)
            or paper_id_by_title.get(tl, "")
            or paper_id_by_doc.get(ax, "")
            or paper_id_by_doi.get(ax.lower(), "")
        )
        if dup_pid:
            dup_title = next(
                (pp.title for pp in papers_all if pp.paper_id == dup_pid),
                "",
            )
            p["reason"] = "dedup_merged"
            p["reason_detail"] = (
                f"与已入库论文《{dup_title[:60]}》重复，去重时合并"
            )
        elif p["source"] == "sciverse" and 0 < float(p["evidence_score"] or 0) < 0.5:
            p["reason"] = "score_rejected"
            p["reason_detail"] = "Sciverse 证据分低于 0.5，未进入论文库"
        else:
            p["reason"] = "unlinked_unknown"
            p["reason_detail"] = "检索命中尚未关联论文；可能仍在筛选、被去重，或筛选未完成，不能断言相关度低"

    papers = sorted(
        deduped,
        key=lambda p: (p["evidence_score"] or 0.0),
        reverse=True,
    )
    return {
        "papers": papers,
        "total": len(papers),
        "filter_threshold": 0.5,
        "filter_note": (
            "入库量化标准：filter 阶段相关性打分 ≥ 0.5 才入库。"
            "Sciverse 候选复用语义检索证据分；arXiv/S2 候选由 LLM 按主题相关性评估。"
            "低于 0.5 的候选保留在未入库列表，可人工复核后手动补录。"
        ),
    }


def _paper_identifiers_from_evidence(source: str, external_id: str) -> tuple[str, str, str, str]:
    """从可信来源类型的外部 ID 还原 DOI/arXiv 与原文、PDF 地址。"""
    source = (source or "").lower()
    raw = (external_id or "").strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw, flags=re.I) if source == "crossref" else ""
    if doi and not re.fullmatch(r"10\.\d{4,9}/\S+", doi, flags=re.I):
        doi = ""
    arxiv_id = ""
    if source == "arxiv":
        arxiv_id = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", raw, flags=re.I)
        arxiv_id = re.sub(r"\.pdf$", "", arxiv_id, flags=re.I)
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.I)
        if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})", arxiv_id, flags=re.I):
            arxiv_id = ""
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (f"https://doi.org/{doi}" if doi else "")
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else ""
    if not doi and re.fullmatch(r"10\.\d{4,9}/\S+", raw, flags=re.I):
        # 通用兜底：external_id 本身就是裸 DOI（如 openalex 等来源）
        doi = raw
        url = url or f"https://doi.org/{doi}"
    return doi, arxiv_id, url, pdf_url


@app.post("/api/projects/{project_id}/papers/import")
def import_unlinked_paper(project_id: str, body: dict) -> dict:
    """手动补录入库：将一条未关联证据候选正式入库为论文，并回填证据链关联。

    body:
      - external_id: 候选的外部 ID（sciverse doc_id / arxiv_id / s2 paperId）
      - title: 候选标题（external_id 缺失时的兜底定位）
      - snippet: 可选覆盖片段（否则用证据链中的 snippet 作为摘要）
    """
    _require_project(project_id)
    external_id = (body.get("external_id") or "").strip()
    title = (body.get("title") or "").strip()
    if not external_id and not title:
        raise HTTPException(status_code=400, detail="external_id 或 title 至少提供一个")

    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
        entries = store.list_unlinked_evidence(limit=500)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取证据链失败: {e}")

    # 定位候选：优先 external_id 精确匹配，其次 title 完全一致
    cand = None
    for e in entries:
        eid = (e.get("external_id") or "").strip()
        etitle = (e.get("title") or "").strip().lower()
        if external_id and eid and eid == external_id:
            cand = e
            break
        if not external_id and title and etitle == title.lower():
            cand = e
            break
    if cand is None:
        # 候选不在未关联列表：可能已入库 → 返回已存在的论文信息
        if external_id:
            existing = store.find_paper_by_external_id(external_id)
            if existing is not None:
                return {
                    "paper_id": existing.paper_id,
                    "created": False,
                    "message": f"该候选已入库：{existing.title}",
                }
        raise HTTPException(
            status_code=404,
            detail="未找到该未入库候选（可能已被入库或已不在证据链中）",
        )

    # 重复入库检查：external_id 已存在则直接关联，不新建论文
    existing = None
    if (cand.get("external_id") or "").strip():
        existing = store.find_paper_by_external_id(cand["external_id"].strip())
    if existing is None:
        existing = next(
            (p for p in store.list_papers()
             if (p.title or "").strip().casefold() == (cand.get("title") or "").strip().casefold()),
            None,
        )
    if existing is not None:
        linked_count = 0
        for e in entries:
            eid = (e.get("external_id") or "").strip()
            etitle = (e.get("title") or "").strip().casefold()
            if (eid and eid == (cand.get("external_id") or "").strip()
                    or etitle and etitle == (cand.get("title") or "").strip().casefold()) and e.get("log_id"):
                store.link_evidence_to_paper(e["log_id"], existing.paper_id)
                linked_count += 1
        return {
            "paper_id": existing.paper_id,
            "created": False,
            "message": f"该候选已存在同名论文，证据链已关联 {linked_count} 条到现有论文",
        }

    # 新建论文（保留可核验外部标识，供原文跳转与 PDF 下载使用）
    paper_id = KnowledgeStore.new_id()
    doi, arxiv_id, source_url, pdf_url = _paper_identifiers_from_evidence(
        cand.get("source", ""), cand.get("external_id", "")
    )
    # 证据链若携带可核验标识（新检索写入 extra_json 的 doi/arxiv_id/url/pdf_url），优先使用
    doi = (cand.get("doi") or "").strip() or doi
    arxiv_id = (cand.get("arxiv_id") or "").strip() or arxiv_id
    source_url = (cand.get("url") or "").strip() or source_url
    pdf_url = (cand.get("pdf_url") or "").strip() or pdf_url
    snippet = body.get("snippet") or (cand.get("snippet") or "")
    # 清理前端渲染残留的 HTML 标签（手动补录时候选标题/片段可能带 <span class=highlight>）
    clean_title = re.sub(r"<[^>]+>", "", (cand.get("title") or title or "Untitled")).strip()
    clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()
    from core.knowledge.schema import Paper
    paper = Paper(
        paper_id=paper_id,
        title=clean_title,
        abstract=clean_snippet or None,
        doi=doi or None,
        arxiv_id=arxiv_id or None,
        url=source_url or None,
        metadata={
            "source": cand.get("source", ""),
            "source_subquery": cand.get("subquery", ""),
            "doc_id": cand.get("external_id", ""),
            "doi": doi,
            "pdf_url": pdf_url,
            "evidence_score": cand.get("evidence_score", 0.0),
            "relevance_score": 0.0,
            "relevance_reason": "手动补录入库",
            "manually_imported": True,
        },
        source_stage="research",
    )
    # 入库论文 + 切分 chunk
    from core.tools.text_split import split_into_chunks
    store.save_paper(paper)
    abstract = clean_snippet or ""
    chunks = split_into_chunks(abstract, max_tokens=500, overlap_tokens=50)
    from core.knowledge.schema import PaperChunk
    chunk_objs = [
        PaperChunk(
            chunk_id=KnowledgeStore.new_id(),
            paper_id=paper_id,
            chunk_index=i,
            text=c.text,
        )
        for i, c in enumerate(chunks)
    ]
    store.save_paper_chunks(chunk_objs)

    # 审计轨迹：手动补录也写一条 evidence_log（source=manual），
    # 使「审计轨迹」能覆盖所有论文来源，避免「已入库 0 条」观感矛盾。
    try:
        store.log_evidence({
            "subquery": cand.get("subquery") or "手动补录",
            "source": "manual",
            "paper_id": paper_id,
            "title": clean_title,
            "external_id": cand.get("external_id") or "",
            "evidence_score": cand.get("evidence_score", 0.0),
            "snippet": clean_snippet[:300],
            "match_type": "manual_import",
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("手动补录证据链落库失败: %s", e)

    # 回填证据链关联：该候选可能被多个子问题命中，全部关联。
    # 匹配口径与上方"已存在论文"分支一致：external_id 精确 或 标题一致（忽略大小写），
    # 不再受请求参数是否携带限制（避免同论文不同 ID 命中的证据漏关联）。
    cand_eid = (cand.get("external_id") or "").strip()
    cand_title = (cand.get("title") or title or "").strip().casefold()
    linked_count = 0
    for e in entries:
        eid = (e.get("external_id") or "").strip()
        etitle = (e.get("title") or "").strip().casefold()
        if ((cand_eid and eid == cand_eid) or (cand_title and etitle == cand_title)) \
                and e.get("log_id"):
            store.link_evidence_to_paper(e["log_id"], paper_id)
            linked_count += 1

    return {
        "paper_id": paper_id,
        "created": True,
        "message": f"论文已入库：{paper.title}（关联 {linked_count} 条证据）",
    }


@app.get("/api/projects/{project_id}/papers/{paper_id}/pdf")
def download_paper_pdf(project_id: str, paper_id: str):
    """代理下载论文 PDF：后端拉取 PDF 存到本地，前端真正一键下载。

    流程：
    1. 读论文的 pdf_url（无则按 arxiv_id/doi 解析）
    2. 若 pdf_url 是 doi.org 兜底链接（会跳到 HTML 文章页），
       先尝试 Unpaywall/OpenAlex 找 OA PDF 直链，再解析落地页
       <meta name="citation_pdf_url"> 提取真实 PDF 地址
    3. 后端请求 PDF 内容，保存到 <project>/papers/<paper_id>.pdf
    4. 返回文件流（attachment 下载）
    """
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
        try:
            paper = store.get_paper(paper_id)
        except Exception:  # noqa: BLE001
            paper = None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取论文失败: {e}") from e
    if paper is None:
        raise HTTPException(status_code=404, detail="论文不存在")

    # 1. 已有本地文件 → 直接返回
    if paper.pdf_path and os.path.exists(paper.pdf_path):
        return FileResponse(
            paper.pdf_path,
            media_type="application/pdf",
            filename=f"{paper.paper_id}.pdf",
        )

    # 2. 解析目标 PDF URL
    meta = paper.metadata or {}
    pdf_url = (meta.get("pdf_url") or "").strip()
    if not pdf_url and paper.arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
    doi = paper.doi or meta.get("doi") or ""
    if not pdf_url and doi:
        pdf_url = f"https://doi.org/{doi}"
    if not pdf_url:
        # 最后手段：OpenAlex 按标题反查 DOI + OA PDF（仅在用户主动点击下载时联网）
        try:
            from core.tools.doi_resolve import resolve_pdf_link
            meta_tmp = {
                "title": paper.title or "",
                "authors": paper.authors or [],
                "year": paper.year,
                "arxiv_id": paper.arxiv_id or "",
                "doi": paper.doi or "",
                "pdf_url": "",
            }
            pdf_url = resolve_pdf_link(meta_tmp)
            if pdf_url and meta_tmp.get("doi"):
                try:
                    from core.knowledge.schema import Paper as _Paper
                    store.save_paper(_Paper(
                        paper_id=paper.paper_id,
                        title=paper.title, authors=paper.authors, year=paper.year,
                        venue=paper.venue, arxiv_id=paper.arxiv_id,
                        abstract=paper.abstract, doi=meta_tmp["doi"], url=paper.url,
                        pdf_path=paper.pdf_path,
                        metadata={**meta, "pdf_url": pdf_url, "doi": meta_tmp["doi"]},
                        source_stage=paper.source_stage, created_at=paper.created_at,
                    ))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pdf_url = ""
    if not pdf_url:
        raise HTTPException(status_code=404, detail="该论文暂无可下载的 PDF 链接")

    # 3. 后端下载 PDF（带 HTML 落地页解析，处理 doi.org 兜底链接）
    import re
    import requests as _req

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/pdf,*/*;q=0.8,text/html;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def _is_pdf(content: bytes) -> bool:
        return bool(content) and len(content) >= 1000 and b"%PDF" in content[:2000]

    def _download(url: str, timeout: int = 30) -> bytes:
        resp = _req.get(url, headers=headers, timeout=timeout, stream=True)
        resp.raise_for_status()
        return resp.content

    def _extract_pdf_from_html(html: bytes, base_url: str) -> str:
        """从出版商标注页解析真实 PDF 地址（citation_pdf_url / og:url 等）。"""
        text = html.decode("utf-8", errors="ignore")
        # 1. <meta name="citation_pdf_url" content="...">
        m = re.search(
            r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
            text,
            re.I,
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
                text,
                re.I,
            )
        if m:
            url = m.group(1).strip()
            if url.startswith("//"):
                url = "https:" + url
            return url
        # 2. <link rel="citation_pdf_url" href="...">
        m = re.search(
            r'<link[^>]+rel=["\']citation_pdf_url["\'][^>]+href=["\']([^"\']+)["\']',
            text,
            re.I,
        )
        if m:
            url = m.group(1).strip()
            if url.startswith("//"):
                url = "https:" + url
            return url
        # 3. og:url（兜底，通常是文章页本身）
        return ""

    content = b""
    tried = []
    # 候选 URL 列表：优先真实 PDF 直链，其次 OA 解析，再次落地页
    candidates: list[str] = []
    candidates.append(pdf_url)
    # 若当前是 doi.org 兜底，先尝试 Unpaywall/OpenAlex 找 OA PDF 直链（force 绕过缓存）
    if "doi.org" in pdf_url and doi:
        try:
            oa = find_open_access_pdf(doi, title=paper.title or "", force=True)
            if oa and oa not in candidates:
                candidates.append(oa)
        except Exception:  # noqa: BLE001
            pass

    for url in candidates:
        try:
            content = _download(url)
            if _is_pdf(content):
                break
            # 不是 PDF → 可能返回了 HTML 落地页，尝试解析 citation_pdf_url
            tried.append(url)
            pdf_href = _extract_pdf_from_html(content, url)
            if pdf_href:
                try:
                    content = _download(pdf_href)
                    if _is_pdf(content):
                        break
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            tried.append(f"{url} ({e})")
            content = b""

    if not _is_pdf(content):
        # 最后兜底：用标题再解析一次（OpenAlex 标题反查 DOI + OA PDF）
        try:
            from core.tools.doi_resolve import resolve_pdf_link
            meta_tmp = {
                "title": paper.title or "",
                "authors": paper.authors or [],
                "year": paper.year,
                "arxiv_id": paper.arxiv_id or "",
                "doi": paper.doi or "",
                "pdf_url": "",
            }
            final_url = resolve_pdf_link(meta_tmp)
            if final_url and final_url not in tried:
                content = _download(final_url)
                if not _is_pdf(content):
                    pdf_href = _extract_pdf_from_html(content, final_url)
                    if pdf_href:
                        content = _download(pdf_href)
        except Exception:  # noqa: BLE001
            content = b""

    if not _is_pdf(content):
        detail = (
            "该论文没有可直链下载的 PDF（doi.org 跳转的是 HTML 文章页，"
            "且 OpenAlex/Unpaywall 未找到开放获取版本）。"
            "通常意味着论文在付费墙内，请前往原文链接查看。"
        )
        raise HTTPException(status_code=502, detail=detail)

    # 4. 保存本地并返回
    try:
        proj_dir = _CONFIG.paths.project_dir(project_id)
        pdf_dir = os.path.join(proj_dir, "papers")
        os.makedirs(pdf_dir, exist_ok=True)
        local_path = os.path.join(pdf_dir, f"{paper_id}.pdf")
        with open(local_path, "wb") as f:
            f.write(content)
        # 回写 pdf_path（存 DB）
        try:
            from core.knowledge.schema import Paper as _Paper
            _paper = _Paper(
                paper_id=paper.paper_id,
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                venue=paper.venue,
                arxiv_id=paper.arxiv_id,
                abstract=paper.abstract,
                doi=paper.doi,
                url=paper.url,
                pdf_path=local_path,
                metadata=paper.metadata,
                source_stage=paper.source_stage,
                created_at=paper.created_at,
            )
            store.save_paper(_paper)
        except Exception:  # noqa: BLE001
            pass  # 回写失败不影响本次下载
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"保存 PDF 失败: {e}") from e

    return FileResponse(
        local_path,
        media_type="application/pdf",
        filename=f"{paper.paper_id}.pdf",
    )


@app.get("/api/projects/{project_id}/claims")
def list_claims(project_id: str, status: Optional[str] = None) -> dict:
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        claims = store.list_claims()
    except Exception as e:  # noqa: BLE001
        return {"claims": [], "error": f"读取失败: {e}"}
    items = []
    for c in claims:
        # 争议检测：claim 证据引用的论文与冲突 sources 有交集 → 争议中
        related_conflicts = []
        try:
            related_conflicts = store.conflicts_for_claim(c.evidence_refs)
        except Exception:  # noqa: BLE001
            related_conflicts = []
        item = {
            "claim_id": c.claim_id,
            "statement": c.statement,
            "status": c.status.value,
            "role": c.role,
            "evidence_count": len(c.evidence_refs),
            "evidence_refs": c.evidence_refs,
            "source_idea_id": c.source_idea_id,
            "source_stage": c.source_stage,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "verified_at": c.verified_at.isoformat() if c.verified_at else None,
            # 冲突可视化：相关文献冲突（非空 → 该 Claim 处于争议中）
            "conflicts": [
                {
                    "conflict_id": x.conflict_id,
                    "claim": x.claim,
                    "sources": x.sources,
                    "resolution": x.resolution,
                    "confidence": x.confidence,
                    "subquery": x.subquery,
                }
                for x in related_conflicts
            ],
        }
        items.append(item)
    if status:
        items = [it for it in items if it["status"] == status]
    return {"claims": items}


@app.get("/api/projects/{project_id}/experiments")
def list_experiments(project_id: str) -> dict:
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        exps = store.list_experiments()
    except Exception as e:  # noqa: BLE001
        return {"experiments": [], "error": f"读取失败: {e}"}
    return {
        "experiments": [
            {
                "experiment_id": e.experiment_id,
                "name": e.name,
                "status": e.status.value,
                "verifies_claim_ids": e.verifies_claim_ids,
                "config": e.config,
                "metrics": e.metrics,
                "result_summary": e.result_summary,
                "anomaly_notes": e.anomaly_notes,
                "started_at": e.started_at.isoformat() if e.started_at else None,
                "completed_at": e.completed_at.isoformat() if e.completed_at else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in exps
        ]
    }


class RegenerateMethodDocRequest(BaseModel):
    """重新生成方法文档请求（无必填参数）。"""

    extra_hint: str = ""


class ExperimentBackfillRequest(BaseModel):
    """实验结果回填请求。

    results_json：用户粘贴/上传的 results.json 内容（字符串），
    结构 {"experiments": [{"name": ..., "metrics": {...}, "status": "success|failed|inconclusive"}]}。
    """
    results_json: str = Field(min_length=1, max_length=250000)


_METHOD_REGEN_JOBS: dict[str, dict] = {}
_METHOD_REGEN_LOCK = threading.Lock()


@app.post("/api/projects/{project_id}/regenerate-method-doc")
def regenerate_method_doc(project_id: str, req: Optional[RegenerateMethodDocRequest] = None) -> dict:
    """重新生成方法文档（异步任务，立即返回）。

    16384-token 的长文档生成经网关需数分钟，同步 HTTP 会超时——
    因此改为后台线程生成，前端轮询 GET /method-doc-status 取结果。
    """
    state = _require_project(project_id)
    _public_domain(state.domain)
    with _METHOD_REGEN_LOCK:
        job = _METHOD_REGEN_JOBS.get(project_id)
        if job and job.get("status") == "running":
            return {"project_id": project_id, "status": "running",
                    "message": "已有重新生成任务进行中，请稍后用状态接口查询"}
        if state.thread is not None and state.thread.is_alive():
            raise HTTPException(status_code=409, detail="当前科研任务仍在运行，请在阶段结束后再生成方法修订稿")
        _METHOD_REGEN_JOBS[project_id] = {
            "status": "running", "content": "", "message": "", "error": "",
        }

    def _run() -> None:
        with _METHOD_REGEN_LOCK:
            _METHOD_REGEN_JOBS[project_id] = {
                "status": "running", "content": "", "message": "", "error": "",
            }
        try:
            content = _generate_method_doc(project_id, (req.extra_hint if req else "") or "")
            from core.knowledge import ArtifactType
            am = _artifact_manager(project_id)
            store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
            claims = store.list_claims()[:8]
            previous = am.list_artifacts(ArtifactType.METHOD_DOC)
            if previous:
                artifact = am.new_version(previous[-1].artifact_group, content=content,
                                          cites_claim_ids=[c.claim_id for c in claims], created_by="web_regenerate")
            else:
                artifact = am.create_artifact(artifact_type=ArtifactType.METHOD_DOC, title="方法文档",
                                             content=content, cites_claim_ids=[c.claim_id for c in claims],
                                             source_stage="design", created_by="web_regenerate")
            with _METHOD_REGEN_LOCK:
                _METHOD_REGEN_JOBS[project_id] = {
                    "status": "done", "content": content, "artifact_id": artifact.artifact_id,
                    "message": "方法修订稿已保存为新版本；请复核，未自动替换旧实验方案", "error": "",
                }
        except Exception as e:  # noqa: BLE001
            with _METHOD_REGEN_LOCK:
                _METHOD_REGEN_JOBS[project_id] = {
                    "status": "failed", "content": "", "message": "",
                    "error": str(e),
                }

    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        with _METHOD_REGEN_LOCK:
            _METHOD_REGEN_JOBS[project_id] = {"status":"failed", "error":"后台生成任务未能启动"}
        raise HTTPException(status_code=503, detail="后台生成任务未能启动，请稍后重试")
    return {"project_id": project_id, "status": "running",
            "message": "重新生成已启动（约 3-6 分钟），请轮询状态接口"}


def _generate_method_doc(project_id: str, hint: str) -> str:
    """基于项目已有产物重建方法文档正文（供后台线程调用）。"""
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
    state = _PROJECTS.get(project_id)
    topic = str(store.get_kv("research.topic") or (state.topic if state else "") or "")

    idea_texts = [i.text for i in store.list_ideas()[:3] if i.text]
    claims = store.list_claims()
    claim_lines = [f"- {c.statement}（状态：{c.status.value}）" for c in claims[:8]]
    report = store.get_kv("cross_validation_report") or {}
    consensus = [str(x) for x in (report.get("consensus") or [])[:5]]

    prompt = (
        f"研究主题：{topic}\n\n"
        f"候选思路：\n" + ("\n".join(f"- {t[:300]}" for t in idea_texts) or "（无）") + "\n\n"
        f"核心 Claim：\n" + ("\n".join(claim_lines) or "（无）") + "\n\n"
        f"调研共识：\n" + ("\n".join(f"- {c}" for c in consensus) or "（无）") + "\n\n"
        + (f"补充要求：{hint}\n\n" if hint else "")
        + "请基于以上素材输出完整的方法文档（Markdown），包含：问题定义与符号表、"
        "方法概述、与已有方法对比、核心公式设计（LaTeX）、算法伪代码、复杂度分析、"
        "实验设计建议。素材缺失的小节请基于研究主题合理构造，不要输出空章节。"
    )
    registry = LLMRegistry.from_config(_CONFIG)
    resp = None
    for attempt in range(2):
        resp = registry.complete(
            task_type="design_method_formalize",
            system=(
                "你是科研方法形式化助手。基于给定素材整合完整方法文档，"
                "输出 Markdown 格式，不得输出空内容或省略章节。" + MATH_DOCUMENT_GUIDE
            ),
            prompt=prompt,
            # 推理模型经网关时 reasoning 也计入 max_tokens，2048 默认值
            # 必然被思考耗尽（finish=length、content 空）——直接给足预算，
            # 免去 registry 里"空内容→升级重试"浪费的一整轮等待。
            max_tokens_override=16384,
        )
        if resp is not None and (resp.text or "").strip():
            break
    content = (resp.text if resp else "").strip()
    if not content:
        raise RuntimeError("模型连续返回空内容，请稍后重试")
    return content


@app.get("/api/projects/{project_id}/method-doc-status")
def get_method_doc_status(project_id: str) -> dict:
    """查询方法文档重新生成任务的状态（running/done/failed/idle）。"""
    _require_project(project_id)
    with _METHOD_REGEN_LOCK:
        job = dict(_METHOD_REGEN_JOBS.get(project_id) or {})
    job.setdefault("status", "idle")
    return job


@app.get("/api/projects/{project_id}/experiment-tasks")
def get_experiment_tasks(project_id: str) -> dict:
    """读取实验任务包（README 任务说明书 + 规划清单），供前端实验页展示。"""
    _require_project(project_id)
    exp_dir = _CONFIG.paths.project_dir(project_id) / "experiments"
    readme_path = exp_dir / "README.md"
    plan_path = exp_dir / "experiment_plan.json"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    plan: dict = {}
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            plan = {}
    return {
        "has_tasks": bool(readme),
        "readme": readme,
        "plan": plan,
        "export_dir": str(exp_dir),
    }


@app.post("/api/projects/{project_id}/experiments/backfill")
def backfill_experiment_results(project_id: str, req: ExperimentBackfillRequest) -> dict:
    _require_project(project_id)
    raise HTTPException(status_code=422,detail=NOTICE)


def _backfill_experiment_results_internal(project_id: str, req: ExperimentBackfillRequest) -> dict:
    """回填实验结果：解析 results.json 结构，按 name 匹配更新实验状态与指标。

    status 映射：success → completed，failed → failed，inconclusive → anomaly_detected。
    """
    state = _require_project(project_id)
    if state.thread is not None and state.thread.is_alive():
        raise HTTPException(status_code=409, detail="当前任务仍在运行或等待确认，请在实验规划结束后回填结果")
    raw = (req.results_json or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="results_json 不能为空")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")
    items = data.get("experiments") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise HTTPException(
            status_code=400,
            detail='格式应为 {"experiments": [{"name": ..., "metrics": {...}, "status": "success"}]}',
        )

    store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
    existing = store.list_experiments()
    by_id = {e.experiment_id: e for e in existing}
    updated, pending, seen = [], [], set()
    status_map = {
        "success": ExperimentStatus.COMPLETED,
        "completed": ExperimentStatus.COMPLETED,
        "failed": ExperimentStatus.FAILED,
        "inconclusive": ExperimentStatus.ANOMALY_DETECTED,
    }
    if len(items) > 100:
        raise HTTPException(status_code=422, detail="单次最多回填 100 条实验结果")
    for index, item in enumerate(items, 1):
        try:
            submitted = SubmittedResult.model_validate(item)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=f"第 {index} 条结果无效：{exc.errors()[0]['msg']}；本批未保存") from exc
        matches = [e for e in existing if e.name == submitted.name.strip()]
        exp = by_id.get(submitted.experiment_id) if submitted.experiment_id else (matches[0] if len(matches) == 1 else None)
        if exp is None:
            reason = "名称重复，请指定 experiment_id" if len(matches) > 1 else "不存在对应实验"
            raise HTTPException(status_code=422, detail=f"第 {index} 条{reason}；本批未保存")
        if submitted.experiment_id and submitted.name and submitted.name.strip() != exp.name:
            raise HTTPException(status_code=422, detail=f"第 {index} 条任务编号与名称不一致；本批未保存")
        if exp.experiment_id in seen:
            raise HTTPException(status_code=422, detail="同一批不能重复提交同一个实验；本批未保存")
        seen.add(exp.experiment_id)
        if not set(submitted.claim_assessments) <= set(exp.verifies_claim_ids):
            raise HTTPException(status_code=422, detail="判断中包含本实验未关联的论断；本批未保存")
        exp = exp.model_copy(deep=True)
        exp.status = status_map[submitted.status]
        exp.metrics = submitted.metrics
        exp.completed_at = datetime.now()
        exp.config["user_claim_assessments"] = submitted.claim_assessments
        exp.anomaly_notes = submitted.summary if submitted.status in ("failed", "inconclusive") else None
        exp.result_summary = (
            f"用户提交，未经系统独立复现：status={submitted.status}，"
            f"metrics={json.dumps(exp.metrics, ensure_ascii=False)}\n{submitted.summary}"
        )
        pending.append(exp)
        updated.append(exp.name)

    with _LOCK:
        if state.thread is not None and state.thread.is_alive():
            raise HTTPException(status_code=409, detail="科研任务已启动，本次结果尚未保存，请等待任务结束后重试")
        store.save_experiments_batch(pending)
    state.summary = f"已保存 {len(updated)} 条用户实验结果。点击继续运行复核结果，不会重新生成实验任务。"
    if state.status == "completed":
        store.save_kv("writing.results_need_review", True)
        state.summary = "结果已更新，原稿件保留但需要核对修订；系统不会把旧稿自动当作新结果的结论。"

    return {
        "updated": updated,
        "unmatched": [],
        "message": state.summary,
    }


def _merged_project_notes(store: KnowledgeStore, state: ProjectState) -> list[dict]:
    """状态计数和笔记列表使用相同的持久化/内存去重口径，不修改存储。"""
    saved = store.get_kv("user.notes") or []
    known = {note["note_id"] for note in saved}
    return saved + [note for note in state.notes if note["note_id"] not in known]


@app.post("/api/projects/{project_id}/notes")
def add_note(project_id: str, req: NoteRequest) -> dict:
    state = _require_project(project_id)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")
    note = {
        "note_id": uuid.uuid4().hex,
        "text": text,
        "created_at": datetime.utcnow().isoformat(),
        "created_at": datetime.now().isoformat(),
    }
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id))
    notes = _merged_project_notes(store, state)
    notes.append(note)
    store.save_kv("user.notes", notes)
    state.notes = notes
    return {"note": note}


@app.get("/api/projects/{project_id}/notes")
def list_notes(project_id: str) -> dict:
    state = _require_project(project_id)
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
    state.notes = _merged_project_notes(store, state)
    return {"notes": list(state.notes)}


@app.post("/api/projects/{project_id}/human-response")
def submit_human_response(project_id: str, req: HumanResponseRequest) -> dict:
    state = _require_project(project_id)
    boundary=inspect_boundary(_CONFIG.paths,project_id,pending=_BRIDGE.get_pending(project_id),observed_status=state.status,observed_history=state.node_history)
    if boundary["restricted"]:
        raise HTTPException(status_code=422,detail=boundary["message"])
    action = req.action or "continue"
    if action not in ("continue", "abort", "rollback"):
        raise HTTPException(status_code=400, detail="action 必须为 continue/abort/rollback")
    pending = _BRIDGE.get_pending(project_id)
    if req.expected_node_id and (pending or {}).get("context", {}).get("node_id") != req.expected_node_id:
        raise HTTPException(status_code=409, detail="人工节点已变化，请刷新页面后重新确认")
    resp = HumanResponse(
        text=req.text,
        selected_option=req.selected_option,
        action=action,
    )
    # 检索偏好（年份/期刊）随人工响应透传给节点，并记录到项目状态
    if req.search_prefs:
        resp.context = {"search_prefs": req.search_prefs}
        state.search_prefs = dict(req.search_prefs)
        KnowledgeStore(_CONFIG.paths.project_db(project_id)).save_kv("web.search_prefs", state.search_prefs)
    ok = _BRIDGE.submit(project_id, resp)
    if not ok:
        # 没有等待中的人工请求：可能已提交、已超时、被清除，或多实例分裂
        raise HTTPException(
            status_code=409,
            detail=(
                "当前没有等待中的人工请求：可能已提交、已超时或被清除。"
                "请刷新页面查看最新状态。"
            ),
        )
    return {"project_id": project_id, "submitted": True, "action": action}


# ===== 调研报告接口 =====


@app.get("/api/projects/{project_id}/research-report")
def get_research_report(project_id: str) -> dict:
    """文献调研报告（research 阶段核心产出）。

    返回 cross_validation_report，含 Research Gaps / 共识 / 冲突 / 整体置信度。
    """
    _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        cv_report = store.get_kv("cross_validation_report")
    except Exception as e:  # noqa: BLE001
        return {"report": None, "error": f"读取失败: {e}"}
    if not cv_report:
        return {
            "report": None,
            "message": "尚未生成调研报告，请先运行 research 阶段（启动 Pipeline）",
        }
    # 补充论文计数
    try:
        paper_count = len(store.list_papers())
    except Exception:  # noqa: BLE001
        paper_count = 0
    return {
        "project_id": project_id,
        "report": cv_report,
        "paper_count": paper_count,
    }


@app.get("/api/projects/{project_id}/dashboard")
def get_dashboard(project_id: str) -> dict:
    """首页 Dashboard 聚合数据。

    一次返回所有计数 + 调研报告摘要，让前端 Dashboard 单次拉取即可渲染。
    """
    state = _require_project(project_id)
    try:
        store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)
        counts = {
            "papers": len(store.list_papers()),
            "ideas": len(store.list_ideas()),
            "claims": len(store.list_claims()),
            "experiments": len(store.list_experiments()),
            "gaps": store.gap_stats()["total"],
        }
        cv_report = store.get_kv("cross_validation_report") or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"读取失败: {e}"}

    # 方法对齐摘要（公式数）
    # 论文写作摘要
    writing_summary = {"artifact_count": 0}
    try:
        writing_summary["artifact_count"] = _writing_artifact_count(store)
    except Exception:  # noqa: BLE001
        pass

    # 阶段状态
    stage_statuses: dict[str, str] = {}
    current_stage = ""
    boundary = inspect_boundary(_CONFIG.paths,project_id)
    current_stage = historical_stage(boundary)
    stage_statuses = {s.value:boundary["stage_statuses"].get(s.value,"not_started") for s in LifecycleStage.ordered()}

    workflow = _workspace_workflow(state,store,stage_statuses)
    counts["writing_artifacts"] = writing_summary["artifact_count"]
    return {
        "project_id": project_id,
        "topic": state.topic,
        "domain": state.domain,
        "status": state.status,
        "summary": state.summary,
        "recommendation": state.recommendation,
        "advice": state.advice,
        "current_stage": current_stage,
        "stage_statuses": stage_statuses,
        "counts": counts,
        "research_report_summary": {
            "gaps_count": len(cv_report.get("gaps", [])),
            "consensus_count": len(cv_report.get("consensus", [])),
            "conflicts_count": len(cv_report.get("conflicts", [])),
            "overall_confidence": cv_report.get("overall_confidence", 0),
        },
        "writing_summary": writing_summary,
        "pending_human": _BRIDGE.get_pending(project_id),
        "product_stage":workflow["product_stage"],
        "product_boundary":workflow["product_boundary"],
        "continuation": workflow["continuation"],
        "workflow": workflow,
        "run_mode": state.run_mode,
    }


# ===== 产出物下载 =====


@app.get("/api/projects/{project_id}/download/{artifact_type}")
def download_artifact(project_id: str, artifact_type: str, format: str = "md", preserve_status: bool = False):
    """下载关键产出物，支持 md / docx / pdf 三种格式。

    支持类型：
    - research-report：文献调研报告
    - discovery-report：关系发现报告
    - experiment-tasks：实验任务说明书（experiment-code 为兼容别名）
    - experiment-results：实验结果（含 metrics）
    - method-doc：方法文档
    - paper-draft：论文稿
    - claims-summary：Claim 汇总
    - ideas-summary：研究思路汇总
    - full-report：全流程综合报告
    """
    _require_project(project_id)
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=True)

    # 实验任务说明书（系统不交付代码，交付物为任务说明书）
    if artifact_type in ("experiment-tasks", "experiment-code"):
        exp_dir = _CONFIG.paths.project_dir(project_id) / "experiments"
        readme_path = exp_dir / "README.md"
        if readme_path.exists():
            md_content = readme_path.read_text(encoding="utf-8")
        else:
            md_content = (
                "# 实验任务说明书\n\n> 尚未导出实验任务包，请先运行 experiment 阶段"
                "并在人工审核节点确认导出。"
            )
        if preserve_status:
            md_content = _download_status_note(store, project_id, artifact_type) + md_content
        return _make_download(md_content, "experiment_tasks.md", "text/markdown", format)

    # 其余类型先统一生成 Markdown 内容
    md_content = ""
    base_filename = ""

    if artifact_type == "research-report":
        report = store.get_kv("cross_validation_report") or {}
        md_content = _build_research_report_md(report, store)
        base_filename = "research_report"

    elif artifact_type == "discovery-report":
        content = store.get_kv("discovery_report_content") or ""
        content = strip_think_tags(content)
        if not content:
            summary = store.get_kv("discovery_summary") or {}
            if summary:
                content = (
                    "# 关系发现报告\n\n"
                    f"- 候选假设数：{summary.get('hypotheses', 0)}\n"
                    f"- 验证发现数：{summary.get('relationships', 0)}\n"
                    f"- 其中新颖发现：{summary.get('novel', 0)}\n\n"
                    "> 报告正文尚未生成（LLM 生成失败或 discovery_report 节点未运行），"
                    "以上为结构化摘要。"
                )
            else:
                content = (
                    "# 关系发现报告\n\n"
                    "> 尚未生成发现报告，请先运行「关系发现」流程。"
                )
        md_content = content
        base_filename = "discovery_report"

    elif artifact_type == "method-doc":
        try:
            from core.artifacts import ArtifactManager
            from core.knowledge import ArtifactType
            am = _artifact_manager(project_id,read_only=True)
            arts = am.list_artifacts(artifact_type=ArtifactType.METHOD_DOC)
            if arts:
                md_content = am.read_content(arts[-1]) or ""
                if not md_content.strip():
                    md_content = (
                        "# 方法文档为空\n\n"
                        "> 生成时模型返回了空内容。请在项目主页点击「重新生成方法文档」，"
                        "系统会基于已有调研产物重建并保存新版本。"
                    )
            else:
                md_content = "# 暂无方法文档\n\n> 尚未生成方法设计文档，请先运行 design 阶段。"
        except Exception:
            md_content = "# 暂无方法文档\n\n> 尚未生成方法设计文档，请先运行 design 阶段。"
        base_filename = "method_doc"

    elif artifact_type == "paper-draft":
        try:
            from core.artifacts import ArtifactManager
            from core.knowledge import ArtifactType
            am = _artifact_manager(project_id,read_only=True)
            arts = am.list_artifacts(artifact_type=ArtifactType.PAPER_DRAFT)
            if arts:
                md_content = am.read_content(arts[-1]) or str(arts[-1].content or "")
            else:
                md_content = "# 暂无 论文稿\n\n> 尚未生成论文稿，请先运行 writing 阶段。"
        except Exception:
            md_content = "# 暂无 论文稿\n\n> 尚未生成论文稿，请先运行 writing 阶段。"
        base_filename = "paper_draft"

    elif artifact_type == "claims-summary":
        md_content = _build_claims_summary_md(store)
        base_filename = "claims_summary"

    elif artifact_type == "ideas-summary":
        md_content = _build_ideas_summary_md(store)
        base_filename = "ideas_summary"

    elif artifact_type == "experiment-results":
        md_content = _build_experiment_results_md(store, project_id)
        base_filename = "experiment_results"

    elif artifact_type == "full-report":
        md_content = _build_full_report_md(store, project_id)
        base_filename = "full_report"

    else:
        raise HTTPException(status_code=400, detail=f"不支持的下载类型: {artifact_type}")

    if preserve_status:
        md_content = _download_status_note(store, project_id, artifact_type) + md_content
    # 按格式转换
    fmt = (format or "md").lower()
    if fmt == "docx":
        return _md_to_docx_response(md_content, base_filename)
    elif fmt == "pdf":
        return _md_to_pdf_response(md_content, base_filename)
    else:
        return _make_download(md_content, f"{base_filename}.md", "text/markdown", "md")


def _download_status_note(store, project_id, kind):
    historical = kind in ("experiment-tasks", "experiment-code", "experiment-results", "paper-draft", "claims-summary", "discovery-report")
    note = f"# 导出说明\n\n项目：{project_id}\n\n状态：{'历史产物，非本轮新增成果' if historical else '已有研究材料，尚待审阅'}\n\n> 本轮止于研究方案，保存、汇总及下载不表示已执行实验或结论得到验证。\n\n"
    fields = ("mode", "status", "data_mode", "run_mode", "dry_run", "is_demo", "demo", "is_teaching", "case_id", "simulated", "needs_review", "result_origin", "provenance")
    sources = []
    if kind == "research-report":
        sources.append(store.get_kv("cross_validation_report") or {})
    if kind == "experiment-results":
        sources.extend(e.config for e in store.list_experiments())
    for source in sources:
        if isinstance(source, dict):
            markers = {key:source[key] for key in fields if key in source}
            if markers:
                note += "原始状态／来源标记（保持原值，不补造真实性）：\n\n```json\n" + json.dumps(markers, ensure_ascii=False, indent=2) + "\n```\n\n"
    return note + "---\n\n"


def _build_research_report_md(report: dict, store: Optional[KnowledgeStore] = None) -> str:
    """把 cross_validation_report 转为 Markdown 调研报告。

    报告结构（完整版，满足辅助科研的「完整性 + 用词准确」）：
    1. 研究缺口（类型 / 陈述 / 优先级 / 可操作性 / 建议行动）
    2. 共识
    3. 文献冲突（陈述 / 来源立场 / 处置 / 置信度）
    4. 文献引用链（引用文献清单 + 结论引用溯源）
    5. 证据链（检索溯源轨迹：子问题 → 数据源 → 命中 → 入库）

    store 提供结构化实体（Material / ResearchGap / ResearchConflict 等）；
    report dict 作为兜底。二者皆可为空，函数始终返回一个结构完整的报告骨架。
    """
    lines: list[str] = ["# 文献调研报告", ""]

    # 主题与总体置信度
    topic = ""
    if store is not None:
        try:
            topic = store.get_kv("research_topic") or ""
        except Exception:
            topic = ""
    topic = topic or report.get("topic", "") or report.get("title", "")
    if topic:
        lines.append(f"> **研究主题**：{topic}")

    overall = report.get("overall_confidence")
    try:
        overall_str = f"{float(overall):.2f}" if overall is not None else "—"
    except (TypeError, ValueError):
        overall_str = str(overall)
    lines.append(f"> **综合置信度**：{overall_str}")
    lines.append(f"> **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # ===== 0. 领域脉络（写作方法论 M2：已有研究版图 3-5 句）=====
    landscape = str(report.get("landscape") or "").strip() if isinstance(report, dict) else ""
    if landscape:
        lines.append("## 领域脉络")
        lines.append("")
        for seg in landscape.split("\n"):
            seg = seg.strip()
            if seg:
                lines.append(seg)
        lines.append("")

    # ===== 统一拉取数据（供各章节复用）=====
    papers: list = []
    gaps_entities: list = []
    conflicts_entities: list = []
    evidence_entries: list = []
    evidence_summary: dict = {}
    if store is not None:
        try:
            papers = store.list_papers() or []
        except Exception:
            papers = []
        try:
            gaps_entities = store.list_research_gaps() or []
        except Exception:
            gaps_entities = []
        try:
            conflicts_entities = store.list_research_conflicts() or []
        except Exception:
            conflicts_entities = []
        try:
            evidence_entries = store.list_evidence(limit=500) or []
        except Exception:
            evidence_entries = []
        try:
            evidence_summary = store.evidence_stats() or {}
        except Exception:
            evidence_summary = {}
    paper_by_id: dict[str, Any] = {p.paper_id: p for p in papers}

    # ===== 1. 研究缺口 =====
    report_gaps = report.get("gaps", []) if isinstance(report, dict) else []
    if gaps_entities or report_gaps:
        lines.append("## 1. 研究缺口（Research Gaps）")
        lines.append("")
        if gaps_entities:
            for i, g in enumerate(gaps_entities, 1):
                actions = "；".join(g.suggested_actions) if g.suggested_actions else "—"
                mats = ", ".join(g.related_materials) if g.related_materials else "—"
                tag = (
                    f"{_cn(_GAP_TYPE_CN, g.gap_type)} · 优先级 {g.priority} · "
                    f"可操作 {_cn(_GAP_ACTIONABILITY_CN, g.actionability)}"
                )
                lines.append(f"{i}. **[{tag}]** {g.statement or '—'}")
                lines.append(f"   - 关联材料：{mats}")
                lines.append(f"   - 建议行动：{actions}")
                if g.detail:
                    lines.append(f"   - 说明：{g.detail}")
            lines.append("")
        else:
            for i, g in enumerate(report_gaps, 1):
                if isinstance(g, dict):
                    lines.append(f"{i}. {g.get('statement') or g.get('gap') or g}")
                else:
                    lines.append(f"{i}. {g}")
            lines.append("")

    # ===== 2. 共识 =====
    consensus = report.get("consensus", []) if isinstance(report, dict) else []
    if consensus:
        lines.append("## 2. 共识")
        lines.append("")
        for i, c in enumerate(consensus, 1):
            lines.append(f"{i}. {c}")
        lines.append("")

    # ===== 3. 文献冲突 =====
    report_conflicts = report.get("conflicts", []) if isinstance(report, dict) else []
    if conflicts_entities or report_conflicts:
        lines.append("## 3. 文献冲突")
        lines.append("")
        if conflicts_entities:
            for i, c in enumerate(conflicts_entities, 1):
                lines.append(f"### 冲突 {i}：{c.claim or '(未命名冲突)'}")
                lines.append("")
                try:
                    lines.append(f"- **置信度**：{float(c.confidence):.2f}")
                except (TypeError, ValueError):
                    lines.append(f"- **置信度**：{c.confidence}")
                for src in c.sources or []:
                    stance = src.get("stance", "?") if isinstance(src, dict) else "?"
                    pid = src.get("paper_id", src.get("title", "?")) if isinstance(src, dict) else str(src)
                    lines.append(f"- **来源立场**：`{_cn(_STANCE_CN, stance)}` — {pid}")
                if c.subquery:
                    lines.append(f"- **关联子问题**：{c.subquery}")
                if c.resolution:
                    lines.append(f"- **处置建议**：{c.resolution}")
                lines.append("")
        else:
            for i, c in enumerate(report_conflicts, 1):
                if isinstance(c, dict):
                    claim = c.get("claim") or c.get("topic") or c.get("description") or "(未命名冲突)"
                    lines.append(f"{i}. **{claim}**")
                    conf_val = c.get("confidence")
                    if conf_val is not None:
                        try:
                            lines.append(f"   - 置信度: {float(conf_val):.2f}")
                        except (TypeError, ValueError):
                            pass
                    for src in c.get("sources") or c.get("positions") or []:
                        lines.append(f"   - 观点/来源: {src}")
                    for pid in c.get("source_paper_ids") or []:
                        lines.append(f"   - 关联论文: {pid}")
                    if c.get("subquery"):
                        lines.append(f"   - 关联子问题: {c['subquery']}")
                    if c.get("resolution"):
                        lines.append(f"   - 处置: {c['resolution']}")
                else:
                    lines.append(f"{i}. {c}")
            lines.append("")

    # ===== 4. 文献引用链（Reference Chain）=====
    if papers:
        lines.append("## 4. 文献引用链（Reference Chain）")
        lines.append("")
        lines.append(
            "> 调研涉及的全部文献及其可溯源元数据，并汇总研究结论（缺口 / 冲突）"
            "对文献的引用，实现「结论 → 文献」双向溯源。"
        )
        lines.append("")
        lines.append("### 4.1 引用文献清单")
        lines.append("")
        lines.append("| # | 标题 | 作者（年份） | 期刊 · arXiv/DOI |")
        lines.append("|---|---|---|---|")
        for i, p in enumerate(papers, 1):
            authors = ", ".join(p.authors[:2]) if p.authors else "—"
            if len(p.authors) > 2:
                authors += " 等"
            year = p.year if p.year is not None else "—"
            ext_id = p.arxiv_id or p.doi or "—"
            title = re.sub(r"<[^>]+>", "", p.title or "").replace("\n", " ").strip() or "—"
            title = title[:60] + ("…" if len(title) > 60 else "")
            title = title.replace("|", "\\|")
            venue = re.sub(r"<[^>]+>", "", p.venue or "").replace("\n", " ").strip() or "—"
            venue = venue[:30] + ("…" if len(venue) > 30 else "")
            venue = venue.replace("|", "\\|")
            lines.append(
                f"| {i} | {title} | {authors}（{year}） | {venue} · {ext_id} |"
            )
        lines.append("")

        # 4.2 结论 → 文献引用溯源（缺口 / 冲突）
        cite_by_paper: dict[str, set[str]] = {}

        def _inc_cite(pid: Any, kind: str) -> None:
            if not pid:
                return
            cite_by_paper.setdefault(str(pid), set()).add(kind)

        for g in gaps_entities:
            for ev in (g.evidence or []):
                _inc_cite(ev.get("paper_id") if isinstance(ev, dict) else None, "研究缺口")
        for c in conflicts_entities:
            for src in (c.sources or []):
                _inc_cite(src.get("paper_id") if isinstance(src, dict) else None, "文献冲突")

        if cite_by_paper:
            lines.append("### 4.2 结论引用溯源")
            lines.append("")
            lines.append("| 标题 | 支撑的结论类型 |")
            lines.append("|---|---|")
            for pid, kinds in sorted(cite_by_paper.items()):
                detail = "、".join(sorted(kinds))
                pp = paper_by_id.get(pid)
                title = re.sub(r"<[^>]+>", "", (pp.title if pp is not None and pp.title else "")).replace("|", "\\|").strip() or "—"
                lines.append(f"| {title} | {detail} |")
            lines.append("")

    # ===== 5. 证据链（Evidence Chain / 检索溯源轨迹）=====
    if evidence_entries:
        lines.append("## 5. 证据链（检索溯源轨迹）")
        lines.append("")
        total_ev = evidence_summary.get("total") or len(evidence_entries)
        linked_ev = evidence_summary.get("linked", 0)
        by_source = evidence_summary.get("by_source") or {}
        src_str = "、".join(f"{k}×{v}" for k, v in by_source.items()) if by_source else "—"
        lines.append(f"- **证据条目总数**：{total_ev}（其中已关联入库：{linked_ev}）")
        lines.append(f"- **数据源分布**：{src_str}")
        lines.append("")
        lines.append("| # | 子问题 | 数据源 | 命中文献 | 证据分 | 入库 |")
        lines.append("|---|---|---|---|---|---|")
        shown = 0
        for e in evidence_entries:
            if shown >= 200:
                lines.append(
                    f"| … | 其余 {len(evidence_entries) - shown} 条从略（详见「证据链」面板） | | | | |"
                )
                break
            linked = "✓" if e.get("paper_id") else "—"
            title = (e.get("title") or "—")[:50].replace("|", "\\|").replace("\n", " ")
            score = e.get("evidence_score") or 0.0
            try:
                score_str = f"{float(score):.2f}"
            except (TypeError, ValueError):
                score_str = str(score)
            subq = (e.get("subquery") or "—")[:30].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {shown + 1} | {subq} | {e.get('source') or '—'} | {title} | "
                f"{score_str} | {linked} |"
            )
            shown += 1
        lines.append("")

    # 空报告兜底
    if len(lines) <= 4:  # 仅标题 + 概览
        lines.append("> 暂无调研产出。请先运行 research 阶段（启动 Pipeline）。")
        lines.append("")

    return "\n".join(lines)


def _make_download(content: str, filename: str, media_type: str, fmt: str = "md") -> Response:
    """构造文件下载响应。"""
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ===== 状态 / 术语中文映射（统一报告用词）=====

_CLAIM_STATUS_CN = {
    "draft": "草稿",
    "evidence_linked": "已关联证据",
    "verified": "已验证",
    "refuted": "已被反驳",
    "superseded": "已被取代",
}
_CLAIM_ROLE_CN = {
    "contribution": "核心贡献",
    "method": "方法",
    "assumption": "假设",
    "result": "结果",
}
_IDEA_STATUS_CN = {
    "draft": "草稿",
    "validated": "已验证",
    "rejected": "已否决",
    "adopted": "已采纳",
}
_EXPERIMENT_STATUS_CN = {
    "planned": "已规划",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
    "anomaly_detected": "检测到异常",
}
_GAP_TYPE_CN = {
    "contradiction": "矛盾结论",
    "unexplored": "未探索方向",
    "underexplored": "探索不足方向",
    "missing_link": "缺失知识连接",
    "missing_connection": "缺失知识连接",
    "data_gap": "数据缺口",
    "method_gap": "方法缺口",
}
_GAP_ACTIONABILITY_CN = {
    "high": "高",
    "medium": "中",
    "low": "低",
}
_STANCE_CN = {
    "support": "支持",
    "refute": "反驳",
}


def _cn(mapping: dict, key: Any) -> str:
    """英文枚举值 → 中文术语，未知或空则返回原值/占位。"""
    if key is None:
        return "—"
    s = str(key)
    return mapping.get(s, s)


def _build_claims_summary_md(store: KnowledgeStore) -> str:
    """构建 Claim 汇总 Markdown（中文术语 + 状态/角色映射）。"""
    claims = store.list_claims()
    md = "# Claim 汇总\n\n"
    md += f"共 **{len(claims)}** 条 Claim\n\n---\n\n"
    if not claims:
        md += "> 暂无 Claim，请先运行 design 阶段。\n\n"
        return md
    for c in claims:
        md += f"## {c.claim_id}\n\n"
        md += f"**陈述**：{c.statement}\n\n"
        md += f"**状态**：{_cn(_CLAIM_STATUS_CN, c.status.value if hasattr(c.status, 'value') else c.status)}\n\n"
        md += f"**角色**：{_cn(_CLAIM_ROLE_CN, c.role)}\n\n"
        if getattr(c, "created_at", None):
            md += f"**创建时间**：{c.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        if getattr(c, "verified_at", None):
            md += f"**验证时间**：{c.verified_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        if c.evidence_refs:
            md += "**证据链**：\n"
            for ref in c.evidence_refs:
                ev_type = _cn({"paper": "文献", "experiment": "实验"}, ref.get("type", "?"))
                md += f"- {ev_type}：{ref.get('id', '?')}"
                if ref.get("chunk_id"):
                    md += f"（chunk: {ref['chunk_id']}）"
                md += "\n"
        md += "\n---\n\n"
    return md


def _build_ideas_summary_md(store: KnowledgeStore) -> str:
    """构建研究思路汇总 Markdown（中文术语 + 结构化验证记录）。"""
    ideas = store.list_ideas()
    md = "# 研究思路汇总\n\n"
    md += f"共 **{len(ideas)}** 个思路\n\n---\n\n"
    if not ideas:
        md += "> 暂无研究思路，请先运行 ideation 阶段。\n\n"
        return md
    for idea in ideas:
        md += f"## {idea.idea_id}\n\n"
        md += f"**状态**：{_cn(_IDEA_STATUS_CN, idea.status)}\n\n"
        md += f"**思路**：{idea.text}\n\n"
        if idea.constraints:
            md += f"**约束条件**：{'；'.join(idea.constraints)}\n\n"
        if idea.source_paper_ids:
            md += f"**来源论文**：{', '.join(idea.source_paper_ids)}\n\n"
        if getattr(idea, "created_by", None):
            md += f"**提出者**：{idea.created_by}\n\n"
        if idea.validation_notes:
            md += "**验证记录**：\n"
            if isinstance(idea.validation_notes, dict):
                for k, v in idea.validation_notes.items():
                    md += f"- **{k}**：{v}\n"
            else:
                md += f"- {idea.validation_notes}\n"
            md += "\n"
        md += "---\n\n"
    return md


def _build_experiment_results_md(store: KnowledgeStore, project_id: str) -> str:
    """构建实验结果 Markdown（含 metrics，中文术语）。"""
    experiments = store.list_experiments()
    md = "# 实验结果汇总\n\n"
    md += f"共 **{len(experiments)}** 个实验\n\n---\n\n"
    if not experiments:
        md += "> 暂无实验记录，请先运行 experiment 阶段。\n\n"
        return md
    for exp in experiments:
        md += f"## {exp.name or exp.experiment_id}\n\n"
        md += f"**状态**：{_cn(_EXPERIMENT_STATUS_CN, exp.status.value if hasattr(exp.status, 'value') else exp.status)}\n\n"
        if hasattr(exp, 'metrics') and exp.metrics:
            md += "**评估指标**：\n"
            if isinstance(exp.metrics, dict):
                for k, v in exp.metrics.items():
                    md += f"- **{k}**：{v}\n"
            else:
                md += f"```\n{json.dumps(exp.metrics, ensure_ascii=False, indent=2)}\n```\n"
            md += "\n"
        if exp.result_summary:
            md += f"**结果摘要**：\n```\n{exp.result_summary[:3000]}\n```\n\n"
        if getattr(exp, 'anomaly_notes', None):
            md += f"**异常说明**：{exp.anomaly_notes[:1000]}\n\n"
        if exp.verifies_claim_ids:
            md += f"**验证的 Claim**：{', '.join(exp.verifies_claim_ids)}\n\n"
        md += "---\n\n"
    return md


def _build_full_report_md(store: KnowledgeStore, project_id: str) -> str:
    """构建全流程综合报告 Markdown。"""
    parts: list[str] = []

    # 标题
    topic = store.get_kv("research_topic") or "(未设置主题)"
    parts.append(f"# 科研项目全流程报告\n\n**研究主题**：{topic}\n\n**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n---\n\n")

    # 1. 调研报告
    report = store.get_kv("cross_validation_report") or {}
    if report:
        parts.append("---\n\n# 第一部分：文献调研\n\n")
        parts.append(_build_research_report_md(report, store))

    # 2. 研究思路
    ideas = store.list_ideas()
    if ideas:
        parts.append("\n\n---\n\n# 第二部分：研究思路\n\n")
        parts.append(_build_ideas_summary_md(store))

    # 3. Claim 汇总
    claims = store.list_claims()
    if claims:
        parts.append("\n\n---\n\n# 第三部分：Claim 汇总\n\n")
        parts.append(_build_claims_summary_md(store))

    # 4. 方法文档
    try:
        from core.artifacts import ArtifactManager
        from core.knowledge import ArtifactType
        am = _artifact_manager(project_id,read_only=True)
        arts = am.list_artifacts(artifact_type=ArtifactType.METHOD_DOC)
        if arts:
            content = am.read_content(arts[-1]) or str(arts[-1].content or "")
            parts.append("\n\n---\n\n# 第四部分：方法设计\n\n")
            parts.append(content)
    except Exception:
        pass

    # 5. 实验结果
    experiments = store.list_experiments()
    if experiments:
        parts.append("\n\n---\n\n# 第五部分：实验结果\n\n")
        parts.append(_build_experiment_results_md(store, project_id))

    # 6. 论文稿
    try:
        from core.artifacts import ArtifactManager
        from core.knowledge import ArtifactType
        am = _artifact_manager(project_id,read_only=True)
        arts = am.list_artifacts(artifact_type=ArtifactType.PAPER_DRAFT)
        if arts:
            content = am.read_content(arts[-1]) or str(arts[-1].content or "")
            parts.append("\n\n---\n\n# 第六部分：论文稿\n\n")
            parts.append(content)
    except Exception:
        pass

    # 7. 关系发现（可选子流程：MCTS + LLM 引导搜索）
    discovery_content = strip_think_tags(store.get_kv("discovery_report_content") or "")
    if not discovery_content:
        summary = store.get_kv("discovery_summary") or {}
        if summary:
            discovery_content = (
                "# 关系发现\n\n"
                f"- 候选假设数：{summary.get('hypotheses', 0)}\n"
                f"- 验证发现数：{summary.get('relationships', 0)}\n"
                f"- 其中新颖发现：{summary.get('novel', 0)}\n"
            )
    if discovery_content:
        parts.append("\n\n---\n\n# 第七部分：关系发现\n\n")
        parts.append(discovery_content)

    from core.workspace.service import WorkspaceService
    workspace = WorkspaceService(project_id,_CONFIG.paths.project_db(project_id),initialize_store=False)
    if workspace.load().items:
        parts.append("\n\n---\n\n# 第八部分：当前研究主线与用户记录\n\n")
        parts.append("以下含计划、骨架及可能的演示假设；以各记录标记为准，不将预期数据算作真实实验结果。\n\n")
        parts.append(workspace.export_markdown())

    return "".join(parts) if parts else "# 全流程报告\n\n（暂无产出）"


# ===== 格式转换：Markdown → DOCX / PDF =====


# ===== Markdown 解析辅助（表格 + 行内标记）=====


def _md_strip_inline(text: str) -> str:
    """去除行内 Markdown 标记（加粗/斜体/行内代码），供 DOCX/PDF 纯文本渲染。"""
    # 行内代码 `x`
    text = re.sub(r"`([^`]*)`", r"\1", text)
    # 加粗 **x** / __x__
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    # 斜体 *x*（避免误伤 ** 已处理后的残留）
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    return text.strip()


def _md_is_table_separator(line: str) -> bool:
    """判断是否为 Markdown 表格分隔行（如 |---|:--:|---|）。"""
    stripped = line.strip().replace(" ", "")
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    inner = stripped[1:-1]
    if not inner:
        return False
    cells = inner.split("|")
    return all(c and set(c) <= {"-", ":"} for c in cells)


def _md_split_table_row(line: str) -> list[str]:
    """Split Markdown columns without breaking TeX cardinality/norm expressions."""
    return [_md_strip_inline(cell) for cell in split_table_row(line)]

def _md_to_docx_response(md_content: str, base_filename: str) -> Response:
    """将 Markdown 转为 DOCX 并返回下载响应。

    支持：标题（#/##/###）、引用（>）、列表（-/*/数字）、代码块（```）、
    分隔线（---）、以及 Markdown 表格（| a | b |，含 |---| 分隔行）。
    """
    import io
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Pt

    doc = Document()
    # 设置默认字体
    style = doc.styles["Normal"]
    style.font.size = Pt(11)

    lines = md_content.split("\n")
    in_code_block = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 代码块
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            i += 1
            continue
        if in_code_block:
            p = doc.add_paragraph(line)
            p.style = doc.styles["Normal"]
            run = p.runs[0] if p.runs else p.add_run("")
            run.font.name = "Consolas"
            run.font.size = Pt(9)
            i += 1
            continue

        # Markdown 表格：| ... | 且下一行为 |---| 分隔行
        if stripped.startswith("|") and i + 1 < len(lines) and _md_is_table_separator(lines[i + 1]):
            header = _md_split_table_row(stripped)
            i += 2  # 跳过分隔行
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_md_split_table_row(lines[i]))
                i += 1
            ncols = len(header)
            table = doc.add_table(rows=1, cols=ncols)
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            # 表头加粗
            hdr_cells = table.rows[0].cells
            for j in range(ncols):
                hdr_cells[j].text = header[j] if j < len(header) else ""
                for para in hdr_cells[j].paragraphs:
                    for run in para.runs:
                        run.bold = True
            # 数据行
            for row in rows:
                cells = table.add_row().cells
                for j in range(ncols):
                    cells[j].text = row[j] if j < len(row) else ""
            doc.add_paragraph("")  # 表格后空行，避免与后续段落粘连
            continue

        # 标题
        if stripped.startswith("### "):
            doc.add_heading(_md_strip_inline(stripped[4:]), level=3)
        elif stripped.startswith("## "):
            doc.add_heading(_md_strip_inline(stripped[3:]), level=2)
        elif stripped.startswith("# "):
            doc.add_heading(_md_strip_inline(stripped[2:]), level=1)
        elif stripped.startswith("---"):
            doc.add_paragraph("─" * 40)
        elif stripped.startswith("> "):
            doc.add_paragraph(_md_strip_inline(stripped[2:]))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            doc.add_paragraph(_md_strip_inline(stripped[2:]), style="List Bullet")
        elif stripped and stripped[0].isdigit() and ". " in stripped[:5]:
            idx = stripped.index(". ")
            doc.add_paragraph(_md_strip_inline(stripped[idx + 2:]), style="List Number")
        elif stripped:
            doc.add_paragraph(_md_strip_inline(stripped))
        # 空行跳过
        i += 1

    buf = io.BytesIO()
    doc.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{base_filename}.docx"'},
    )


# 中文字体（SimHei/微软雅黑）缺失的符号字形 → 安全 ASCII/中文表示。
# 避免 PDF 里出现「方块/乱码」。注意：这些映射仅用于 PDF 纯文本渲染，
# 不影响 Markdown / DOCX 原文。
_PDF_GLYPH_MAP = {
    "\u2713": "[通过]",   # ✓ CHECK MARK
    "\u2714": "[通过]",   # ✔ HEAVY CHECK MARK
    "\u2717": "[未通过]",  # ✗ BALLOT X
    "\u2718": "[未通过]",  # ✘ HEAVY BALLOT X
    "\u26a0": "[!]",      # ⚠ WARNING SIGN
    "\u2605": "[*]",      # ★ BLACK STAR
    "\u2606": "[*]",      # ☆ WHITE STAR
    "\u2192": "->",       # → RIGHTWARDS ARROW
    "\u21d2": "=>",       # ⇒ RIGHTWARDS DOUBLE ARROW
    "\u2b06": "[上]",      # ⬆ UP ARROW
    "\u2b07": "[下]",      # ⬇ DOWN ARROW
    "\u2026": "...",      # … HORIZONTAL ELLIPSIS
    "\ufe0f": "",         # emoji 变体选择符（⚠️ → ⚠）
}


def _pdf_safe_text(text: str) -> str:
    """pdf 文本安全化：去除不可打印控制符，替换中文字体缺失的符号字形，
    保留完整内容与原有空格；长 URL、化学式与中文由 PDF 排版器换行。"""
    if text is None:
        return ""
    # 替换缺失字形（先做，避免 emoji 变体选择符干扰）
    for src, dst in _PDF_GLYPH_MAP.items():
        if src in text:
            text = text.replace(src, dst)
    # 保留常见标点与中文，剥离控制字符（\x00-\x1f 等）
    return "".join(ch for ch in text if ch >= " " or ch in "\n\r\t")


def _md_to_pdf_response(md_content: str, base_filename: str) -> Response:
    """将 Markdown 转为 PDF 并返回下载响应。

    使用 fpdf2 + Windows 系统中文字体（Microsoft YaHei / SimHei）。
    支持：标题（#/##/###）、引用（>）、列表（-/*）、代码块（```）、
    分隔线（---）、以及 Markdown 表格（| a | b |，含 |---| 分隔行）。
    """
    import io
    from fpdf import FPDF
    from fpdf.enums import MethodReturnValue, XPos, YPos

    class DeliveryPDF(FPDF):
        def set_font(self, family=None, style="", size=0):
            super().set_font(family, style, size)
            if self.page and self.font_family:
                # fpdf2 can select a CJK fallback inside q/Q yet mark the primary
                # font as globally selected, dropping subsequent ASCII. Public
                # text("") emits its font selection without adding text or moving.
                self.text(self.x, self.y, "")

        def footer(self):
            self.set_y(-12)
            self.set_font(font_name, size=8)
            self.set_text_color(105, 115, 125)
            self.cell(0, 4, f"{self.page_no()} / {{nb}}", align="C")

    pdf = DeliveryPDF(orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # 注册中文字体（Windows 系统字体）。
    # 注意：fpdf2 >= 2.7 已弃用 uni=True 参数，传入反而触发 TTC 中文渲染 bug
    # （multi_cell 抛 "Not enough horizontal space to render a single character"），
    # 故一律不传 uni；优先 simhei.ttf（单 TTF，最稳），再试 msyh.ttc / simsun.ttc。
    font_name = "chinese"
    font_paths = [
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    font_loaded = False
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                pdf.add_font(font_name, "", fp)
                # 注册粗体（fpdf2 table 表头默认加粗，需 B 样式字体）
                pdf.add_font(font_name, "B", fp)
                font_loaded = True
                break
            except Exception:
                continue
    if not font_loaded:
        # 兜底：用内置字体（不支持中文，但不会崩溃）
        font_name = "Helvetica"

    # Segoe keeps proportional Latin/formulas; the CJK font supplies Chinese glyphs.
    scientific_font = "C:/Windows/Fonts/segoeui.ttf"
    if os.path.isfile(scientific_font):
        pdf.add_font("scientific", "", scientific_font)
        scientific_bold = "C:/Windows/Fonts/segoeuib.ttf"
        pdf.add_font("scientific", "B", scientific_bold if os.path.isfile(scientific_bold) else scientific_font)
        if font_loaded:
            pdf.set_fallback_fonts([font_name])
        font_name = "scientific"

    pdf.set_font(font_name, size=11)

    lines = md_content.split("\n")
    in_code_block = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            i += 1
            continue
        if in_code_block:
            pdf.set_font(font_name, size=8)
            # Character wrapping preserves full code without overflowing the page.
            pdf.multi_cell(0, 5, _pdf_safe_text(stripped), align="L", wrapmode="CHAR", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font(font_name, size=11)
            i += 1
            continue

        # Markdown 表格：| ... | 且下一行为 |---| 分隔行
        if stripped.startswith("|") and i + 1 < len(lines) and _md_is_table_separator(lines[i + 1]):
            header = _md_split_table_row(stripped)
            i += 2  # 跳过分隔行
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_md_split_table_row(lines[i]))
                i += 1
            _draw_pdf_table(pdf, font_name, header, rows)
            pdf.ln(2)
            continue

        wrapmode = "CHAR" if re.search(r"[\u3400-\u9fff]", stripped) else "WORD"
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            size, line_height, before = {1: (18, 9, 6), 2: (14, 7, 4), 3: (12, 6, 3)}[level]
            title = _pdf_safe_text(_md_strip_inline(heading.group(2)))
            pdf.set_font(font_name, style="B", size=size)
            height = pdf.multi_cell(0, line_height, title, align="L", wrapmode=wrapmode, dry_run=True,
                                    output=MethodReturnValue.HEIGHT)
            # Keep the heading with at least two body lines, including paragraph spacing.
            if pdf.will_page_break(before + height + 18):
                pdf.add_page()
            if pdf.get_y() > pdf.t_margin + 1:
                pdf.ln(before)
            pdf.set_text_color(31, 50, 66)
            pdf.multi_cell(0, line_height, title, align="L", wrapmode=wrapmode, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2 if level < 3 else 1)
            pdf.set_text_color(0)
            pdf.set_font(font_name, size=11)
        elif stripped.startswith("---"):
            pdf.ln(3)
            pdf.set_draw_color(205, 214, 222)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(4)
        elif stripped.startswith("> "):
            pdf.set_font(font_name, size=10)
            pdf.multi_cell(0, 6, _pdf_safe_text(_md_strip_inline(stripped[2:])), align="L", wrapmode=wrapmode, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font(font_name, size=11)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            pdf.multi_cell(0, 6, f"- {_pdf_safe_text(_md_strip_inline(stripped[2:]))}", align="L", wrapmode=wrapmode, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        elif stripped:
            pdf.multi_cell(0, 6, _pdf_safe_text(_md_strip_inline(stripped)), align="L", wrapmode=wrapmode, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.ln(3)
        i += 1

    buf = io.BytesIO()
    pdf.output(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{base_filename}.pdf"'},
    )


def _draw_pdf_table(pdf: Any, font_name: str, header: list[str], rows: list[list[str]]) -> None:
    """用 fpdf2 table API 绘制 Markdown 表格。

    列宽按页面有效宽度均分；单元格文本过 _pdf_safe_text 防超宽字符抛异常；
    表头自动加粗（fpdf2 默认 heading 行为）。
    """
    from fpdf.fonts import FontFace

    ncols = max(len(header), 1)
    # 有效页面宽度均分到各列
    usable = pdf.epw  # effective page width (mm)
    col_widths = tuple(usable / ncols for _ in range(ncols))

    pdf.set_font(font_name, size=9)
    try:
        with pdf.table(col_widths=col_widths, text_align="LEFT", v_align="TOP",
                       line_height=5, padding=(1.5, 2),
                       headings_style=FontFace(emphasis="B", fill_color=(238, 242, 246))) as table:
            # 表头行
            head_row = table.row()
            for j in range(ncols):
                head_row.cell(_pdf_safe_text(header[j] if j < len(header) else ""))
            # 数据行
            for row in rows:
                data_row = table.row()
                for j in range(ncols):
                    cell_text = row[j] if j < len(row) else ""
                    data_row.cell(_pdf_safe_text(cell_text))
    except ValueError as exc:
        if "too high" not in str(exc):
            raise
        raise HTTPException(422, "表格单行超过 PDF 页面高度，未截断内容。请下载 Markdown 或 DOCX 获取完整材料。") from exc
    pdf.set_font(font_name, size=11)


# ===== 文件上传 =====


@app.post("/api/projects/{project_id}/upload-paper")
async def upload_paper(
    project_id: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    request_id: Optional[str] = Form(None, max_length=128),
):
    from core.workspace.uploads import UploadService,UploadError
    from starlette.concurrency import run_in_threadpool
    _require_project(project_id)
    raw = await file.read()
    rid = request_id or uuid.uuid4().hex
    filename = file.filename or "uploaded.txt"
    try:
        service = UploadService(_CONFIG.paths.project_db(project_id),project_id)
        result = await run_in_threadpool(service.process,rid,filename,raw,title,
            lambda capture,paper_id: _prepare_upload(project_id,raw,filename,title,capture,paper_id))
        return JSONResponse(status_code=202 if result["status"]=="processing" else 200,content=result)
    except UploadError as exc:
        raise HTTPException(exc.code,detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/upload-requests/{request_id}")
def upload_request_state(project_id: str,request_id: str):
    from core.workspace.uploads import UploadService,UploadError
    _require_project(project_id)
    try:
        return UploadService(_CONFIG.paths.project_db(project_id),project_id).status(request_id)
    except UploadError as exc:
        raise HTTPException(exc.code,detail=str(exc)) from exc


def _prepare_upload(project_id,raw,filename,title,store,paper_id):
    """上传 PDF/文本文献，入库为 Paper 实体。

    支持 .pdf / .txt / .md 文件。PDF 按现有解析能力提取片段，
    txt/md 保存正文片段，开头节选不是论文摘要或全文核验。
    """
    _require_project(project_id)
    db = _CONFIG.paths.project_db(project_id)
    if not db.is_file():
        raise HTTPException(404, detail="项目数据库不存在，未创建空白数据替代历史项目")
    if filename != Path(filename).name or any(c in filename for c in ("/", "\\", ":")):
        raise HTTPException(400, detail="文件名不能包含目录或路径，请重命名后上传")
    ext = Path(filename).suffix.lower()
    if ext not in (".pdf", ".txt", ".md"):
        raise HTTPException(400, detail=f"不支持的文件类型: {ext}")
    if not raw:
        raise HTTPException(400, detail="文件为空，请选择包含正文的文件")
    text_content = ""
    if ext in (".txt", ".md"):
        try:
            text_content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, detail="文本不是有效 UTF-8，请转换编码后重试") from exc
        if not text_content.strip():
            raise HTTPException(400, detail="文件没有可读取的正文")
    abstract = ""
    pdf_path = None
    parse_mode = "none"
    sections_count = 0
    figures_count = 0
    mineru_doc = None
    parse_error = None

    if ext == ".pdf":
        # PDF：保存到项目目录，调用 MinerU 解析结构化内容（推荐工具）
        upload_dir = _CONFIG.paths.project_dir(project_id) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        # A new upload must not overwrite the file backing an older paper.
        pdf_path = upload_dir / (paper_id + "_" + filename)
        pending_path = pdf_path.with_suffix(pdf_path.suffix+".part")
        with pending_path.open("wb") as pending_file:
            pending_file.write(raw)
            pending_file.flush()
            os.fsync(pending_file.fileno())
        os.replace(pending_path,pdf_path)

        # MinerU 解析（无 API key 时降级到 pypdf fallback）
        from core.tools import parse_pdf_with_mineru, mineru_is_available
        if mineru_is_available():
            try:
                mineru_doc = parse_pdf_with_mineru(pdf_path)
                parse_mode = mineru_doc.mode
                sections_count = len(mineru_doc.sections)
                figures_count = len(mineru_doc.figures)
                # 优先使用 MinerU 抽取的 title
                extracted_title = mineru_doc.title if mineru_doc.title else None
                # 拼接前 2000 字作为 abstract
                abstract = mineru_doc.full_text[:2000] if mineru_doc.full_text else ""
                if not abstract.strip():
                    parse_error = "PDF 已保存，但未提取到可读取正文，请补充文本或核对文件"
                # 把 MinerU 解析的结构化数据存入 KV（前端可视化与 discovery 阶段使用）
                store.save_kv(f"mineru_{paper_id}", {
                    "title": mineru_doc.title,
                    "sections_count": sections_count,
                    "figures_count": figures_count,
                    "tables_count": len(mineru_doc.tables),
                    "equations_count": len(mineru_doc.equations),
                    "references_count": len(mineru_doc.references),
                    "mode": parse_mode,
                    "sections": [
                        {"heading": s.heading, "level": s.level, "page": s.page}
                        for s in mineru_doc.sections[:30]
                    ],
                })
                if extracted_title and not title:
                    title = extracted_title
            except Exception:
                parse_mode = "error"
                abstract = ""
                parse_error = "PDF 已保存，但解析失败；未读取正文，请补充文本后讨论"
        else:
            parse_mode = "fallback"
            parse_error = "PDF 已保存，解析服务未启用；仅有元信息，请补充文本"

    elif ext in (".txt", ".md"):
        parse_mode = "text"
        abstract = text_content[:5000]

    from core.knowledge import Paper
    # 清理文件名/标题中可能残留的前端 HTML 标签
    clean_title = re.sub(r"<[^>]+>", "", (title or filename or "untitled")).strip()
    paper = Paper(
        paper_id=paper_id,
        title=clean_title,
        authors=[],
        abstract=abstract,
        url=None,
        pdf_path=str(pdf_path) if pdf_path else None,
        source_stage="upload",
        metadata={
            "parse_mode": parse_mode,
            "parse_error": parse_error,
            "reading_scope": (
                "用户上传文本：已保存正文片段，展示为开头节选，不等于论文摘要或已核验全文"
                if text_content else
                "PDF 提取片段：未确认全文完整性" if abstract else "仅元信息；未读取正文"
            ),
        },
    )
    store.save_paper(paper)

    # 审计轨迹：上传文献也写一条 evidence_log（source=manual），
    # 使「审计轨迹」能覆盖所有论文来源（上传/补录/检索），避免统计口径误解。
    try:
        store.log_evidence({
            "subquery": "文献上传",
            "source": "manual",
            "paper_id": paper_id,
            "title": clean_title,
            "external_id": "",
            "evidence_score": 0.0,
            "snippet": (abstract or "")[:300],
            "match_type": "manual_upload",
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("上传证据链落库失败: %s", e)

    # txt/md 内容切分为 chunk（PDF 由 MinerU 解析后也会按 section 切分）
    chunk_count = 0
    if ext in (".txt", ".md") and text_content:
        from core.tools import split_into_chunks
        from core.knowledge import PaperChunk
        text_chunks = split_into_chunks(text_content, max_tokens=500, overlap_tokens=50)
        paper_chunks = [
            PaperChunk(
                chunk_id=f"{paper_id}_c{tc.index}",
                paper_id=paper_id,
                chunk_index=tc.index,
                text=tc.text,
            )
            for tc in text_chunks
        ]
        if paper_chunks:
            store.save_paper_chunks(paper_chunks)
            chunk_count = len(paper_chunks)
    elif mineru_doc is not None and abstract:
        # MinerU 解析后按 section 切分 chunk（结构化优于字符切分）
        try:
            from core.knowledge import PaperChunk
            paper_chunks = []
            for i, sec in enumerate(mineru_doc.sections):
                if not sec.text or len(sec.text.strip()) < 20:
                    continue
                paper_chunks.append(PaperChunk(
                    chunk_id=f"{paper_id}_s{i}",
                    paper_id=paper_id,
                    chunk_index=i,
                    text=sec.text[:1500],
                ))
                if len(paper_chunks) >= 30:  # 上限 30 section chunks
                    break
            if paper_chunks:
                store.save_paper_chunks(paper_chunks)
                chunk_count = len(paper_chunks)
        except Exception:
            pass

    return {
        "paper_id": paper_id,
        "title": paper.title,
        "filename": filename,
        "chunks": chunk_count,
        "parse_mode": parse_mode,
        "sections_count": sections_count,
        "figures_count": figures_count,
        "reading_scope": paper.metadata["reading_scope"],
        "parse_error": parse_error,
        "message": parse_error or "文献上传成功，可基于已保存片段讨论",
    }


@app.post("/api/projects/{project_id}/upload-topic")
async def upload_topic(
    project_id: str,
    file: UploadFile = File(...),
):
    """上传主题描述文件（.txt/.md），覆盖项目主题。"""
    state = _require_project(project_id)
    raw = await file.read()
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise HTTPException(status_code=400, detail="文件内容为空")
    state.topic = text
    return {"project_id": project_id, "topic": text, "message": "主题已更新"}


# ===== 辅助 =====


def _artifact_manager(project_id: str, *, read_only=False):
    """构造项目级 ArtifactManager（元数据 store + 内容文件 store）。

    此前多处 ArtifactManager(db_path) 少传 content_store，一调用就 TypeError
    被 except 吞掉，导致方法文档/论文稿永远显示「暂无」。
    """
    from core.artifacts import ArtifactContentStore, ArtifactManager
    store = KnowledgeStore(_CONFIG.paths.project_db(project_id),read_only=read_only)
    content_store = ArtifactContentStore(_CONFIG.paths.project_artifacts(project_id))
    return ArtifactManager(store, content_store)


def _require_project(project_id: str) -> ProjectState:
    with _LOCK:
        if project_id not in _PROJECTS and re.fullmatch(r"[A-Za-z0-9_-]{1,120}", project_id):
            if _CONFIG.paths.project_db(project_id).is_file():
                _scan_existing_projects()
        state = _PROJECTS.get(project_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"项目不存在: {project_id}"
                "。请核对项目编号及当前服务的数据目录；不要通过新建项目覆盖原项目。"
            ),
        )
    if not _CONFIG.paths.project_db(project_id).is_file():
        raise HTTPException(404,"项目数据库不存在，未创建空白数据替代历史项目")
    return state


# ===== 静态资源 =====


from web.workspace_api import build_workspace_router  # noqa: E402
from web.conversation_home_api import build_home_router  # noqa: E402
from web.project_exports_api import build_exports_router  # noqa: E402
from web.runtime_status_api import build_runtime_status_router  # noqa: E402


def _register_conversation_project(pid, topic, domain, created_at):
    with _LOCK:
        if pid not in _PROJECTS:
            store = KnowledgeStore(_CONFIG.paths.project_db(pid),read_only=True)
            _PROJECTS[pid] = ProjectState(project_id=pid,topic=store.get_kv("research.topic",topic),domain=store.get_kv("project.domain",domain),created_at=created_at,
                                          display_name=store.get_kv("project.display_name",topic))


app.include_router(build_home_router(lambda:_CONFIG,_require_project,_register_conversation_project))
app.include_router(build_runtime_status_router(lambda:_CONFIG))
app.include_router(build_exports_router(lambda:_CONFIG, _require_project,
    lambda state:_workspace_workflow(state,include_materials=True), download_artifact,
    _make_download, _md_to_docx_response, _md_to_pdf_response))

app.include_router(build_workspace_router(_CONFIG, _require_project,workflow_provider=lambda state:_workspace_workflow(state,include_materials=True)))


@app.middleware("http")
async def teaching_execution_boundary(request, call_next):
    """Old execution/import endpoints cannot turn a saved lesson into live work."""
    import re
    from fastapi.responses import JSONResponse
    from core.workspace.conversations import ConversationService
    from core.workspace.teaching import teaching_identity
    match = re.match(r"^/api/projects/([A-Za-z0-9_-]+)/(.+)$", request.url.path)
    if request.method in ("POST", "PUT") and match:
        pid, tail = match.groups()
        blocked = tail in {"run", "resume", "run-topic-discovery", "regenerate-method-doc", "human-response", "papers/import", "upload-paper", "upload-topic", "workspace/guide"} or "research-tasks" in tail
        db = _CONFIG.paths.project_db(pid)
        if blocked and db.is_file():
            try:
                if teaching_identity(ConversationService(pid, db)):
                    return JSONResponse(status_code=422, content={"detail": "教学体验不执行实时检索、生成或实验。请明确转为自己的普通研究后操作。"})
            except ValueError:
                pass  # The owning endpoint reports storage/ownership errors.
    return await call_next(request)


@app.get("/workspace")
def research_workspace() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/data-sources")
def list_data_sources() -> dict:
    """外部数据源合规登记。

    数据源元信息集中登记在 `core/tools/data_provenance.py`，
    本端点暴露给前端 dashboard 的"数据源合规性"卡片。
    """
    from core.tools import data_sources_summary, list_sources
    return {
        "sources": list_sources(),
        "summary": data_sources_summary(),
    }


@app.get("/static/{file_path:path}")
def static_files(file_path: str) -> FileResponse:
    """静态资源（强制 no-cache，避免前端更新后用户仍看到旧版缓存导致样式错乱）。"""
    target = (STATIC_DIR / file_path).resolve()
    if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        target,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
# 挂载静态目录（/static/...）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn

    # 优先取 SRA_WEB_PORT；Render/Fly 等 PaaS 注入 $PORT 时兼容使用
    port = int(os.environ.get("SRA_WEB_PORT") or os.environ.get("PORT") or "8001")
    uvicorn.run("web.api:app", host="0.0.0.0", port=port, reload=False)
