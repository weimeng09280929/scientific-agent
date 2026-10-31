"""Opt-in local account experience; one durable CNY 2 reservation across projects.

This launcher supplements, rather than replaces, project request/audit limits.
No model request is sent by startup. Rates are checked against public metadata.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

from core.invocation_audit import AuditScope, CallLimitError

MODEL = "deepseek-v4.1-flash-1017"
PRICE_URL = "https://www.tokendance.space/portal/api/models/" + MODEL
SESSION = "user-experience-20261029-2cny"
PRICING = {"input": 2.0, "output": 8.0, "basis": PRICE_URL + " (undiscounted peak, CNY/M tokens)"}


def validate_prices(data):
    if data.get("slug") != MODEL:
        raise ValueError("模型价格资料不匹配，未启用真实调用")
    pricing = data.get("pricing", {})
    groups = [pricing.get("items", []), pricing.get("reference", [])]
    timed = pricing.get("time_pricing", {})
    groups += [timed.get("base_items", []), timed.get("base_reference", [])]
    for option in timed.get("options", []):
        groups += [option.get("items", []), option.get("reference", [])]
    for suffix, bound in (("input_tokens", 2), ("completion_tokens", 8)):
        rates = [float(plan["rate"]) for group in groups for item in group
                 if item.get("id") == "openai:chat-completions:" + suffix
                 for plan in item.get("plans", []) if plan.get("unit") == "millionTokens"]
        if not rates or any(not 0 < rate <= bound for rate in rates):
            raise ValueError("公开单价缺失或超过费用预留单价，未启用真实调用")


class ExperienceBudget:
    def __init__(self, path: Path, price_data: dict):
        validate_prices(price_data)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS experience_config (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            row = conn.execute("SELECT data FROM experience_config WHERE id=?", (SESSION,)).fetchone()
            if row:
                self.config = json.loads(row[0])
                if self.config.get("cost_limit") != 2 or self.config.get("pricing") != PRICING:
                    raise ValueError("既有体验额度配置不匹配，未重置额度")
            else:
                self.config = {"cost_limit": 2, "pricing": PRICING,
                               "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                               "price_snapshot": price_data}
                conn.execute("INSERT INTO experience_config VALUES (?,?)", (SESSION, json.dumps(self.config)))
        self.scope = AuditScope(self.path, "local-user-experience", "experience-network",
                                group_id=SESSION, limits={"model": 100, "source": 0},
                                pricing=PRICING, cost_limit=2, expected_model=MODEL)

    def reserve(self, request):
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.config["expires_at"]):
            raise CallLimitError("本次体验授权已到期，未发出模型请求", "experience_expired")
        if request.url.scheme != "https" or request.url.host not in ("tokendance.space", "www.tokendance.space") or request.url.path != "/gateway/v1/chat/completions":
            raise CallLimitError("本次体验不允许该模型端点，未发出调用", "model_mismatch")
        payload = json.loads(request.content)
        output = payload.get("max_tokens")
        messages = payload.get("messages")
        if (payload.get("model") != MODEL or payload.get("stream") or type(output) is not int
                or not 0 < output <= 32768 or not isinstance(messages, list)
                or any(not isinstance(m, dict) or not isinstance(m.get("content"), str) for m in messages)):
            raise CallLimitError("本次体验仅允许有界文字请求，未发出调用", "model_mismatch")
        # Bytes overestimate text tokens. Extra 4096 covers protocol/template overhead.
        reserved = ((len(request.content) + 4096) * PRICING["input"] + output * PRICING["output"]) / 1_000_000
        try:
            return self.scope.begin("network", "model", MODEL, reserved_cost=reserved)
        except CallLimitError as exc:
            if exc.reason == "budget_exhausted":
                raise CallLimitError("本次体验的累计调用次数已达到保护上限，未发出调用", "experience_limit") from exc
            raise

    def install(self):
        from core.llm.providers.openai_provider import OpenAIProvider
        original = OpenAIProvider._before_http
        budget = self

        def bounded(provider, request):
            pending = provider._pending.get()
            if pending is None:
                raise CallLimitError("无法确认模型请求审计，未发出调用", "cost_unknown")
            ticket = budget.reserve(request)
            # Existing response/failure handlers finish both ledgers. Reservations
            # are never refunded, even when the project guard rejects afterwards.
            pending.append((id(request), budget.scope, ticket))
            original(provider, request)

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
