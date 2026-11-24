"""Exercise the real authenticated message API with an offline registry."""
import json
import os
from pathlib import Path
from threading import Event
import time

import pytest

from core.llm.base import LLMError
from core.workspace.reading_session import PassageRequest, digest, preview_version
from core.workspace.schema import GuideDraft
from tests.guided_paper_reading_fixture import PAPER_ID, PREVIEW, SENTENCE, passage_reply, seed_passages
from tests.local_backup_fixture import expect
from tests.question_paper_reading_fixture import COMPARE, EXPERIMENT, LIMIT, METHOD, explain, seed
from tests.research_partner_fixture import create, sample, send, workspace


@pytest.fixture
def reading(tmp_path):
    with sample(tmp_path) as s:
        s["pdf"] = seed(s)
        seed_passages(s)
        s["cid"] = create(s)
        s["control"]["result"] = lambda context, text: passage_reply(context, text) if "passage" in context else explain(context, text)
        yield s


def path(s, suffix, cid=None):
    return f'/api/projects/{s["pid"]}/conversations/{cid or s["cid"]}/{suffix}'


def passage(s, **changes):
    start = PREVIEW.rindex(SENTENCE)
    value = {
        "operation": "translate", "paper_id": PAPER_ID, "source": "preview",
        "version": preview_version(s["store"].get_paper(PAPER_ID)),
        "start": start, "end": start + len(SENTENCE), "text": SENTENCE,
        **changes,
    }
    try:
        rid = PassageRequest.model_validate(value).identity()
    except ValueError:
        rid = "invalid-passage-test"
    return rid, value


def submit(s, payload, *, wait=True, client=None, cid=None):
    rid, value = payload
    return (client or s["a"]).post(path(s, "messages", cid), json={
        "content": "论文选段", "request_id": rid, "passage": value, "wait_for_completion": wait,
    })


def test_translation_and_explanation_are_local_versioned_and_distinct(reading):
    s = reading
    before = workspace(s)
    baseline = expect(s["a"].get(path(s, "").rstrip("/")))["conversation"]
    requests = [passage(s), passage(s, operation="explain")]
    receipts = []
    for op, data in zip(("translate", "explain"), requests):
        accepted = expect(submit(s, data))
        snapshot = accepted["passage"]["source"]
        result = accepted["passage"]["result"]
        assert snapshot["text"] == SENTENCE and snapshot["start"] == PREVIEW.rindex(SENTENCE)
        assert snapshot["version"] == data[1]["version"] and not snapshot["historical"]
        assert bool(result["translation"]) == (op == "translate")
        assert bool(result["explanation"]) == (op == "explain")
        assert accepted["messages"] == accepted["suggestions"] == []
        call = s["calls"][-1]
        assert call["context"]["passage"] == {k: snapshot[k] for k in ("text", "before", "after", "scope", "historical")}
        assert call["automatic_retry"] is False
        assert "数字、单位、公式符号、否定和条件" in call["system"]
        assert "不确定语气" in call["system"] and "不得改写成因果" in call["system"]
        assert ("写入 translation" if op == "translate" else "写入 explanation") in call["system"]
        assert len([a for a in accepted["call_audit"]["entries"] if a["event"] == "network"]) == 1
        receipts.append({"source": snapshot, "result": result, "actual_provider_input": call,
                         "request_id": data[0], "status": accepted["status"]})
    assert expect(submit(s, requests[0]))["passage"]["result"] == receipts[0]["result"]
    assert len(s["calls"]) == 2
    assert expect(s["a"].get(path(s, "requests/" + requests[0][0])))["passage"]["source"] == receipts[0]["source"]
    assert expect(s["a"].get(path(s, "messages")))["messages"] == []
    assert expect(s["a"].get(path(s, "").rstrip("/")))["conversation"] == baseline
    assert workspace(s)["items"] == before["items"]
    if out := os.environ.get("SRA_GUIDED_EVIDENCE"):
        (Path(out) / "provider-receipts.json").write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("fault", ["empty", "long", "offset", "wrong-text", "version", "foreign-paper", "wrong-project", "foreign-user", "forged-id"])
def test_reject_invalid_selection_before_model(reading, fault):
    s = reading
    rid, body = passage(s)
    patches = {
        "empty": {"text": "", "end": body["start"]}, "long": {"text": "a" * 1001},
        "offset": {"start": body["start"] + 1}, "wrong-text": {"text": "Replaced sentence."},
        "version": {"version": "0" * 64}, "foreign-paper": {"paper_id": "other-account"},
        "wrong-project": {"paper_id": "other-project"},
    }
    payload = passage(s, **patches[fault]) if fault in patches else (rid, body)
    if fault == "forged-id":
        payload = ("forged", body)
    response = submit(s, payload, client=s["b"] if fault == "foreign-user" else None)
    assert response.status_code == (404 if fault == "foreign-user" else 422)
    assert not s["calls"]
    assert s["a"].get(path(s, "requests/" + payload[0])).status_code == 404


@pytest.mark.parametrize("goal,question,quote,page", [
    ("method", "这篇论文的方法怎样工作？", METHOD, 4),
    ("evidence", "这篇论文的实验怎样比较？", EXPERIMENT, 5),
    ("limitations", "这篇论文有哪些局限？", LIMIT, 6),
])
def test_companion_uses_explicit_paper_and_real_late_excerpts(reading, goal, question, quote, page):
    s = reading
    reply = expect(send(s, s["cid"], question, selected_paper_ids=["birch"],
                        reading_guide={"paper_id": s["pdf"], "goal": goal}))["messages"][-1]
    packet = s["calls"][0]["context"]
    assert [p["paper_id"] for p in packet["papers"]] == [s["pdf"]]
    assert packet["reading_guide"] == reply["reading_guide"]
    assert any(quote in e["text"] and e["page"] == page for e in packet["papers"][0]["excerpts"])
    assert reply["reading_materials"] == packet["papers"]
    assert "【新手论文伴读】" in s["calls"][0]["system"]
    assert not (reply["actions"] or reply["research_gaps"] or reply["suggestion_ids"])


def test_companion_confusion_term_pause_and_missing_evidence(reading):
    s = reading
    cid = s["cid"]
    expect(send(s, cid, "这篇论文的方法怎样工作？", reading_guide={"paper_id": "birch", "goal": "method"}))
    for i, question in enumerate(("没看懂，请再简单一点", "caption 是什么意思？")):
        result = expect(send(s, cid, question, rid=f"follow-{i}", selected_paper_ids=[s["pdf"]]))["messages"][-1]
        packet = s["calls"][-1]["context"]
        assert packet["reading_guide"] == {"paper_id": "birch", "goal": "method"}
        assert packet["reading_question"] == "这篇论文的方法怎样工作？" and packet["history"] == []
        assert [p["paper_id"] for p in packet["papers"]] == ["birch"]
        assert not result["actions"] and not result["suggestion_ids"] and not result["research_gaps"]
    paused = expect(send(s, cid, "暂停一下", rid="pause"))["messages"][-1]
    assert "暂停伴读" in paused["content"] and len(s["calls"]) == 3
    reply = expect(send(s, cid, "这篇论文有哪些局限？", rid="missing",
                        reading_guide={"paper_id": "title-only", "goal": "limitations"}))["messages"][-1]
    assert "不足" in reply["content"] and "仅题名" in reply["reading_materials"][0]["scope"]
    assert not reply["reading_citations"] and not workspace(s)["items"]


def test_snapshot_source_survives_edit_delete_and_wrong_conversation_is_rejected(reading):
    s = reading
    msg = expect(send(s, s["cid"], "只看这篇论文的 Birch 方法", selected_paper_ids=["birch"]))["messages"][-1]
    cite = msg["reading_citations"][0]
    assert cite["quote"] == COMPARE
    old = s["store"].get_paper("birch")
    s["store"].save_paper(old.model_copy(update={"title": "Replacement", "abstract": "Unrelated new text."}))
    with s["store"]._connect() as conn:
        conn.execute("DELETE FROM papers WHERE paper_id='birch'")
    data = passage(s, paper_id="birch", source="citation", message_id=msg["message_id"], locator="0",
                   start=0, end=len(COMPARE), text=COMPARE, version=digest(COMPARE))
    assert submit(s, data, cid=create(s)).status_code == 422
    assert submit(s, passage(s, **{**data[1], "locator": "1"})).status_code == 422
    saved = expect(submit(s, data))["passage"]["source"]
    assert saved["historical"] and saved["text"] == COMPARE and saved["title"] == old.title
    excerpt = msg["reading_materials"][0]["excerpts"][0]
    body = passage(s, paper_id="birch", source="excerpt", message_id=msg["message_id"], locator=excerpt["excerpt_id"],
                   start=0, end=len(excerpt["text"]), text=excerpt["text"], version=digest(excerpt["text"]))
    assert expect(submit(s, body))["passage"]["source"]["excerpt_id"] == excerpt["excerpt_id"]
    assert len(s["calls"]) == 3


def test_local_cancel_discards_late_result_and_duplicate_does_not_run(reading):
    s = reading
    entered, release = Event(), Event()

    def hold(context, text):
        entered.set()
        assert release.wait(12)
        return passage_reply(context, text)

    s["control"]["result"] = hold
    data = passage(s)
    rid = data[0]
    try:
        assert submit(s, data, wait=False).status_code == 202
        assert entered.wait(8)
        assert submit(s, data, wait=False).status_code == 202
        stopped = expect(s["a"].post(path(s, f"requests/{rid}/cancel-passage")))
        assert stopped["status"] == "cancelled" and stopped["passage"]["result"] is None
        assert submit(s, data, wait=False).status_code == 409
        assert submit(s, passage(s, operation="explain"), wait=False).status_code == 409
    finally:
        release.set()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        current = expect(s["a"].get(path(s, f"requests/{rid}")))
        if all(e["outcome"] != "processing" for e in current["call_audit"]["entries"]):
            break
        time.sleep(.05)
    assert current["status"] == "cancelled" and current["passage"]["result"] is None
    assert len(s["calls"]) == 1


def test_failure_requires_explicit_retry_and_preserves_version(reading):
    s = reading
    data = passage(s)
    rid = data[0]

    def fail(_ctx, _text):
        raise LLMError("offline test")

    s["control"]["result"] = fail
    failed = submit(s, data)
    assert failed.status_code == 502
    assert failed.json()["failure"]["can_retry"] and not failed.json()["failure"]["can_restart"]
    assert submit(s, data).status_code == 502
    s["store"].save_paper(s["store"].get_paper(PAPER_ID).model_copy(update={"abstract": "New text version."}))
    s["control"]["result"] = passage_reply
    result = expect(s["a"].post(path(s, f"requests/{rid}/retry"), json={}))
    assert result["passage"]["source"]["text"] == SENTENCE and result["passage"]["source"]["version"] == data[1]["version"]
    assert len(s["calls"]) == 2


@pytest.mark.parametrize("fault", ["connection", "invalid-json"])
def test_one_actual_sdk_attempt_per_explicit_passage_action(tmp_path, fault):
    from core.invocation_audit import AuditScope, read_audit
    from core.llm.providers.openai_provider import OpenAIProvider
    from core.llm.registry import LLMRegistry
    from core.llm.task_router import TaskRouter
    from core.workspace.reading_session import passage_prompt
    from tests.test_real_workflow_reliability import model_response, sdk_client

    network = []

    def handler(request):
        network.append(request)
        if fault == "connection":
            raise module.ConnectError("offline", request=request)
        return model_response(module, request, content="NOT JSON")

    db = tmp_path / "audit.sqlite"
    db.touch()
    client, module = sdk_client(handler)
    provider = OpenAIProvider("offline-key", base_url="https://offline.invalid/v1",
                              max_retries=3, http_client=client)
    router = TaskRouter.load(Path("config/tasks.yaml"))
    registry = LLMRegistry(router, {router.get("paper_chunk_summarize").provider: provider})
    snapshot = {"operation": "translate", "text": SENTENCE, "before": "", "after": "",
                "scope": "作者摘要", "historical": False}
    try:
        with AuditScope(db, "p", "passage-one").activate(), pytest.raises(LLMError):
            passage_prompt(registry, snapshot)
        assert len(network) == 1
        rows = read_audit(db, "p", operation_id="passage-one")["entries"]
        assert len([row for row in rows if row["event"] == "network"]) == 1
    finally:
        client.close()
