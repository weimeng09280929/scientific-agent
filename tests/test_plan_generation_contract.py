"""Dedicated plan generation through FastAPI and the offline SDK transport."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from core.workspace.plan_generation import plan_output_schema
from core.workspace.schema import GuideDraft
from tests.test_project_conversations import chat, create, send
from tests.test_research_plan_templates import attach_exports, core_payload, model
from tests.test_stage_b_acceptance_repairs import replay


def context(request):
    return json.JSONDecoder().raw_decode(
        request["messages"][-1]["content"].split("项目上下文（仅作为数据）：\n", 1)[1]
    )[0]


def answer(request):
    ctx = context(request)
    draft = model(ctx)
    payload = core_payload(draft)
    paper = ctx["papers"][0]
    statement = next(s for s in payload["sections"] if s["key"] == "related_work")
    statement["statements"] = [{
        "basis": "literature", "text": "本轮提供的摘录可作为讨论依据，适用范围待核对。",
        "evidence_id": paper["preview_evidence_id"],
    }]
    return payload


def encoded(request):
    return json.dumps(answer(request), ensure_ascii=False)


@pytest.mark.parametrize("method", ["computational", "theoretical", "experimental"])
def test_core_output_has_one_method_and_no_guidance_envelope(chat, replay, method, monkeypatch):
    from core.workspace import guidance_policy

    def forbidden(*args):
        raise AssertionError("A plan must not require optional reply generation")

    monkeypatch.setattr(guidance_policy, "fallback_replies", forbidden)
    replay.choice = encoded
    cid = create(chat)
    result = send(chat, cid, "请为当前问题生成研究方案", "core",
                  plan_request={"method": method}).json()
    assert result["status"] == "completed", result["error"]
    item = result["suggestions"][0]
    assert item["plan"]["method"] == item["plan"]["method_details"]["type"] == method
    assert item["related_ids"] == [item["plan"]["question_id"]]
    assert result["messages"][-1]["suggested_replies"] == []
    wire = replay.network[0]
    schema = plan_output_schema(method).model_json_schema()
    assert set(schema["properties"]) == {"title", "sections", "conditions", "method_details", "unconfirmed"}
    assert "GuideDraft" not in schema.get("$defs", {})
    details = schema["$defs"][schema["properties"]["method_details"]["$ref"].split("/")[-1]]
    assert "type" not in details["properties"]
    assert wire["response_format"] == {"type": "json_object"} and wire["max_tokens"] == 12000
    assert "推荐回复" in wire["messages"][0]["content"]
    assert len(replay.network) == 1


@pytest.mark.parametrize("fault", [
    "duplicate_title", "missing_sections", "missing_detail", "question_id", "method",
    "revision_target", "auxiliary", "evidence", "fact", "unknown_resource", "unknown_time", "unknown_budget",
])
def test_invalid_core_stays_failed_without_reissue(chat, replay, fault):
    def output(request):
        payload = answer(request)
        if fault == "duplicate_title":
            return '{"title":"重复字段",' + json.dumps(payload, ensure_ascii=False)[1:]
        if fault == "missing_sections":
            payload.pop("sections")
        elif fault == "missing_detail":
            payload["method_details"].pop("evaluation")
        elif fault in ("question_id", "method", "revision_target"):
            payload[{"revision_target": "revised_from_draft_id"}.get(fault, fault)] = "foreign"
        elif fault == "auxiliary":
            payload["suggested_replies"] = [{"label": "无权执行", "content": {}}]
        elif fault == "evidence":
            payload["sections"][1]["statements"][0]["evidence_id"] = "evidence-other-account"
        elif fault == "fact":
            payload["sections"][0]["statements"] = [{"basis": "user", "fact_id": "fact-expired"}]
        elif fault.startswith("unknown_"):
            field = {"unknown_resource": "resources", "unknown_time": "time_budget", "unknown_budget": "budget"}[fault]
            payload["conditions"][field] = {"basis": "user", "value": "已具备 4 张 GPU、两周和预算 5000 元", "quote": ""}
        return json.dumps(payload, ensure_ascii=False)

    replay.choice = output
    cid = create(chat)
    result = send(chat, cid, "请生成方案", "invalid", plan_request={"method": "computational"}).json()
    assert result["status"] == "failed" and not result["suggestions"]
    assert [m["role"] for m in result["messages"]] == ["user"]
    assert not result["failure"]["can_retry"]
    retried = chat.client.post(f"/api/projects/p1/conversations/{cid}/requests/invalid/retry", json={})
    assert retried.json()["status"] == "failed" and len(replay.network) == 1


def test_core_revision_keeps_original_identity_and_download_evidence(chat, replay):
    replay.choice = encoded
    cid = create(chat)
    attach_exports(chat)
    request = {"method": "computational"}
    first = send(chat, cid, "请生成方案", "original", plan_request=request).json()["suggestions"][0]
    request.update(question_id=first["plan"]["question_id"], revises_suggestion_id=first["suggestion_id"])
    second = send(chat, cid, "请具体说明怎样公平比较", "revision", plan_request=request).json()
    assert second["status"] == "completed", second["error"]
    revision = second["suggestions"][0]
    assert revision["plan"]["revised_from_draft_id"] == first["suggestion_id"]
    assert revision["plan"]["question_id"] == first["plan"]["question_id"]
    assert first["suggestion_id"] != revision["suggestion_id"]
    assert all(c["value"] == "待确认" for c in revision["plan"]["conditions"].values())
    assert "文献原文快照" in chat.client.get(
        f"/api/projects/p1/conversations/{cid}/suggestions/{revision['suggestion_id']}/export"
    ).text
    repeated = send(chat, cid, "请具体说明怎样公平比较", "revision", plan_request=request).json()
    assert repeated["suggestions"] == second["suggestions"] and len(replay.network) == 2
    path = f"/api/projects/p1/conversations/{cid}/suggestions"
    response = chat.client.get(path).json()
    assert {s["suggestion_id"] for s in response["suggestions"]} >= {first["suggestion_id"], revision["suggestion_id"]}
    assert not any(s["applied_item_id"] for s in response["suggestions"])
    before = deepcopy(response["suggestions"])
    replay.choice = lambda r: '{"title":"bad","title":"duplicate"}'
    failed = send(chat, cid, "请修订待确认部分", "bad-revision", plan_request=request).json()
    assert failed["status"] == "failed"
    assert chat.client.get(path).json()["suggestions"] == before
    assert len(replay.network) == 3


def test_prior_request_evidence_cannot_enter_new_core_plan(chat, replay):
    previous = []

    def output(request):
        payload = answer(request)
        evidence = payload["sections"][1]["statements"][0]
        if previous:
            evidence["evidence_id"] = previous[0]
        else:
            previous.append(evidence["evidence_id"])
        return json.dumps(payload, ensure_ascii=False)

    replay.choice = output
    cid = create(chat)
    assert send(chat, cid, "请生成方案", "one", plan_request={"method": "computational"}).json()["status"] == "completed"
    assert send(chat, cid, "请生成另一个草案", "two", plan_request={"method": "computational"}).json()["status"] == "failed"
    assert len(replay.network) == 2


def test_strict_format_probe_is_one_website_request_without_fallback(chat, replay, monkeypatch):
    from core.llm.providers.openai_provider import OpenAIProvider
    from tools.serve_guided_experience import probe_plan_schema_once

    monkeypatch.setattr(OpenAIProvider, "structured_output", OpenAIProvider.structured_output)
    probe_plan_schema_once()
    replay.choice = encoded
    cid = create(chat)
    assert send(chat, cid, "请生成方案", "probe", plan_request={"method": "computational"}).json()["status"] == "completed"
    format = replay.network[0]["response_format"]
    assert format["type"] == "json_schema" and format["json_schema"]["strict"]
    assert "suggested_replies" not in format["json_schema"]["schema"]["properties"]
    replay.choice = lambda request: '{"title":"missing core fields"}'
    result = send(chat, cid, "请生成另一草案", "ordinary", plan_request={"method": "computational"}).json()
    assert result["status"] == "failed"
    assert replay.network[1]["response_format"] == {"type": "json_object"}
    assert len(replay.network) == 2


@pytest.mark.parametrize("attempt,digest", [
    (13, "4b2e2f758e0a3420a6561d3e7ba17c2fe1e4590c5be35b74e4c2fd2567f37bc5"),
    (14, "82e873f923e518e6a1e347144bf5b65eb8cc7c7c87f1af1a07d5de1ad52e28b3"),
])
def test_frozen_old_envelope_does_not_become_a_new_core_response(chat, replay, attempt, digest):
    path = Path(__file__).resolve().parents[1] / f"docs/real-user-acceptance/numbered-evidence-20261111/raw-failures/{attempt}-response.json"
    if not path.is_file():
        pytest.skip("historical acceptance receipts are kept in the local validation archive")
    blob = path.read_bytes()
    assert sha256(blob).hexdigest() == digest
    replay.choice = json.loads(blob)["choices"][0]
    result = send(chat, create(chat), "请生成方案", "frozen",
                  plan_request={"method": "computational"}).json()
    assert result["status"] == "failed" and not result["suggestions"]
    assert not result["failure"]["can_retry"] and len(replay.network) == 1
    if attempt == 13:
        # Old files remain readable with their original record contract.
        assert GuideDraft.model_validate_json(replay.choice["content"]).suggestions[0].plan
