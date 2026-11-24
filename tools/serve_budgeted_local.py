"""Opt-in local account experience; one durable CNY 2 reservation across projects.

This launcher supplements, rather than replaces, project request/audit limits.
No model request is sent by startup. Rates are checked against public metadata.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3

from core.invocation_audit import AuditScope, CallLimitError

MODEL = "deepseek-v4.1-flash-1017"
PRICE_URL = "https://www.tokendance.space/portal/api/models/" + MODEL
SESSION = "user-experience-20261029-2cny"
PRICING = {"input": 2.0, "output": 8.0, "basis": PRICE_URL + " (undiscounted peak, CNY/M tokens)"}


def tokendance_chat_endpoint(base_url: str) -> str:
    base_url = base_url.removesuffix("/")
    if base_url not in ("https://tokendance.space/gateway/v1", "https://www.tokendance.space/gateway/v1"):
        raise ValueError("DEEPSEEK_BASE_URL 未指向受支持的 TokenDance 网关")
    return base_url + "/chat/completions"


def validate_prices(data, *, model=MODEL, pricing=PRICING, require_cny=False):
    if data.get("slug") != model:
        raise ValueError("模型价格资料不匹配，未启用真实调用")
    published = data.get("pricing", {})
    groups = [published.get("items", []), published.get("reference", [])]
    timed = published.get("time_pricing", {})
    groups += [timed.get("base_items", []), timed.get("base_reference", [])]
    for option in timed.get("options", []):
        groups += [option.get("items", []), option.get("reference", [])]
    for suffix, bound in (("input_tokens", pricing["input"]), ("completion_tokens", pricing["output"])):
        plans = [plan for group in groups for item in group
                 if item.get("id") == "openai:chat-completions:" + suffix
                 for plan in item.get("plans", [])]
        if (not plans or any(plan.get("unit") != "millionTokens" or not 0 < float(plan["rate"]) <= bound
                             for plan in plans)):
            raise ValueError("公开单价缺失或超过费用预留单价，未启用真实调用")
        if require_cny and any(plan.get("currency", published.get("currency")) != "CNY" for plan in plans):
            raise ValueError("公开单价未能核实为人民币，未启用真实调用")


@dataclass(frozen=True)
class ExperiencePolicy:
    session: str
    model: str
    endpoint: str
    pricing: dict
    cost_limit: float | None
    attempts: int
    expires_at: str
    request_bytes: int
    output_tokens: int

    def __post_init__(self):
        deadline = datetime.fromisoformat(self.expires_at)
        suffix = "/chat/completions"
        if (not self.endpoint.endswith(suffix)
                or tokendance_chat_endpoint(self.endpoint.removesuffix(suffix)) != self.endpoint):
            raise ValueError("验收端点与 TokenDance 网关不匹配")
        if (not self.session or self.model != MODEL
                or deadline.utcoffset() is None
                or not all(math.isfinite(x) and x > 0 for x in (self.pricing["input"], self.pricing["output"]))
                or self.cost_limit is not None and (not math.isfinite(self.cost_limit) or self.cost_limit <= 0)
                or not 1 <= self.attempts <= 100 or not 1 <= self.request_bytes <= 200_000
                or not 1 <= self.output_tokens <= 32768):
            raise ValueError("验收预算、有效期或请求上限不合法")


class ExperienceBudget:
    def __init__(self, path: Path, price_data: dict, *, policy: ExperiencePolicy | None = None):
        self.policy = policy
        model = policy.model if policy else MODEL
        pricing = policy.pricing if policy else PRICING
        cost_limit = policy.cost_limit if policy else 2
        attempts = policy.attempts if policy else 100
        validate_prices(price_data, model=model, pricing=pricing, require_cny=policy is not None)
        # One fixed slot per guided ledger. Changing the run ID cannot reset it.
        slot = "guided-acceptance" if policy else SESSION
        expected = {"cost_limit": cost_limit, "pricing": pricing}
        if policy:
            expected["authorization"] = asdict(policy)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS experience_config (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            row = conn.execute("SELECT data FROM experience_config WHERE id=?", (slot,)).fetchone()
            if row:
                self.config = json.loads(row[0])
                if any(self.config.get(key) != value for key, value in expected.items()):
                    raise ValueError("既有体验额度配置不匹配，未重置额度")
            else:
                self.config = {**expected,
                               "expires_at": policy.expires_at if policy else (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                               "price_snapshot": price_data}
                conn.execute("INSERT INTO experience_config VALUES (?,?)", (slot, json.dumps(self.config)))
        self.scope = AuditScope(self.path, "local-user-experience", "experience-network",
                                group_id=policy.session if policy else SESSION,
                                limits={"model": attempts, "source": 0},
                                pricing=pricing, cost_limit=cost_limit, expected_model=model)

    def reserve(self, request):
        self.check_deadline()
        matches_endpoint = str(request.url) == self.policy.endpoint if self.policy else (
            request.url.scheme == "https" and request.url.host in ("tokendance.space", "www.tokendance.space")
            and request.url.path == "/gateway/v1/chat/completions" and request.url.port in (None, 443)
            and not request.url.query and not request.url.userinfo
        )
        if request.method != "POST" or not matches_endpoint:
            raise CallLimitError("本次体验不允许该模型端点，未发出调用", "model_mismatch")
        payload = json.loads(request.content)
        output = payload.get("max_tokens")
        messages = payload.get("messages")
        if (payload.get("model") != self.scope.expected_model or payload.get("stream") or type(output) is not int
                or not 0 < output <= (self.policy.output_tokens if self.policy else 32768) or not isinstance(messages, list)
                or any(not isinstance(m, dict) or not isinstance(m.get("content"), str) for m in messages)):
            raise CallLimitError("本次体验仅允许有界文字请求，未发出调用", "model_mismatch")
        if self.policy and (len(request.content) > self.policy.request_bytes or not messages
                            or set(payload) - {"model", "messages", "max_tokens", "temperature", "stop", "response_format", "stream"}
                            or any(set(m) != {"role", "content"} or m["role"] not in ("system", "user", "assistant") for m in messages)):
            raise CallLimitError("请求超出本轮已批准的文字范围或输入上限，未发出调用", "experience_scope")
        # Bytes overestimate text tokens. Extra 4096 covers protocol/template overhead.
        rates = self.config["pricing"]
        reserved = ((len(request.content) + 4096) * rates["input"] + output * rates["output"]) / 1_000_000
        try:
            return self.scope.begin("network", "model", self.scope.expected_model, reserved_cost=reserved)
        except CallLimitError as exc:
            if exc.reason == "budget_exhausted":
                raise CallLimitError("本次体验的累计调用次数已达到保护上限，未发出调用", "experience_limit") from exc
            raise

    def check_deadline(self):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.config["expires_at"]):
            raise CallLimitError("本次体验授权已到期，未发出模型请求", "experience_expired")

    def install(self, *, admit=None, before_send=None):
        from core.llm.providers.openai_provider import OpenAIProvider
        original = OpenAIProvider._before_http
        budget = self

        def bounded(provider, request):
            pending = provider._pending.get()
            if pending is None:
                raise CallLimitError("无法确认模型请求审计，未发出调用", "cost_unknown")
            if admit:
                admit()
            ticket = budget.reserve(request)
            # Existing response/failure handlers finish both ledgers. Reservations
            # are never refunded, even when the project guard rejects afterwards.
            pending.append((id(request), budget.scope, ticket))
            original(provider, request)
            if before_send:
                before_send(request, ticket)
                budget.check_deadline()

        OpenAIProvider._before_http = bounded
        return original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("必须有本轮明确授权并指定 --allow-live")
    from tools.serve_local import configure
    from runtime.cli import _load_env
    _load_env()
    root = configure("accounts", args.port, args.data_root, True)
    os.environ["SRA_DRY_RUN"] = "false"
    from urllib.parse import urlsplit
    endpoint = urlsplit(os.environ.get("DEEPSEEK_BASE_URL", ""))
    if (not os.environ.get("DEEPSEEK_API_KEY") or endpoint.scheme != "https"
            or endpoint.hostname not in ("tokendance.space", "www.tokendance.space")
            or endpoint.path.rstrip("/") != "/gateway/v1"):
        parser.error("已有模型端点或凭据不符合本次授权，未修改模型路由")
    import requests
    response = requests.get(PRICE_URL, timeout=20)
    response.raise_for_status()
    budget = ExperienceBudget(root.parent / "runtime" / (SESSION + ".db"), response.json())
    from core.llm.task_router import TaskRouter
    from core.config import get_config
    router = TaskRouter.load(get_config().llm.tasks_config_path)
    if any(router.get(task).provider != "deepseek" or router.get(task).model != MODEL for task in router.list_tasks()):
        parser.error("任务模型与授权不一致，未修改路由或启用真实调用")
    budget.install()
    from web.api import app
    import uvicorn
    print(f"Account experience: http://127.0.0.1:{args.port}; shared CNY 2; expires {budget.config['expires_at']}", flush=True)
    print(f"Data root: {root}; existing request limits retained; no startup generation", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
