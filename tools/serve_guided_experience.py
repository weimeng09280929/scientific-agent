"""Explicitly approved, isolated Stage 5 acceptance on the production app."""
from __future__ import annotations

import argparse
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re

from core.invocation_audit import CallLimitError
from tools.serve_budgeted_local import MODEL, PRICE_URL, ExperienceBudget, ExperiencePolicy, tokendance_chat_endpoint

ROOT = Path(__file__).resolve().parents[1] / ".pytest_cache" / "real-user-acceptance-stage5"
APPROVED_POST = re.compile(r"/api/projects/[^/]+/conversations/[^/]+/(?:messages|requests/[^/]+/retry)")
BLOCKED_POST = re.compile(r"/(?:research-tasks|run|resume|run-topic-discovery|restart)(?:/|$)")
THINK_TAG = re.compile(r"</?think\b[^>]*>", re.I)


def load_tokendance_endpoint() -> str:
    from runtime.cli import _load_env

    _load_env()
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
        if not os.environ.get(name, "").strip():
            raise ValueError(f"TokenDance 配置缺少 {name}")
    return tokendance_chat_endpoint(os.environ["DEEPSEEK_BASE_URL"])


def tokendance_price_snapshot(data):
    # The TokenDance catalog UI displays these rates in yuan. Its current JSON
    # omits the currency field, but any explicit currency must agree with it.
    pricing = data["pricing"]
    if any(entry.get("currency", "CNY") != "CNY" for entry in (data, pricing)):
        raise ValueError("TokenDance 目录未使用人民币计费单位")
    return {**data, "pricing": {**pricing, "currency": "CNY"}}


def recorded_round_root(base, policy):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,15}", policy.session):
        raise ValueError("新轮次编号格式不合法")
    root = base / "rounds" / policy.session
    record = root / "authorization.json"
    if root.resolve() != root or record.resolve() != record or not record.is_file():
        raise ValueError("没有该轮独立、可核对的用户授权记录")
    approval = json.loads(record.read_text(encoding="utf-8-sig"))
    expected = {
        "authorization_id": policy.session, "provider": "TokenDance", "model": policy.model,
        "endpoint": policy.endpoint, "listen": "127.0.0.1:8125", "cost_cny": policy.cost_limit,
        "network_attempts": policy.attempts, "request_bytes": policy.request_bytes,
        "output_tokens": policy.output_tokens, "input_cny": policy.pricing["input"],
        "output_cny": policy.pricing["output"],
    }
    issued = datetime.fromisoformat(approval["received_at_utc"])
    deadline = datetime.fromisoformat(approval["expires_at_utc"])
    if (any(key not in approval or approval[key] != value for key, value in expected.items())
            or issued.utcoffset() is None or deadline.utcoffset() is None
            or deadline != datetime.fromisoformat(policy.expires_at)
            or deadline - issued != timedelta(minutes=90)
            or not issued <= datetime.now(timezone.utc) < deadline):
        raise ValueError("启动参数与该轮用户授权或原始截止时间不一致")
    if not (root / "budget.sqlite").is_file() and any((root / name).exists() for name in ("data", "receipts")):
        raise ValueError("已有该轮数据但缺少原账本，拒绝重置额度")
    return root


def visible_content(content, credential):
    if not isinstance(content, str):
        return None
    parts, depth, offset = [], 0, 0
    for tag in THINK_TAG.finditer(content):
        if depth == 0:
            parts.append(content[offset:tag.start()])
        depth += -1 if tag[0].startswith("</") else 1
        if depth < 0:
            return None  # A leading reasoning segment may lack its opening tag.
        offset = tag.end()
    if depth == 0:
        parts.append(content[offset:])
    visible = "".join(parts)
    if re.search(r"</?think\b", visible, re.I):
        return None
    return visible.replace(credential, "[已移除凭据]") if credential else visible


def install_guard(app, budget, *, receipts=None, capture_inputs=False):
    """A generation HTTP action may reserve once, including work offloaded from it."""
    from fastapi.responses import JSONResponse

    origin = ContextVar("guided_acceptance_origin", default=None)

    def admit():
        allowance = origin.get()
        if allowance is None or allowance:
            raise CallLimitError("本轮只允许明确网页操作的一次模型网络尝试", "experience_scope")
        allowance.append(True)  # also visible to a worker receiving copy_context()

    from core.invocation_audit import ACTIVE

    credential = os.environ.get("DEEPSEEK_API_KEY", "")
    if capture_inputs:
        if receipts is None:
            raise ValueError("该轮必须提供独立输入证据目录")
        inputs = receipts / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)

        def capture_input(request, ticket):
            payload = json.loads(request.content)
            # Payload shape and route were already checked by the shared ledger.
            if any((credential and credential in message["content"])
                   or re.search(r"</?think\b", message["content"], re.I) for message in payload["messages"]):
                raise CallLimitError("请求包含不可归档内容，未发往模型", "experience_scope")
            audit = ACTIVE.get()
            envelope = {
                "attempt_id": ticket[0], "operation_id": audit.operation_id if audit else None,
                "request_bytes": len(request.content), "request": payload,
            }
            with (inputs / (ticket[0] + ".json")).open("x", encoding="utf-8") as stream:
                json.dump(envelope, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())

    original = budget.install(admit=admit, before_send=capture_input if capture_inputs else None)
    if receipts is not None:
        from core.llm.providers.openai_provider import OpenAIProvider

        receipts.mkdir(exist_ok=True)
        finish = OpenAIProvider._after_http

        def capture(provider, response):
            finish(provider, response)
            ticket = next((t for identity, scope, t in provider._pending.get() or []
                           if identity == id(response.request) and scope is budget.scope), None)
            if not ticket:
                return
            info = {"status": response.status_code, "actual_model": None}
            audit = ACTIVE.get()
            info["operation_id"] = audit.operation_id if audit else None
            info["request_bytes"] = len(response.request.content)
            info["max_tokens"] = json.loads(response.request.content)["max_tokens"]
            if response.is_success:
                try:
                    data = response.json()
                    info.update(actual_model=data.get("model"), usage=data.get("usage"), choices=[
                        {"finish_reason": item.get("finish_reason"),
                         "content": visible_content((item.get("message") or {}).get("content"), credential)}
                        for item in data.get("choices", [])
                    ])
                except (ValueError, AttributeError, TypeError):
                    info["response_error"] = "provider_response_not_json"
            # No request headers, prompts, provider error bodies, or hidden reasoning.
            (receipts / (ticket[0] + ".json")).write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8",
            )

        OpenAIProvider._after_http = capture

    @app.middleware("http")
    async def authorized_surface(request, call_next):
        path = request.url.path
        if request.method == "POST" and path.startswith("/api/projects/") and BLOCKED_POST.search(path):
            return JSONResponse({"detail": "本轮只验收已导入论文，不启动检索、流水线或新恢复轮次"}, status_code=403)
        token = origin.set([] if request.method == "POST" and APPROVED_POST.fullmatch(path) else None)
        try:
            return await call_next(request)
        finally:
            origin.reset(token)

    return original


def configure_isolation(root, port, model):
    from core.config import GlobalConfig
    from core.llm.task_router import TaskRouter
    from tools.serve_local import configure, disable_live_calls
    import core.tools as paper_tools
    from core.research_tasks.service import ResearchTaskService, TaskConflict

    config_file = root / "tasks.yaml"
    source = GlobalConfig.load().llm.tasks_config_path
    config_file.write_bytes(source.read_bytes())
    router = TaskRouter.load(config_file)
    tasks = router.list_tasks()
    if not tasks or any((router.get(task).provider, router.get(task).model) != ("deepseek", model) for task in tasks):
        raise ValueError("日常任务路由与验收范围不符，未改写模型")
    configure("accounts", port, root / "data", True)
    os.environ.update({
        "SRA_DRY_RUN": "false", "SRA_TASKS_CONFIG_PATH": str(config_file),
        "SRA_GUIDED_SINGLE_MODEL_ATTEMPT": "1",
        "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "LOCAL_LLM_BASE_URL": "",
        "MINIMAX_API_KEY": "", "MIMO_API_KEY": "", "SPARK_API_KEY": "", "SILICONFLOW_API_KEY": "",
    })
    disable_live_calls(allow_model=True, allow_sources=False)
    paper_tools.mineru_is_available = lambda: False  # choose existing local pypdf path

    def research_disabled(*args, **kwargs):
        raise TaskConflict("本轮只验收已导入论文，外部检索未启用")

    # Also protect implicit dispatch from a normal message.
    ResearchTaskService.start = research_disabled
    ResearchTaskService.retry = research_disabled


def disable_automatic_attempts():
    from core.config import get_config
    from core.llm import LLMRegistry

    get_config().llm.max_retries = 0
    structured = LLMRegistry.structured_output

    def once(self, *args, **kwargs):
        kwargs["automatic_retry"] = False
        return structured(self, *args, **kwargs)

    LLMRegistry.structured_output = once


def probe_plan_schema_once():
    """One recorded, normal website plan request tests the endpoint format."""
    from core.invocation_audit import span
    from core.llm.providers.openai_provider import OpenAIProvider
    from core.workspace.plan_generation import PlanOutput
    from threading import Lock

    original = OpenAIProvider.structured_output
    pending = True
    lock = Lock()

    def probe(self, request, model):
        nonlocal pending
        with lock:
            selected = pending and issubclass(request.output_schema, PlanOutput)
            if selected:
                pending = False
        if not selected:
            return original(self, request, model)
        # No fallback or second request. The existing transport guard records
        # this request and its response in the same acceptance ledger.
        response = self._request(
            self._client.chat.completions.create,
            model=model, messages=self._build_messages_for_structured(request, request.output_schema),
            temperature=request.temperature, max_tokens=request.max_tokens,
            response_format={"type": "json_schema", "json_schema": {
                "name": "research_plan", "strict": True,
                "schema": request.output_schema.model_json_schema(),
            }},
            automatic_retry=False,
        )
        with span("phase", "model", "structure_validation"):
            return self._parse_structured(response, request)

    OpenAIProvider.structured_output = probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--experience-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input-cny", type=float, required=True)
    parser.add_argument("--output-cny", type=float, required=True)
    amount = parser.add_mutually_exclusive_group(required=True)
    amount.add_argument("--cost-cny", type=float)
    amount.add_argument("--no-cost-cap", action="store_true")
    parser.add_argument("--attempts", type=int, required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--request-bytes", type=int, required=True)
    parser.add_argument("--output-tokens", type=int, required=True)
    parser.add_argument("--recorded-round", action="store_true",
                        help="只从该轮授权文件启动，并留存实际模型输入正文")
    parser.add_argument("--probe-plan-schema", action="store_true",
                        help="仅一次正常网页方案请求试验 json_schema，失败不自动降级或补发")
    args = parser.parse_args()
    if not args.allow_live or args.port != 8125:
        parser.error("需明确授权并使用独立回环端口 8125")
    if args.no_cost_cap and not args.recorded_round:
        parser.error("不设累计金额上限必须有独立轮次的明确授权记录")
    if (args.model != MODEL or (args.cost_cny is not None and args.cost_cny > 5)
            or args.attempts > (24 if args.no_cost_cap else 16)
            or args.request_bytes > 160000 or args.output_tokens > 12000):
        parser.error("模型或请求上限超出本轮验收范围")
    try:
        deadline = datetime.fromisoformat(args.expires_at)
        now = datetime.now(timezone.utc)
        if deadline.utcoffset() is None or not now < deadline <= now + timedelta(minutes=90):
            raise ValueError("需提供已批准且未到期的绝对截止时间，剩余期限最多 90 分钟")
        policy = ExperiencePolicy(
            session=args.experience_id, model=args.model, endpoint=load_tokendance_endpoint(),
            pricing={"input": args.input_cny, "output": args.output_cny,
                     "basis": PRICE_URL + " (TokenDance peak reference, CNY/M tokens)"},
            cost_limit=args.cost_cny, attempts=args.attempts, expires_at=args.expires_at,
            request_bytes=args.request_bytes, output_tokens=args.output_tokens,
        )
        root = recorded_round_root(ROOT, policy) if args.recorded_round else ROOT
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    if root.resolve() != root or any((root / name).resolve() != root / name
                                    for name in ("data", "budget.sqlite", "tasks.yaml", "receipts", "receipts/inputs")):
        parser.error("隔离目录或账本路径不安全")
    import requests
    try:
        response = requests.get(PRICE_URL, timeout=20, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("未取得 TokenDance 公开价格")
        price_data = tokendance_price_snapshot(response.json())
        budget = ExperienceBudget(root / "budget.sqlite", price_data, policy=policy)
    except (requests.RequestException, ValueError, IndexError, KeyError):
        parser.error("无法复核价格或既有批准账本，未启用真实调用")
    configure_isolation(root, args.port, args.model)
    disable_automatic_attempts()
    from web.api import app, _CONFIG
    from core.llm.task_router import TaskRouter
    import uvicorn

    router = TaskRouter.load(_CONFIG.llm.tasks_config_path)
    if any((router.get(task).provider, router.get(task).model) != ("deepseek", args.model)
           for task in router.list_tasks()):
        parser.error("业务代码实际加载的路由与批准不一致")
    install_guard(app, budget, receipts=root / "receipts", capture_inputs=args.recorded_round)
    if args.probe_plan_schema:
        probe_plan_schema_once()
    print(f"Isolated acceptance on 127.0.0.1:{args.port}; approved until {policy.expires_at}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
