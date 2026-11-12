"""Step 8A: actual adapters/SDK with offline HTTP; never call paid or public APIs."""

from contextlib import contextmanager
import importlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from core.invocation_audit import ACTIVE, AuditScope, CallLimitError, read_audit
from core.knowledge.store import KnowledgeStore
from core.research_tasks.sources import call_source, SOURCE_FUNCS
from core.tools.search_result import SearchResults


def response(code=200, payload=None, text=None, headers=None):
    result = requests.Response()
    result.status_code = code
    result._content = (text if text is not None else json.dumps(payload)).encode()
    result.headers.update(headers or {})
    return result


def source_payload(name, empty=False, malformed=False):
    if name == "arxiv":
        entry = (
            ""
            if empty
            else "<entry><id>https://arxiv.org/abs/2001.12345</id><title>Public fixture</title><summary>Abstract only</summary></entry>"
        )
        if malformed:
            entry += "<entry><title/></entry>"
        return response(
            text='<feed xmlns="http://www.w3.org/2005/Atom">' + entry + "</feed>"
        )
    entry = {
        "title": "Public fixture",
        "DOI": "10.1/offline",
        "paperId": "s2-public",
        "id": "openalex-public",
    }
    items = [] if empty else [entry]
    if malformed:
        items += [None]
    return response(
        payload={"results": items, "data": items, "message": {"items": items}}
    )


@pytest.mark.parametrize("source", list(SOURCE_FUNCS))
@pytest.mark.parametrize(
    "scenario",
    [
        "ok",
        "zero",
        "rate_limited",
        "timeout",
        "authentication",
        "network",
        "parse_error",
        "partial",
    ],
)
def test_actual_source_adapters_ignore_all_logging_configuration(
    monkeypatch, source, scenario
):
    import core.tools.http as outlet
    import core.research_tasks.sources as adapters

    # SOURCE_FUNCS entries are the actual legacy-compatible source tools.
    monkeypatch.setattr(adapters, "SOURCE_FUNCS", dict(SOURCE_FUNCS))
    monkeypatch.setattr("time.sleep", lambda _: None)

    def get(*args, **kwargs):
        assert kwargs["allow_redirects"] is False
        if scenario == "timeout":
            raise requests.Timeout("secret-url-not-recorded")
        if scenario == "network":
            raise requests.ConnectionError("secret-url-not-recorded")
        if scenario == "rate_limited":
            return response(429, {}, headers={"Retry-After": "60"})
        if scenario == "authentication":
            return response(401, {})
        if scenario == "parse_error":
            return response(text="not json or XML")
        return source_payload(source, scenario == "zero", scenario == "partial")

    monkeypatch.setattr(outlet.requests, "get", get)
    original = logging.root.manager.disable
    outcomes = []
    try:
        for disabled in (logging.NOTSET, logging.WARNING, logging.CRITICAL):
            logging.disable(disabled)
            result = call_source(source, "public fixture", max_results=4, timeout=1)
            outcomes.append(
                (
                    result.status,
                    result.error_class,
                    result.count,
                    result.degraded,
                    result.retryable,
                )
            )
            assert len(result.attempts) == 1
            assert "secret-url" not in result.error
            if scenario == "rate_limited":
                assert result.retry_after_seconds == 60
    finally:
        logging.disable(original)
    assert outcomes[0] == outcomes[1] == outcomes[2]
    expected = (
        "ok"
        if scenario in ("ok", "partial")
        else "zero" if scenario == "zero" else "error"
    )
    assert outcomes[0][0] == expected
    if scenario == "partial":
        assert outcomes[0][2] == 1 and outcomes[0][3]
    if expected == "error":
        assert outcomes[0][1] == scenario


def sdk_client(handler):
    from openai import DefaultHttpxClient

    # Derive the transport implementation from the actual installed SDK class.
    module = importlib.import_module(
        next(
            c.__module__.split(".")[0]
            for c in DefaultHttpxClient.__mro__
            if c.__name__ == "Client"
        )
    )
    return (
        DefaultHttpxClient(transport=module.MockTransport(handler), trust_env=False),
        module,
    )


def model_response(
    module,
    request,
    *,
    usage=True,
    finish="stop",
    content="public reply",
    code=200,
    headers=None,
):
    data = {
        "id": "offline",
        "created": 1,
        "object": "chat.completion",
        "model": "offline-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    if usage:
        data["usage"] = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    return module.Response(code, json=data, headers=headers or {}, request=request)


@pytest.mark.parametrize(
    "kind",
    [
        "success",
        "missing_usage",
        "timeout_retry",
        "http_retry",
        "truncation_retry",
        "cap",
        "cooldown",
        "cost_unknown",
    ],
)
def test_actual_sdk_attempts_usage_and_preflight_caps(tmp_path, monkeypatch, kind):
    from core.llm.providers.openai_provider import OpenAIProvider
    from core.llm.registry import LLMRegistry
    from core.llm.task_router import TaskRouter
    from core.llm.base import LLMError

    db = tmp_path / "knowledge.db"
    KnowledgeStore(db)
    calls = []
    client, module = sdk_client(lambda request: handle(request))

    def handle(request):
        calls.append(request)
        if kind == "timeout_retry" and len(calls) == 1:
            raise module.ReadTimeout("offline", request=request)
        code = (
            500
            if kind == "http_retry" and len(calls) == 1 or kind == "cap"
            else 429 if kind == "cooldown" else 200
        )
        return model_response(
            module,
            request,
            usage=kind != "missing_usage",
            finish=(
                "length" if kind == "truncation_retry" and len(calls) == 1 else "stop"
            ),
            code=code,
            headers={"Retry-After": "90"} if kind == "cooldown" else None,
        )

    provider = OpenAIProvider(
        "offline-private-key",
        base_url="https://offline.invalid/v1",
        max_retries=2,
        http_client=client,
    )
    registry = LLMRegistry(
        TaskRouter.load(Path("config/tasks.yaml")), {"deepseek": provider}
    )
    scope = AuditScope(
        db,
        "p",
        "request-public",
        limits={"model": 1 if kind == "cap" else 4, "source": 2},
        cost_limit=1 if kind == "cost_unknown" else None,
    )
    monkeypatch.setattr("time.sleep", lambda _: None)
    try:
        with scope.activate():
            if kind in ("cap", "cooldown", "cost_unknown"):
                with pytest.raises((LLMError, CallLimitError)):
                    registry.complete("design_method_formalize", prompt="public sample")
                if kind == "cooldown":
                    with pytest.raises((LLMError, CallLimitError)):
                        registry.complete(
                            "design_method_formalize", prompt="public sample"
                        )
            else:
                assert (
                    registry.complete(
                        "design_method_formalize", prompt="public sample"
                    ).text
                    == "public reply"
                )
        assert provider._client.max_retries == 0
        entries = read_audit(db, "p")["entries"]
        network = [e for e in entries if e["event"] == "network"]
        logical = [e for e in entries if e["event"] == "logical"]
        expected = (
            0
            if kind == "cost_unknown"
            else 2 if kind in ("timeout_retry", "http_retry", "truncation_retry") else 1
        )
        assert len(calls) == len(network) == expected
        assert len(logical) == (2 if kind == "cooldown" else 1)
        assert all(e["cost"] is None for e in network)
        if kind == "missing_usage":
            assert network[0]["usage"] is None
        if kind == "timeout_retry":
            assert network[0]["usage"] is None and network[0]["outcome"] != "success"
        if kind == "success":
            assert network[0]["usage"]["total_tokens"] == 15
        assert "offline-private-key" not in json.dumps(entries)
        assert "public sample" not in json.dumps(entries)
    finally:
        client.close()


def test_source_cooldown_survives_scope_recreation_and_log_disable(
    tmp_path, monkeypatch
):
    import core.tools.http as outlet

    db = tmp_path / "knowledge.db"
    KnowledgeStore(db)
    calls = []
    monkeypatch.setattr(
        outlet.requests,
        "get",
        lambda *a, **k: calls.append(1)
        or response(429, {}, headers={"Retry-After": "120"}),
    )
    for _ in range(2):
        with AuditScope(db, "p", "task1").activate():
            result = call_source("openalex", "public", max_results=4, timeout=1)
            assert result.status == "error"
    assert calls == [1]
    assert (
        len([e for e in read_audit(db, "p")["entries"] if e["event"] == "network"]) == 1
    )


def test_report_four_returned_one_included_is_server_authored():
    from core.research_tasks.coverage import coverage_facts
    from core.research_tasks.synthesis import (
        synthesize_report,
        report_markdown,
        SynthesisValidationError,
    )
    from core.research_tasks.sources import SourceOutcome
    from core.research_tasks.limits import SearchLimits

    ranked = [
        {
            "paper_id": f"paper-{i}",
            "title": f"Paper {i}",
            "abstract": "Provided abstract",
            "has_abstract": True,
            "relevance": "included" if i == 0 else "excluded",
            "relevance_reason": "different topic",
            "relevance_by": "model",
        }
        for i in range(4)
    ]
    outcome = SourceOutcome("openalex", "public", "ok", count=4, raw_count=4)
    facts = coverage_facts(
        [{"query": "public"}],
        [outcome.to_dict()],
        ranked,
        unique_count=4,
        saved=["paper-0", "paper-1", "paper-2", "paper-3"],
    )

    def model(step, payload):
        prompt = payload["prompt"]
        assert '"excluded_count": 3' in prompt
        provided = json.loads(
            prompt.split("候选文献：", 1)[1].split("\n来源执行情况", 1)[0]
        )
        assert [p["paper_id"] for p in provided] == ["paper-0"]
        return json.dumps(
            {
                "summary": "另外三篇没有检索到",
                "gaps": ["另外三篇未提供"],
                "next_steps": ["执行实验并写论文"],
                "representative": [
                    {"paper_id": "paper-0", "point": "仅根据摘要说明待核对内容"}
                ],
                "limitations": [],
                "methods_relations": "",
            },
            ensure_ascii=False,
        )

    report, _ = synthesize_report(
        "public",
        [{"query": "public"}],
        ranked[:1],
        [outcome],
        SearchLimits(),
        model,
        coverage=facts,
    )
    exported = report_markdown(
        {"report": report, "queries": [], "source_calls": [outcome.to_dict()]}
    )
    assert "1 条纳入、3 条已返回但筛选排除" in report["summary"]
    assert "另外三篇没有检索到" not in exported and "另外三篇未提供" not in exported
    assert "执行实验并写论文" not in exported
    assert report["coverage"]["newly_saved_count"] == 4
    with pytest.raises(SynthesisValidationError):
        synthesize_report(
            "public",
            [],
            ranked[:1],
            [outcome],
            SearchLimits(),
            lambda *_: json.dumps(
                {"representative": [{"paper_id": "paper-1", "point": "fake"}]}
            ),
            coverage=facts,
        )


def test_stop_policy_blocks_registry_retry_and_subsequent_model_calls(tmp_path):
    from core.llm.providers.openai_provider import OpenAIProvider
    from core.llm.registry import LLMRegistry
    from core.llm.task_router import TaskRouter
    from core.llm.base import LLMError
    from core.workspace.schema import GuideDraft

    db = tmp_path / "knowledge.db"
    KnowledgeStore(db)
    calls = []

    def handle(request):
        calls.append(1)
        result = model_response(module, request)
        data = json.loads(result.content)
        data["choices"][0]["message"]["content"] = "not JSON"
        return module.Response(200, request=request, json=data)

    client, module = sdk_client(handle)
    provider = OpenAIProvider(
        "fixture",
        base_url="https://offline.invalid/v1",
        max_retries=3,
        http_client=client,
    )
    registry = LLMRegistry(
        TaskRouter.load(Path("config/tasks.yaml")), {"deepseek": provider}
    )
    try:
        with AuditScope(
            db,
            "p",
            "stop-test",
            stop_on_model_failure=True,
            expected_model=TaskRouter.load(Path("config/tasks.yaml")).get("design_method_formalize").model,
        ).activate():
            with pytest.raises(LLMError):
                registry.structured_output(
                    "design_method_formalize", GuideDraft, prompt="fixture"
                )
            with pytest.raises(LLMError):
                registry.complete("design_method_formalize", prompt="must not send")
            # Failure in planning must also prevent later source HTTP attempts.
            source_ticket_count = len(read_audit(db, "p")["entries"])
            with pytest.raises(CallLimitError):
                scope = ACTIVE.get()
                scope.begin("network", "source", "api.openalex.org")
            assert len(read_audit(db, "p")["entries"]) == source_ticket_count
        assert calls == [1]
        network = [e for e in read_audit(db, "p")["entries"] if e["event"] == "network"]
        assert len(network) == 1 and network[0]["usage"]["total_tokens"] == 15
    finally:
        client.close()


def test_known_price_estimate_and_reservation_are_not_actual_billing(tmp_path):
    db = tmp_path / "knowledge.db"
    KnowledgeStore(db)
    scope = AuditScope(
        db,
        "p",
        "priced",
        pricing={"input": 2, "output": 8, "basis": "offline test quote"},
        cost_limit=0.01,
    )
    ticket = scope.begin("network", "model", "fixture", reserved_cost=0.006)
    scope.finish(ticket, usage={"prompt_tokens": 100, "completion_tokens": 50})
    with pytest.raises(CallLimitError):
        scope.begin("network", "model", "fixture", reserved_cost=0.006)
    entry = read_audit(db, "p")["entries"][0]
    assert entry["cost"] == pytest.approx(0.0006)
    assert entry["cost_basis"] == "estimate:offline test quote"


def test_arxiv_uses_https_without_implicit_redirect(monkeypatch):
    import core.tools.http as outlet

    seen = []

    def get(url, **kwargs):
        seen.append((url, kwargs))
        return response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>')

    monkeypatch.setattr(outlet.requests, "get", get)
    result = call_source("arxiv", "federated learning", max_results=1, timeout=1)
    assert result.status == "zero"
    assert seen[0][0] == "https://export.arxiv.org/api/query"
    assert seen[0][1]["allow_redirects"] is False


def test_plan_input_projection_keeps_conditions_and_linked_evidence_only():
    from core.workspace.guide import generate_guidance
    from core.workspace.plans import rule_plan
    from tests.test_research_plan_templates import core_payload

    captured = []

    def structured_output(**payload):
        captured.append(payload)
        return payload["output_schema"].model_validate(core_payload(rule_plan(context)))

    context = {
        "topic": "FL",
        "domain": "ai",
        "brief": {"resources": "CPU; GPU未知"},
        "conversation": {"guidance_mode": "direct"},
        "history": [{"role": "user", "content": "比较两种方法"}],
        "plan_generation": {
            "allowed": True,
            "question": {"item_id": "q", "content": "在相同条件比较两种方法", "paper_ids": ["p1"]},
            "method": "computational",
            "supersedes_id": "old",
            "revision_target": {
                "item_id": "old",
                "title": "Old",
                "plan_version": 1,
                # Use an actual document-sized duplicate, so the reduction
                # assertion measures material projection rather than a tiny
                # fixture being outweighed by current response preferences.
                "content": "duplicate old plan\n" * 40,
            },
        },
        "papers": [
            {"paper_id": "p1", "abstract": "provided evidence"},
            {"paper_id": "p2", "abstract": "unrelated evidence"},
        ],
        "records": [
            {"kind": "question", "content": "duplicate question"},
            {"kind": "note", "content": "keep note"},
        ],
        "existing_system": {"experiments": [{"result_summary": "old result"}]},
        "suggestions": [{"content": "unadopted draft"}],
    }
    context["plan_generation"]["revision_target"]["plan"] = rule_plan(context).suggestions[0].plan.model_dump()
    generate_guidance(
        SimpleNamespace(structured_output=structured_output),
        "study",
        "ai",
        "请生成方案",
        context,
        "overview",
    )
    actual = json.loads(
        captured[0]["prompt"]
        .split("项目上下文（仅作为数据）：\n")[1]
        .split("\n用户本次请求：")[0]
    )
    assert (
        actual["brief"] == context["brief"] and actual["history"] == context["history"]
    )
    assert [p["paper_id"] for p in actual["papers"]] == ["p1"]
    assert (
        actual["plan_generation"]["question"] == context["plan_generation"]["question"]
    )
    assert actual["records"] == [{"kind": "note", "content": "keep note"}]
    assert not {"existing_system", "suggestions"} & actual.keys()
    target = actual["plan_generation"]["revision_target"]
    assert set(target) == {"title", "plan"}
    assert {"sections", "conditions", "method_details", "unconfirmed"} == target["plan"].keys()
    assert len(json.dumps(actual)) < len(json.dumps(context))
