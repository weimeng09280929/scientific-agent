"""P1 repair: temporary projects and an SDK network substitute only."""

from copy import deepcopy
import os
from pathlib import Path

import pytest

from core.invocation_audit import AuditScope
from core.llm.base import LLMRequest
from core.llm.providers.openai_provider import OpenAIProvider
from core.workspace.plans import rule_plan, validate_plan
from core.workspace.schema import Brief, GuideDraft
from core.workspace.service import WorkspaceError
from tests.test_project_conversations import chat, create, messages, send
from tests.test_real_workflow_reliability import model_response, sdk_client
from tests.test_research_plan_templates import model as offline_plan, attach_exports


def offline_generator(chat, monkeypatch, handler, *, max_retries=3):
    client, module = sdk_client(handler)
    provider = OpenAIProvider("offline-key", base_url="https://offline.invalid/v1",
                              max_retries=max_retries, http_client=client)
    monkeypatch.setattr("time.sleep", lambda _: None)

    def generate(*args):
        provider.complete(LLMRequest(prompt="offline fixture"), model="fixture")
        return GuideDraft(reply="Recovered offline answer")

    chat.control["generate"] = generate
    return client, module


def test_retry_uses_remaining_allowance_and_preserves_one_message(chat, monkeypatch):
    cid = create(chat)
    attempts = []
    failing = [True]

    def handler(request):
        attempts.append(1)
        if failing[0]:
            raise module.ConnectError("offline", request=request)
        return model_response(module, request)

    client, module = offline_generator(chat, monkeypatch, handler, max_retries=0)
    try:
        first = send(chat, cid, "解释范围", "recover-within").json()
        assert first["status"] == "failed"
        assert first["failure"]["reason"] == "connection_error"
        assert first["failure"]["can_retry"] is True
        failing[0] = False
        result = chat.client.post(
            f"/api/projects/p1/conversations/{cid}/requests/recover-within/retry",
            json={"wait_for_completion": True},
        ).json()
        assert result["status"] == "completed"
        assert len(attempts) == 2
        assert [m["role"] for m in messages(chat, cid)] == ["user", "assistant"]
        assert result["attempt"] == 2
    finally:
        client.close()


def test_budget_exhaustion_restart_is_idempotent_and_keeps_audit(chat, monkeypatch):
    cid = create(chat)
    attempts = []
    failing = [True]

    def handler(request):
        attempts.append(1)
        if failing[0]:
            raise module.ConnectError("offline", request=request)
        return model_response(module, request)

    client, module = offline_generator(chat, monkeypatch, handler)
    try:
        base = f"/api/projects/p1/conversations/{cid}/requests/budget-round"
        assert send(chat, cid, "解释范围", "budget-round").json()["status"] == "failed"
        assert len(attempts) == 4
        second = chat.client.post(base + "/retry", json={}).json()
        assert len(attempts) == 6 and second["failure"]["reason"] == "budget_exhausted"
        assert second["failure"]["can_retry"] is False
        assert second["failure"]["can_restart"] is True
        again = chat.client.post(base + "/retry", json={}).json()
        assert len(attempts) == 6 and again["attempt"] == 2
        assert chat.client.post(base + "/restart", json={"acknowledge_model_cost": False}).status_code == 422
        failing[0] = False
        restarted = chat.client.post(base + "/restart", json={"acknowledge_model_cost": True}).json()
        assert restarted["status"] == "completed"
        assert restarted["recovery_of"] == "budget-round"
        assert restarted["request_id"].startswith("recovery-")
        assert len(attempts) == 7
        replay = chat.client.post(base + "/restart", json={"acknowledge_model_cost": True}).json()
        assert replay["request_id"] == restarted["request_id"] and len(attempts) == 7
        from core.workspace.conversations import ConversationService
        reopened = ConversationService("p1", chat.config.paths.project_db("p1"))
        old = reopened.request_view(cid, "budget-round")
        child = reopened.request_view(cid, restarted["request_id"])
        assert old["recovery_request_id"] == child["request_id"]
        assert old["failure"]["model_attempts_used"] == 6
        assert sum(e["event"] == "network" for e in child["call_audit"]["entries"]) == 1
        assert [m["role"] for m in messages(chat, cid)] == ["user", "assistant"]
    finally:
        client.close()


def test_enclosing_budget_cannot_be_reset_by_restart(chat, monkeypatch):
    cid = create(chat)
    attempts = []

    def handler(request):
        attempts.append(1)
        raise module.ConnectError("offline", request=request)

    client, module = offline_generator(chat, monkeypatch, handler)
    try:
        scope = AuditScope(chat.config.paths.project_db("p1"), "p1", "upper",
                           group_id="user-budget-fixture", limits={"model": 2, "source": 0})
        with scope.activate():
            result = send(chat, cid, "解释范围", "upper-round").json()
        assert len(attempts) == 2
        assert result["failure"]["reason"] == "budget_exhausted"
        assert result["failure"]["can_restart"] is False
        base = f"/api/projects/p1/conversations/{cid}/requests/upper-round"
        response = chat.client.post(base + "/restart", json={"acknowledge_model_cost": True})
        assert response.status_code == 409 and len(attempts) == 2
    finally:
        client.close()


def test_zero_enclosing_budget_does_not_create_a_fresh_allowance(chat, monkeypatch):
    cid = create(chat)
    calls = []
    def handler(request):
        calls.append(1)
        return model_response(module, request)
    client, module = offline_generator(chat, monkeypatch, handler)
    try:
        scope = AuditScope(chat.config.paths.project_db("p1"), "p1", "upper-zero",
                           group_id="user-budget-zero", limits={"model": 0, "source": 0})
        with scope.activate():
            result = send(chat, cid, "解释范围", "zero-budget").json()
        assert result["status"] == "failed" and calls == []
        assert result["failure"]["reason"] == "budget_exhausted"
        assert result["failure"]["can_restart"] is False
        response = chat.client.post(
            f"/api/projects/p1/conversations/{cid}/requests/zero-budget/restart",
            json={"acknowledge_model_cost": True},
        )
        assert response.status_code == 409 and calls == []
    finally:
        client.close()


def test_changed_material_requires_review_before_new_round(chat, monkeypatch):
    cid = create(chat)
    def handler(request):
        raise module.ConnectError("offline", request=request)
    client, module = offline_generator(chat, monkeypatch, handler)
    try:
        base = f"/api/projects/p1/conversations/{cid}/requests/stale-round"
        assert send(chat, cid, "解释范围", "stale-round").json()["status"] == "failed"
        assert chat.client.post(base + "/retry", json={}).json()["failure"]["can_restart"] is True
        service = chat.services["p1"]
        service.save_brief(Brief(resources="新条件"), service.load().revision)
        latest = chat.client.get(base).json()
        assert latest["basis_changed"] is True
        assert latest["failure"]["can_restart"] is False
        assert chat.client.post(base + "/restart", json={"acknowledge_model_cost": True}).status_code == 409
    finally:
        client.close()


@pytest.mark.parametrize("code,reason", [(401, "authentication"), (429, "rate_limited")])
def test_authentication_and_cooldown_have_distinct_recovery_state(chat, monkeypatch, code, reason):
    cid = create(chat)
    calls = []

    def handler(request):
        calls.append(1)
        return model_response(module, request, code=code,
                              headers={"Retry-After": "60"} if code == 429 else None)

    client, module = offline_generator(chat, monkeypatch, handler)
    try:
        data = send(chat, cid, "解释范围", f"error-{code}").json()
        assert data["failure"]["reason"] == reason
        assert data["failure"]["can_retry"] is False
        if code == 429:
            assert data["failure"]["retry_after_seconds"] > 0
        assert len(calls) == 1
    finally:
        client.close()


def test_structure_validation_failure_is_not_a_blind_transport_retry(chat):
    cid = create(chat)
    def invalid(*args):
        raise WorkspaceError("离线方案结构无效")
    chat.control["generate"] = invalid
    result = send(chat, cid, "解释范围", "invalid-structure").json()
    assert result["status"] == "failed"
    assert result["failure"]["reason"] == "structure_invalid"
    assert result["failure"]["can_retry"] is False
    assert result["failure"]["can_restart"] is False


def test_timeout_is_separate_from_connection_failure(chat, monkeypatch):
    cid = create(chat)
    def handler(request):
        raise module.ReadTimeout("offline timeout", request=request)
    client, module = offline_generator(chat, monkeypatch, handler, max_retries=0)
    try:
        result = send(chat, cid, "解释范围", "offline-timeout").json()
        assert result["failure"]["reason"] == "timeout"
        assert result["failure"]["can_retry"] is True
    finally:
        client.close()


def plan_context(quote="只有CPU，没有GPU"):
    return {"brief": {"resources": quote}, "papers": [], "history": [],
            "plan_generation": {"allowed": True, "method": "computational",
                                "question": {"item_id": "q", "content": "比较两种计算方法"},
                                "supersedes_id": None}}


def user_statement(context):
    plan = rule_plan(context).suggestions[0].plan
    statement = plan.sections[3].statements[0]
    statement.basis = "user"
    statement.quote = context["brief"]["resources"]
    return plan, statement


@pytest.mark.parametrize("claim", ["用户已确认有GPU可供使用", "用户有GPU", "用户只有CPU，没有GPU；建议准备脚本"])
def test_user_quote_cannot_support_contrary_or_added_claim(claim):
    context = plan_context()
    plan, statement = user_statement(context)
    statement.text = claim
    with pytest.raises(WorkspaceError):
        validate_plan(plan, context, "生成方案")


@pytest.mark.parametrize("quote,claim", [
    ("多个随机种子", "固定多个随机种子，建议3–5个"),
    ("四周", "用户期限为四周，准备小规模仿真脚本"),
])
def test_added_quantity_or_task_cannot_inherit_user_attribution(quote, claim):
    context = plan_context(quote)
    plan, statement = user_statement(context)
    statement.text = claim
    with pytest.raises(WorkspaceError):
        validate_plan(plan, context, "生成方案")


@pytest.mark.parametrize("quote,claim", [
    ("只有CPU，没有GPU", "用户只有 CPU，没有 GPU"),
    ("没有GPU", "用户暂无GPU"),
    ("四周", "用户期限为四周"),
])
def test_narrow_synonyms_and_literal_user_facts_still_pass(quote, claim):
    context = plan_context(quote)
    plan, statement = user_statement(context)
    statement.text = claim
    validate_plan(plan, context, "生成方案")


def test_literature_claim_must_split_inference():
    context = plan_context()
    context["papers"] = [{"paper_id": "p", "abstract": "方法使用近端项。", "excerpts": []}]
    plan = rule_plan(context).suggestions[0].plan
    plan.references = ["p"]
    statement = plan.sections[1].statements[0]
    statement.basis = "literature"
    statement.paper_ids = ["p"]
    statement.quote = "方法使用近端项。"
    statement.text = "方法使用近端项，因此建议所有项目采用。"
    with pytest.raises(WorkspaceError):
        validate_plan(plan, context, "生成方案")
    statement.text = "方法使用近端项。"
    validate_plan(plan, context, "生成方案")


def test_critical_user_conflict_cannot_be_relabelled_as_proposal():
    context = plan_context()
    plan, statement = user_statement(context)
    statement.basis = "proposal"
    statement.quote = ""
    statement.text = "建议使用GPU进行训练。"
    with pytest.raises(WorkspaceError):
        validate_plan(plan, context, "生成方案")
    statement.text = "建议用户核对是否需要额外资源。"
    validate_plan(plan, context, "生成方案")


def test_failed_revision_keeps_v1_until_recovered_draft_is_adopted(chat, monkeypatch):
    attach_exports(chat)
    cid = create(chat)
    chat.control["generate"] = lambda area, text, context: offline_plan(context)
    first = send(chat, cid, "请生成计算研究方案", "initial-plan",
                 plan_request={"method": "computational"}).json()
    assert first["status"] == "completed"
    sid = first["suggestions"][0]["suggestion_id"]
    chosen = chat.client.post(
        f"/api/projects/p1/conversations/{cid}/suggestions/{sid}/apply", json={}
    ).json()
    assert chosen["item"]["plan_version"] == 1
    v1_id = chosen["item"]["item_id"]
    v1_url = f"/api/projects/p1/exports/items/{v1_id}?format=md"
    v1_before = chat.client.get(v1_url)
    assert v1_before.status_code == 200
    attempts = []
    failing = [True]

    def handler(request):
        attempts.append(1)
        if failing[0]:
            raise module.ConnectError("offline", request=request)
        return model_response(module, request)

    client, module = offline_generator(chat, monkeypatch, handler)

    # Use the already installed offline transport through the fixture generator,
    # then produce the same structured plan the regular design path validates.
    transport_generate = chat.control["generate"]
    chat.control["generate"] = lambda area, text, context: (
        transport_generate(area, text, context), offline_plan(context)
    )[1]
    try:
        base = f"/api/projects/p1/conversations/{cid}/requests/revision-round"
        body = {"plan_request": {"method": "computational", "supersedes_id": v1_id}}
        assert send(chat, cid, "请修订当前方案", "revision-round", **body).json()["status"] == "failed"
        assert chat.client.post(base + "/retry", json={}).json()["status"] == "failed"
        assert len(attempts) == 6
        assert next(i for i in chat.services["p1"].load().items if i.item_id == v1_id).status == "active"
        assert chat.client.get(v1_url).content == v1_before.content
        failing[0] = False
        recovered = chat.client.post(base + "/restart", json={"acknowledge_model_cost": True}).json()
        assert recovered["status"] == "completed" and recovered["suggestions"]
        assert next(i for i in chat.services["p1"].load().items if i.item_id == v1_id).status == "active"
        assert chat.client.get(v1_url).content == v1_before.content
        draft = recovered["suggestions"][0]
        adopted = chat.client.post(
            f"/api/projects/p1/conversations/{cid}/suggestions/{draft['suggestion_id']}/apply", json={}
        ).json()
        assert adopted["item"]["plan_version"] == 2
        assert next(i for i in chat.services["p1"].load().items if i.item_id == v1_id).status == "superseded"
        v2 = chat.client.get(f"/api/projects/p1/exports/items/{adopted['item']['item_id']}?format=md")
        assert v2.status_code == 200 and "版本：2" in v2.content.decode("utf-8")
        artifact_dir = os.environ.get("SRA_P1_ARTIFACT_DIR")
        if artifact_dir:
            target = Path(artifact_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "offline-plan-v1-before-revision.md").write_bytes(v1_before.content)
            (target / "offline-plan-v2-after-adoption.md").write_bytes(v2.content)
    finally:
        client.close()
