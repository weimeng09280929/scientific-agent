"""Local CNY 2 experience guard: real SDK, fake transport; no paid calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading

import httpx
import pytest

from core.invocation_audit import AuditScope, CallLimitError
from core.knowledge.store import KnowledgeStore
from core.llm.base import LLMRequest, LLMError
from core.llm.providers.openai_provider import OpenAIProvider
from tests.test_real_workflow_reliability import model_response
from tools.serve_budgeted_local import ExperienceBudget, MODEL, SESSION, validate_prices


def prices():
    return {"slug": MODEL, "pricing": {"items": [
        {"id": "openai:chat-completions:input_tokens", "plans": [{"unit": "millionTokens", "rate": "2"}]},
        {"id": "openai:chat-completions:completion_tokens", "plans": [{"unit": "millionTokens", "rate": "8"}]},
    ]}}


def request(**changes):
    payload = {"model": MODEL, "max_tokens": 32768, "messages": [{"role": "user", "content": "hello"}]}
    payload.update(changes)
    return httpx.Request("POST", "https://tokendance.space/gateway/v1/chat/completions", json=payload)


def totals(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT COUNT(*),SUM(reserved_cost) FROM sra_call_audit WHERE event='network'").fetchone()


def test_concurrent_shared_reservation_and_restart(tmp_path):
    budget = ExperienceBudget(tmp_path / "ledger.db", prices())
    barrier = threading.Barrier(12)
    def reserve(_):
        barrier.wait()
        try:
            budget.reserve(request())
            return True
        except CallLimitError as exc:
            assert exc.reason == "cost_exhausted"
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        successes = sum(pool.map(reserve, range(12)))
    count, cost = totals(budget.path)
    assert count == successes == 7 and 0 < cost <= 2
    reopened = ExperienceBudget(budget.path, prices())
    assert reopened.config == budget.config
    with pytest.raises(CallLimitError, match="超过授权"):
        reopened.reserve(request())
    assert totals(budget.path) == (count, cost)


@pytest.mark.parametrize("case", ["model", "stream", "tokens", "image", "endpoint"])
def test_unpriced_calls_never_admitted(tmp_path, case):
    budget = ExperienceBudget(tmp_path / "ledger.db", prices())
    changes = {"model": {"model": "other"}, "stream": {"stream": True},
               "tokens": {"max_tokens": None}, "image": {"messages": [{"content": []}]}}
    req = request(**changes.get(case, {}))
    if case == "endpoint":
        req = httpx.Request("POST", "https://other.invalid/v1/chat/completions", content=req.content)
    with pytest.raises(CallLimitError):
        budget.reserve(req)
    with sqlite3.connect(budget.path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_call_audit'").fetchone()


def test_sdk_preserves_project_audits_failures_and_global_cap(tmp_path, monkeypatch):
    budget = ExperienceBudget(tmp_path / "ledger.db", prices())
    monkeypatch.setattr(OpenAIProvider, "_before_http", OpenAIProvider._before_http)
    original = budget.install()
    calls = []
    def handler(req):
        calls.append(1)
        if len(calls) == 2:
            raise httpx.ConnectError("offline", request=req)
        return model_response(httpx, req)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider("offline-key", base_url="https://tokendance.space/gateway/v1", max_retries=0, http_client=client)
    try:
        for project in ("a", "b"):
            db = tmp_path / (project + ".db")
            KnowledgeStore(db)
            with AuditScope(db, project, "original-request").activate():
                if project == "b":
                    with pytest.raises(LLMError):
                        provider.complete(LLMRequest(prompt="offline", max_tokens=32768), MODEL)
                else:
                    provider.complete(LLMRequest(prompt="offline", max_tokens=32768), MODEL)
            assert totals(db)[0] == 1
        for _ in range(5):
            provider.complete(LLMRequest(prompt="offline", max_tokens=32768), MODEL)
        with pytest.raises(LLMError) as error:
            provider.complete(LLMRequest(prompt="offline", max_tokens=32768), MODEL)
        assert error.value.__cause__.reason == "cost_exhausted"
        assert len(calls) == 7 and totals(budget.path)[0] == 7
        with sqlite3.connect(budget.path) as conn:
            outcomes = [r[0] for r in conn.execute("SELECT outcome FROM sra_call_audit")]
        assert outcomes.count("success") == 6 and outcomes.count("APIConnectionError") == 1
    finally:
        OpenAIProvider._before_http = original
        client.close()


def test_expiry_and_tariff_fail_closed(tmp_path):
    data = prices()
    data["pricing"]["time_pricing"] = {"options": [{"items": [{
        "id": "openai:chat-completions:completion_tokens", "plans": [{"unit": "millionTokens", "rate": "9"}]}]}]}
    with pytest.raises(ValueError):
        validate_prices(data)
    budget = ExperienceBudget(tmp_path / "ledger.db", prices())
    budget.config["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(CallLimitError) as exc:
        budget.reserve(request())
    assert exc.value.reason == "experience_expired"


def test_shared_attempt_limit_is_not_an_original_request_restart(tmp_path):
    budget = ExperienceBudget(tmp_path / "ledger.db", prices())
    for _ in range(100):
        budget.reserve(request(max_tokens=1))
    with pytest.raises(CallLimitError) as exc:
        budget.reserve(request(max_tokens=1))
    assert exc.value.reason == "experience_limit"
    assert totals(budget.path)[0] == 100 and totals(budget.path)[1] < 2
