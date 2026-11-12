"""Stage 5 preflight. Real app, SDK MockTransport, fresh SQLite, zero paid traffic."""
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from core.invocation_audit import CallLimitError
from core.llm.providers.openai_provider import OpenAIProvider
from core.workspace.reading_session import PassageRequest
from tests.question_paper_reading_fixture import make_pdf
from tests.test_real_workflow_reliability import model_response
from tools.serve_budgeted_local import MODEL, ExperienceBudget, ExperiencePolicy
from tools.serve_guided_experience import (
    configure_isolation, disable_automatic_attempts, install_guard, load_tokendance_endpoint, tokendance_price_snapshot,
    recorded_round_root, visible_content,
)


BASE_URL = "https://tokendance.space/gateway/v1"
ENDPOINT = BASE_URL + "/chat/completions"



def policy(**changes):
    base = ExperiencePolicy(
        "approval-stage5-test", MODEL, ENDPOINT, {"input": 2, "output": 8},
        5, 7, (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat(), 160000, 12000,
    )
    return replace(base, **changes)


def rates(**changes):
    def items(input_rate, output_rate):
        return [
            {"id": "openai:chat-completions:" + kind, "plans": [{"unit": "millionTokens", "rate": rate}]}
            for kind, rate in (("input_tokens", input_rate), ("completion_tokens", output_rate))
        ]

    return {"slug": MODEL, "pricing": {
        "items": items("0.8", "3.2"),
        "reference": items("1", "4"),
        "time_pricing": {"options": [{"items": items("1.6", "6.4"), "reference": items("2", "8")}]},
    }, **changes}


def counts(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT COUNT(*),COALESCE(SUM(reserved_cost),0) FROM sra_call_audit WHERE event='network'").fetchone()


def test_tokendance_model_currency_and_peak_fail_closed(tmp_path):
    path = tmp_path / "budget.db"
    assert ExperienceBudget(path, tokendance_price_snapshot(rates()), policy=policy())
    with pytest.raises(ValueError, match="模型"):
        ExperienceBudget(path, tokendance_price_snapshot(rates(slug="other")), policy=policy())
    with pytest.raises(ValueError, match="人民币"):
        tokendance_price_snapshot(rates(currency="USD"))
    peak = rates()
    peak["pricing"]["time_pricing"]["options"][0]["reference"][1]["plans"][0]["rate"] = "9"
    with pytest.raises(ValueError, match="超过"):
        ExperienceBudget(path, tokendance_price_snapshot(peak), policy=policy())


@pytest.fixture
def local_tokendance_env(tmp_path, monkeypatch):
    from runtime import cli

    monkeypatch.setattr(cli, "_PROJECT_ROOT", tmp_path)
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    monkeypatch.delenv("SRA_TASKS_CONFIG_PATH", raising=False)
    local = tmp_path / ".env"
    local.write_text(f'DEEPSEEK_API_KEY="offline-fixture-key"\nDEEPSEEK_BASE_URL={BASE_URL}\n', encoding="utf-8")
    return local


def test_daily_loader_and_config_precedence(local_tokendance_env, monkeypatch):
    assert load_tokendance_endpoint() == ENDPOINT
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    with pytest.raises(ValueError, match="DEEPSEEK_BASE_URL"):
        load_tokendance_endpoint()
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    local_tokendance_env.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        load_tokendance_endpoint()


def test_expired_launch_never_loads_gateway_or_pricing(monkeypatch, capsys):
    import sys
    from tools import serve_guided_experience as launcher

    def forbid():
        pytest.fail("expired launch attempted to load a model gateway")

    monkeypatch.setattr(launcher, "load_tokendance_endpoint", forbid)
    monkeypatch.setattr(sys, "argv", [
        "stage5", "--allow-live", "--experience-id", "old", "--port", "8125", "--model", MODEL,
        "--input-cny", "2", "--output-cny", "8", "--cost-cny", "5", "--attempts", "16",
        "--expires-at", "2000-01-01T00:00:00Z", "--request-bytes", "160000", "--output-tokens", "12000",
    ])
    with pytest.raises(SystemExit) as stopped:
        launcher.main()
    assert stopped.value.code == 2
    assert "绝对截止时间" in capsys.readouterr().err


def test_amount_deadline_and_same_ledger_reopen(tmp_path):
    path = tmp_path / "budget.db"
    approved = policy(cost_limit=.02)
    budget = ExperienceBudget(path, tokendance_price_snapshot(rates()), policy=approved)
    body = {"model": MODEL, "messages": [{"role": "user", "content": "public"}], "max_tokens": 512}
    request = httpx.Request("POST", ENDPOINT, json=body)
    budget.reserve(request)
    reopen = ExperienceBudget(path, tokendance_price_snapshot(rates()), policy=approved)
    with pytest.raises(CallLimitError) as error:
        reopen.reserve(request)
    assert error.value.reason == "cost_exhausted" and counts(path)[0] == 1
    for change in ({"session": "new-id"}, {"attempts": 8}, {"cost_limit": .03}, {"expires_at": policy().expires_at}):
        with pytest.raises(ValueError, match="未重置"):
            ExperienceBudget(path, tokendance_price_snapshot(rates()), policy=replace(approved, **change))
    expired = ExperienceBudget(tmp_path / "expired.db", tokendance_price_snapshot(rates()), policy=policy(
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    with pytest.raises(CallLimitError) as error:
        expired.reserve(request)
    assert error.value.reason == "experience_expired"


def test_explicit_no_amount_cap_keeps_durable_attempt_limit_and_reservations(tmp_path):
    config = policy(cost_limit=None, attempts=2)
    path = tmp_path / "uncapped.db"
    body = {"model": MODEL, "messages": [{"role": "user", "content": "public"}], "max_tokens": 512}
    request = httpx.Request("POST", ENDPOINT, json=body)
    ledger = ExperienceBudget(path, tokendance_price_snapshot(rates()), policy=config)
    ledger.reserve(request)
    reopen = ExperienceBudget(path, tokendance_price_snapshot(rates()), policy=config)
    reopen.reserve(request)
    with pytest.raises(CallLimitError) as stopped:
        reopen.reserve(request)
    assert stopped.value.reason == "experience_limit"
    count, reserved = counts(path)
    assert count == 2 and reserved > 0


def test_recorded_round_preserves_old_ledger_and_refuses_restart_without_new_ledger(tmp_path):
    approved = policy(session="r2-test", request_bytes=104000, attempts=16)
    previous = tmp_path / "budget.sqlite"
    previous.write_bytes(b"old ledger fixture")
    with pytest.raises(ValueError, match="授权记录"):
        recorded_round_root(tmp_path, approved)
    root = tmp_path / "rounds" / approved.session
    root.mkdir(parents=True)
    expiry = datetime.fromisoformat(approved.expires_at)
    approval = {
        "authorization_id": approved.session, "provider": "TokenDance", "model": MODEL, "endpoint": ENDPOINT,
        "listen": "127.0.0.1:8125", "cost_cny": 5, "network_attempts": 16, "request_bytes": 104000,
        "output_tokens": 12000, "input_cny": 2, "output_cny": 8,
        "received_at_utc": (expiry - timedelta(minutes=90)).isoformat(), "expires_at_utc": approved.expires_at,
    }
    (root / "authorization.json").write_text(json.dumps(approval), encoding="utf-8")
    assert recorded_round_root(tmp_path, approved) == root
    with pytest.raises(ValueError, match="原始截止时间"):
        recorded_round_root(tmp_path, replace(approved, expires_at=(expiry + timedelta(seconds=1)).isoformat()))
    (root / "data").mkdir()
    with pytest.raises(ValueError, match="原账本"):
        recorded_round_root(tmp_path, approved)
    budget = ExperienceBudget(root / "budget.sqlite", tokendance_price_snapshot(rates()), policy=approved)
    assert recorded_round_root(tmp_path, approved) == root
    assert budget.config["expires_at"] == approved.expires_at
    assert previous.read_bytes() == b"old ledger fixture"


def test_receipt_keeps_visible_content_and_excludes_hidden_thoughts_and_credential():
    assert visible_content('前<THINK>内部<think>更多</think></THINK>后', 'key') == '前后'
    assert visible_content('答案<think>被截断的隐藏内容', 'key') == '答案'
    assert visible_content('未标记内容</think>答案', 'key') is None
    assert visible_content('可见 key', 'key') == '可见 [已移除凭据]'


def test_deadline_rechecked_between_input_receipt_and_transport(tmp_path, monkeypatch):
    budget = ExperienceBudget(tmp_path / "budget.db", tokendance_price_snapshot(rates()), policy=policy())
    monkeypatch.setattr(OpenAIProvider, "_before_http", lambda provider, request: None)

    def finish_archiving(request, ticket):
        budget.config["expires_at"] = "2000-01-01T00:00:00+00:00"

    def forbid(request):
        pytest.fail("expired request reached transport after archiving")

    budget.install(before_send=finish_archiving)
    provider = SimpleNamespace(_pending=ContextVar("pending", default=[]))

    def hook(request):
        OpenAIProvider._before_http(provider, request)

    body = {"model": MODEL, "messages": [{"role": "user", "content": "public"}], "max_tokens": 512}
    with httpx.Client(transport=httpx.MockTransport(forbid), event_hooks={"request": [hook]}) as client:
        with pytest.raises(CallLimitError) as error:
            client.post(ENDPOINT, json=body)
    assert error.value.reason == "experience_expired" and counts(budget.path)[0] == 1


@pytest.mark.parametrize("fault", ["upstream", "model", "tools", "image", "bytes", "output", "stream"])
def test_stage5_rejects_unapproved_network_shapes(tmp_path, fault):
    budget = ExperienceBudget(tmp_path / "budget.db", tokendance_price_snapshot(rates()), policy=policy(request_bytes=500))
    body = {"model": MODEL, "messages": [{"role": "user", "content": "public"}], "max_tokens": 4096}
    changes = {
        "model": {"model": "deepseek-v4-pro"}, "tools": {"tools": []},
        "image": {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
        "bytes": {"messages": [{"role": "user", "content": "x" * 800}]},
        "output": {"max_tokens": 12001}, "stream": {"stream": True},
    }
    body.update(changes.get(fault, {}))
    target = "https://api.deepseek.com/v1/chat/completions" if fault == "upstream" else ENDPOINT
    with pytest.raises(CallLimitError):
        budget.reserve(httpx.Request("POST", target, json=body))
    with sqlite3.connect(budget.path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sra_call_audit'").fetchone()


def test_production_routes_share_budget_and_limit_automatic_attempts(tmp_path, monkeypatch, local_tokendance_env):
    from core import config
    from core.llm import LLMRegistry
    import core.tools
    from core.research_tasks.service import ResearchTaskService
    import requests

    # Undo process-local launcher protections when pytest returns.
    monkeypatch.setattr(config, "_default_config", None)
    monkeypatch.setattr(core.tools, "mineru_is_available", core.tools.mineru_is_available)
    monkeypatch.setattr(ResearchTaskService, "start", ResearchTaskService.start)
    monkeypatch.setattr(ResearchTaskService, "retry", ResearchTaskService.retry)
    monkeypatch.setattr(requests.sessions.Session, "request", requests.sessions.Session.request)
    from core.research_tasks import sources
    monkeypatch.setattr(sources, "SOURCE_FUNCS", sources.SOURCE_FUNCS)
    monkeypatch.setattr(OpenAIProvider, "_before_http", OpenAIProvider._before_http)
    monkeypatch.setattr(OpenAIProvider, "_after_http", OpenAIProvider._after_http)
    monkeypatch.setattr(LLMRegistry, "structured_output", LLMRegistry.structured_output)
    from unittest.mock import patch

    wire = []
    clients = []
    should_fail = False
    original_init = OpenAIProvider.__init__

    def handler(request):
        nonlocal should_fail
        assert str(request.url) == ENDPOINT
        assert request.headers.get("Authorization") == "Bearer offline-fixture-key"
        payload = json.loads(request.content)
        assert payload["model"] == MODEL
        archived = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "receipts/inputs").glob("*.json")]
        assert len(archived) == len(wire) + 1  # Includes the in-flight input before network I/O.
        assert any(r["request"] == payload and r["request_bytes"] == len(request.content) for r in archived)
        assert all("offline-fixture-key" not in json.dumps(r) for r in archived)
        wire.append(payload)
        if should_fail:
            should_fail = False
            raise httpx.ConnectError("offline injected failure", request=request)
        system = payload["messages"][0]["content"]
        result = ({"translation": "离线样本。", "terms": []} if "网站论文选段" in system
                  else {"reply": "只用于费用拦截验证的离线回复。"})
        return model_response(httpx, request, content=json.dumps(result, ensure_ascii=False))

    def init_with_offline_transport(self, *args, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return original_init(self, *args, **{**kwargs, "http_client": client})

    monkeypatch.setattr(OpenAIProvider, "__init__", init_with_offline_transport)
    try:
        with patch.dict(os.environ):
            assert load_tokendance_endpoint() == ENDPOINT
            daily_route = config.GlobalConfig.load().llm.tasks_config_path
            configure_isolation(tmp_path, 8125, MODEL)
            assert (tmp_path / "tasks.yaml").read_bytes() == daily_route.read_bytes()
            disable_automatic_attempts()
            from web import api
            importlib.reload(api)
            assert api._CONFIG.paths.root == tmp_path / "data"
            assert not api._CONFIG.dry_run
            approved = policy()
            budget = ExperienceBudget(tmp_path / "budget.db", tokendance_price_snapshot(rates()), policy=approved)
            install_guard(api.app, budget, receipts=tmp_path / "receipts", capture_inputs=True)
            with TestClient(api.app) as a, TestClient(api.app) as b:
                for n, client in enumerate((a, b)):
                    email = f"acceptance-{n}@example.test"
                    password = "offline-test-password-Stage5"
                    api._ACCOUNT_STORE.create_user(email, password)
                    login = client.post("/api/auth/login", headers={"X-SRA-Login": "1"}, json={"email": email, "password": password})
                    assert login.status_code == 200
                    client.headers["X-SRA-CSRF"] = login.json()["csrf_token"]

                def project(client):
                    res = client.post("/api/projects", json={"topic": "Public retrieval benchmark", "domain": "ai"})
                    assert res.status_code == 200, res.text
                    pid = res.json()["project_id"]
                    proj = f"/api/projects/{pid}"
                    chat = client.post(proj + "/conversations", json={"title": "Read public material"}).json()["conversation"]["conversation_id"]
                    return proj, proj + "/conversations/" + chat

                p, chat = project(a)
                other, other_chat = project(b)
                ordinary = {"content": "请帮我理解段落检索是什么。", "request_id": "ordinary"}
                assert a.post(chat + "/messages", json=ordinary).status_code == 200
                assert a.post(chat + "/messages", json=ordinary).status_code == 200 and len(wire) == 1
                upload = a.post(p + "/upload-paper", files={"file": ("example.pdf", make_pdf(), "application/pdf")})
                assert upload.status_code == 200, upload.text
                paper = upload.json()["paper_id"]
                guided = {"content": "这篇论文的方法怎样工作？", "request_id": "companion",
                          "reading_guide": {"paper_id": paper, "goal": "method"}}
                assert a.post(chat + "/messages", json=guided).status_code == 200
                state = a.get(p + "/workspace?include_chat=false").json()
                preview = next(x for x in state["papers"] if x["paper_id"] == paper)
                text = preview["abstract"][:10]
                selection = PassageRequest(operation="translate", source="preview", paper_id=paper,
                                           version=preview["preview_version"], text=text, start=0, end=len(text))
                translate = {"content": "论文选段", "request_id": selection.identity(), "passage": selection.model_dump()}
                assert a.post(chat + "/messages", json=translate).status_code == 200
                assert len(wire) == 3
                added = a.post(p + "/workspace/items", json={"expected_revision": state["workspace"]["revision"],
                    "item": {"kind": "question", "title": "确认的问题", "content": "如何比较两个索引的检索效果？", "paper_ids": [paper]}})
                assert added.status_code == 200, added.text
                qid = added.json()["workspace"]["items"][-1]["item_id"]
                plan = {"content": "请生成计算研究方案", "request_id": "plan",
                        "plan_request": {"method": "computational", "question_id": qid}, "wait_for_completion": False}
                started = a.post(chat + "/messages", json=plan)
                assert started.status_code in (202, 422, 502), started.text
                # The real asynchronous worker must receive the originating HTTP permit.
                from time import sleep
                for _ in range(40):
                    row = a.get(chat + "/requests/plan").json()
                    if row["status"] != "processing":
                        break
                    sleep(.1)
                assert row["status"] == "failed" and len(wire) == 4
                should_fail = True
                failure = a.post(chat + "/messages", json={**ordinary, "request_id": "retry-me"})
                assert failure.status_code == 502 and len(wire) == 5
                assert a.post(chat + "/requests/retry-me/retry", json={}).status_code == 200 and len(wire) == 6
                assert b.get(p + "/workspace").status_code == 404
                assert b.post(other_chat + "/messages", json={**ordinary, "request_id": "second-owner"}).status_code == 200
                assert a.post(chat + "/research-tasks", json={}).status_code == 403
                assert a.post(chat + "/requests/retry-me/restart", json={}).status_code == 403
                exhausted = a.post(chat + "/messages", json={**ordinary, "request_id": "new-id-after-cap"})
                assert exhausted.json()["failure"]["reason"] == "experience_limit"
                assert len(wire) == counts(budget.path)[0] == 7
                assert len(list((tmp_path / "receipts").glob("*.json"))) == 6  # connection failure has only audit record
                assert len(list((tmp_path / "receipts/inputs").glob("*.json"))) == 7  # even failure has its original input
                assert counts(budget.path)[1] < 5
    finally:
        for client in clients:
            client.close()
