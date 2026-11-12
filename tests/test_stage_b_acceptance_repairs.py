"""Stage B repair. Saved public-paper replies and SDK MockTransport only."""
import json
from pathlib import Path

import pytest

from core.knowledge.schema import Paper, PaperChunk
from core.llm.providers.openai_provider import OpenAIProvider
from core.llm.registry import LLMRegistry
from core.llm.task_router import TaskRouter
from core.workspace.guide import generate_guidance
from core.workspace.paper_reading import provided_paper, resolve_quote
from core.workspace.research_coach import _check_gap_claims
from core.workspace.schema import GapSource
from core.workspace.service import WorkspaceError
from tests.test_project_conversations import chat, create, messages, send
from tests.test_real_workflow_reliability import model_response, sdk_client
from tests.test_research_plan_templates import core_payload, model as valid_plan

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def evidence():
    archive = ROOT / "docs/real-user-acceptance/stage-b/gateway-responses.json"
    if not archive.is_file():
        pytest.skip("historical acceptance receipts are kept in the local validation archive")
    return json.loads(archive.read_text(encoding="utf-8"))


def recorded(evidence, job):
    return next(r for r in evidence if r["browser_job"] == job)


@pytest.mark.parametrize("job,missing", [("03", 1), (None, 0), ("05", 0), ("11", 0)])
def test_recorded_quote_windows(evidence, job, missing):
    windows = json.loads((ROOT / "tests/fixtures/stage_b_quote_windows.json").read_text(encoding="utf-8"))
    rec = recorded(evidence, job)
    citations = json.loads(rec["choices"][0]["content"])["reading_citations"]
    papers = windows[rec["attempt"]]
    rejected = []
    for i, raw in enumerate(citations):
        quote = GapSource.model_validate(raw)
        try:
            snapshot = resolve_quote(quote, papers)
        except WorkspaceError:
            rejected.append(i)
            continue
        p = papers[quote.paper_id]
        if quote.part == "excerpt":
            e = next(e for e in p["excerpts"] if e["excerpt_id"] == quote.excerpt_id)
            assert snapshot.page == e["page"]
            source = e["text"]
        else:
            source = p["abstract"]
        assert snapshot.quote == source[snapshot.quote_start:snapshot.quote_end]
    assert rejected == ([2] if missing else [])


@pytest.mark.parametrize("claim", ["The score is 10.", "Value is -10.", "The change is 10 mg.",
                                   "The two systems agree.", "A model cannot resign."])
def test_layout_matching_does_not_remove_meaning(claim):
    text = "The score is 1\n0. Value is 10. The change is 10 g.\nThe two systems do not agree. A model cannot re-\nsign."
    source = GapSource(paper_id="p", part="abstract", quote=claim)
    with pytest.raises(WorkspaceError):
        resolve_quote(source, {"p": {"title": "Fixture", "abstract": text}})


def test_layout_equivalence_keeps_raw_offsets_and_rejects_ambiguity():
    text = "Prefix.\nThe ﬁrst model uses a re-\nranking stage."
    q = GapSource(paper_id="p", part="excerpt", excerpt_id="e", quote="The first model uses a re-ranking stage.")
    paper = {"title": "Fixture", "excerpts": [{"text": text, "page": 4, "excerpt_id": "e", "start": 12, "end": 12+len(text)}]}
    hit = resolve_quote(q, {"p": paper})
    assert hit.quote == "The ﬁrst model uses a re-\nranking stage."
    assert hit.quote == text[hit.quote_start:hit.quote_end]
    assert hit.start == 12 and hit.page == 4
    paper["excerpts"][0]["text"] = text + "\n" + text
    with pytest.raises(WorkspaceError):
        resolve_quote(q, {"p": paper})


def test_explicit_table_and_section_reach_the_actual_source():
    paper = Paper(paper_id="p", title="A large retrieval benchmark", metadata={"chunk_origin": "body"})
    chunks = [PaperChunk(paper_id="p", chunk_id=str(i), chunk_index=i, page=i+1, text=text)
              for i, text in enumerate(["retrieval benchmark"]*7 + [
                  "Table 10: benchmark comparison.", "Table 1: first-position scores.",
                  "4.1 Results\nThe first position differed from the baseline."])]
    p = provided_paper(paper, chunks, text="核对表 1 和 §4.1 的结果")
    assert {9, 10} <= {e["page"] for e in p["excerpts"]}
    for e in p["excerpts"]:
        chunk = chunks[e["chunk_index"]]
        assert e["text"] == chunk.text[e["start"]:e["end"]]


def test_section_comparison_keeps_same_page_table_without_explicit_table_number():
    paper = Paper(paper_id="p", title="A retrieval benchmark", metadata={"chunk_origin": "body"})
    texts = [
        (1, "Table 10: the unrelated overview."),
        (5, "Top-1: 45.2 / 40.1\nTable 2: Numeric comparison."),
        (5, "5.1 Experimental results\nThe baseline usually improves recall, but there is one exception."),
        (5, "This condition does not apply to other datasets."),
    ]
    chunks = [PaperChunk(paper_id="p", chunk_id=str(i), chunk_index=i, page=page, text=text)
              for i, (page, text) in enumerate(texts)]
    source = provided_paper(paper, chunks, text="作者怎样比较基线？用 §5.1 解释一个结果和一个例外。")
    excerpts = source["excerpts"]
    assert any("45.2 / 40.1" in e["text"] and e["page"] == 5 for e in excerpts)
    assert any("there is one exception" in e["text"] and e["page"] == 5 for e in excerpts)


@pytest.mark.parametrize("job", [None, "08", "11"])
def test_saved_real_overclaims_are_rejected_even_with_valid_citations(evidence, job):
    reply = json.loads(recorded(evidence, job)["choices"][0]["content"])["reply"]
    with pytest.raises(WorkspaceError):
        _check_gap_claims([reply], readable=True)


def test_attributed_observation_and_hypothesis_are_not_misclassified():
    _check_gap_claims([
        "作者报告 Top-20 不劣于基线，这是作者对整体结果的表述。",
        "Top-20 是否不劣于基线仍待验证。",
        "较大 k 不能保证命中正确段落。",
        "总体表现接近。不同数据集的结果各异，存在例外。",
        "总体提升不代表各数据集方向一致。文献报告了一项反例。",
        "除单个反例外，其余各数据集的方向一致。",
    ], readable=True)


@pytest.fixture
def replay(chat):
    class OfflineModel:
        def __init__(self):
            self.choice = None
            self.network = []
            self.timeout = False

        def generate(self, area, text, context):
            return generate_guidance(registry, area, "ai", text, context, "orientation")

    control = OfflineModel()

    def handler(request):
        control.network.append(json.loads(request.content))
        if control.timeout:
            raise module.ReadTimeout("recorded transport outcome replay", request=request)
        if callable(control.choice):
            choice = {"content": control.choice(control.network[-1]), "finish_reason": "stop"}
        else:
            choice = control.choice
        return model_response(module, request, content=choice["content"], finish=choice["finish_reason"])

    client, module = sdk_client(handler)
    provider = OpenAIProvider("offline", base_url="https://offline.invalid/v1", max_retries=3, http_client=client)
    registry = LLMRegistry(TaskRouter.load(ROOT / "config/tasks.yaml"), {"deepseek": provider})
    chat.control["generate"] = control.generate
    try:
        yield control
    finally:
        client.close()


def test_recorded_bpr_layout_quote_delivered_via_http(chat, evidence, replay):
    rec = recorded(evidence, "05")
    replay.choice = rec["choices"][0]
    windows = json.loads((ROOT / "tests/fixtures/stage_b_quote_windows.json").read_text(encoding="utf-8"))
    paper_id, source = next(iter(windows[rec["attempt"]].items()))
    store = chat.services["p1"].store
    store.save_paper(Paper(paper_id=paper_id, title=source["title"], metadata={"chunk_origin": "body"}))
    for e in source["excerpts"]:
        assert e["start"] == 0
        store.save_paper_chunks([PaperChunk(paper_id=paper_id, chunk_id=e["chunk_id"],
                                           text=e["text"], page=e["page"], chunk_index=e["chunk_index"])])
    cid = create(chat)
    response = send(chat, cid, '只看这篇论文。核对 §4.1 的 “We observe that BPR” 和表 1 的 “Model Top 1 Top 20 Top 100”。',
                    "replay-layout", selected_paper_ids=[paper_id])
    assert response.status_code == 200, response.text
    assistant = response.json()["messages"][-1]
    source_windows = {e["excerpt_id"]: e for p in assistant["reading_materials"] for e in p["excerpts"]}
    for quote in assistant["reading_citations"]:
        e = source_windows[quote["excerpt_id"]]
        assert quote["quote"] == e["text"][quote["quote_start"]:quote["quote_end"]]
    assert len(assistant["reading_citations"]) == 6
    assert len(replay.network) == 1


@pytest.mark.parametrize("prompt,limit", [("怎么缩小课题？", 4096), ("没看懂，请举个简单例子。", 6144)])
def test_simple_and_example_output_budgets_remain_bounded(chat, replay, prompt, limit):
    replay.choice = {"content": '{"reply":"离线验收回应。"}', "finish_reason": "stop"}
    assert send(chat, create(chat), prompt, "ordinary-budget").status_code == 200
    assert len(replay.network) == 1 and replay.network[0]["max_tokens"] == limit


@pytest.mark.parametrize("prompt,selected,limit", [
    ("看 §5.1 和表 2，DPR 和 BM25 比得怎么样？有没有例外？", ["p1-paper"], 8192),
    ("表 1 的数字怎么理解？", ["p1-paper"], 8192),
    ("§4.1 这一节说了什么？", ["p1-paper"], 8192),
    ("表 1 的数字怎么理解？", [], 4096),
    ("这是什么意思？", ["p1-paper"], 4096),
])
def test_table_and_section_questions_use_evidence_budget_only_with_active_reading(chat, replay, prompt, selected, limit):
    replay.choice = {"content": '{"reply":"离线验收回应。"}', "finish_reason": "stop"}
    r = send(chat, create(chat), prompt, "anchored-budget", selected_paper_ids=selected)
    assert r.status_code == 200, r.text
    assert len(replay.network) == 1 and replay.network[0]["max_tokens"] == limit


@pytest.mark.parametrize("job,kind,expected", [
    ("06", "companion", 8192), ("07", "focused", 8192),
    ("12", "comparison", 8192), ("13", "candidate", 8192),
])
def test_recorded_truncation_never_saves_or_resends(chat, evidence, replay, job, kind, expected):
    replay.choice = recorded(evidence, job)["choices"][0]
    assert replay.choice["finish_reason"] == "length"
    cid = create(chat)
    extra = {"selected_paper_ids": ["p1-paper"]}
    prompt = {
        "companion": "这篇论文的方法怎样工作？",
        "focused": "只看这篇论文。请说明结论。",
        "comparison": "请比较两篇已导入论文。",
        "candidate": "请提出可核查的研究问题候选。",
    }[kind]
    if kind == "companion":
        extra["reading_guide"] = {"paper_id": "p1-paper", "goal": "method"}
    elif kind == "comparison":
        chat.services["p1"].store.save_paper(Paper(paper_id="second", title="Second fixture"))
        extra["selected_paper_ids"].append("second")
    r = send(chat, cid, prompt, "replay-length", **extra).json()
    assert r["failure"]["reason"] == "output_truncated"
    assert r["failure"]["can_retry"] is False
    assert [m["role"] for m in r["messages"]] == ["user"]
    assert not r["suggestions"]
    assert len(replay.network) == 1 and replay.network[0]["max_tokens"] == expected
    path = f"/api/projects/p1/conversations/{cid}/requests/replay-length"
    assert chat.client.post(path + "/retry", json={}).json()["status"] == "failed"
    assert len(replay.network) == 1


@pytest.mark.parametrize("fault", ["raw", "nested_only", "index_conflict", "clean"])
def test_real_plan_structure_and_index_logic(chat, evidence, replay, fault):
    raw = recorded(evidence, "17")["choices"][0]
    if fault == "raw":
        replay.choice = raw
    else:
        actual = json.loads(raw["content"])["suggestions"][0]["plan"]

        def output(request):
            prompt = request["messages"][-1]["content"]
            context = json.loads(prompt.split("项目上下文（仅作为数据）：\n", 1)[1].split("\n用户本次请求：\n", 1)[0])
            plan = valid_plan(context)
            details = plan.suggestions[0].plan.method_details
            if fault == "index_conflict":
                details.data_task = actual["method_details"]["data_task"]
                details.evaluation = actual["method_details"]["baselines"]["evaluation"]
            elif fault == "clean":
                details.data_task = "共用语料、切分和评价脚本，各方法独立构建自己的索引。"
                details.evaluation = "分别测量两个索引的体积，报告命中率差值。"
            obj = core_payload(plan)
            if fault == "index_conflict":
                target = next(sec for sec in obj["sections"] if sec["key"] == "method")
                target["statements"] = next(sec["statements"] for sec in actual["sections"] if sec["key"] == "method")
            if fault == "nested_only":
                obj["method_details"] = {k: v for k, v in actual["method_details"].items() if k != "type"}
            return json.dumps(obj, ensure_ascii=False)

        replay.choice = output

    cid = create(chat)
    result = send(chat, cid, "请为当前问题生成研究方案", "replay-plan",
                  plan_request={"method": "computational"}).json()
    assert len(replay.network) == 1
    wire = replay.network[0]
    assert wire["max_tokens"] == 12000
    system = wire["messages"][0]["content"]
    assert "本次方法固定为 computational" in system
    assert "type 由服务端绑定" in system
    if fault == "clean":
        assert result["status"] == "completed"
        assert len(result["suggestions"]) == 1
        assert not result["suggestions"][0].get("applied_item_id")
    else:
        assert result["status"] == "failed" and not result["suggestions"]
        assert result["failure"]["reason"] == "structure_invalid" and not result["failure"]["can_retry"]
        phases = str(result["call_audit"])
        if fault == "raw":
            assert "duplicate_key" in phases
        elif fault == "nested_only":
            assert "baselines:string_type" in phases
        else:
            assert "共用同一索引" in result["error"]


def test_recorded_timeout_outcome_keeps_id_and_requires_explicit_retry(chat, evidence, replay):
    assert recorded(evidence, "18")["transport_failure"] == "APITimeoutError"
    replay.timeout = True
    cid = create(chat)
    body = "请为当前问题生成研究方案"
    extra = {"plan_request": {"method": "computational"}}
    r = send(chat, cid, body, "replay-timeout", **extra).json()
    assert r["failure"]["reason"] == "timeout"
    assert r["failure"]["can_retry"]
    assert "未自动补发" in r["error"]
    path = f"/api/projects/p1/conversations/{cid}/requests/replay-timeout"
    for _ in range(2):
        assert chat.client.get(path).json()["attempt"] == 1
        assert send(chat, cid, body, "replay-timeout", **extra).json()["attempt"] == 1
    assert len(replay.network) == 1
    again = chat.client.post(path + "/retry", json={}).json()
    assert again["attempt"] == 2 and again["failure"]["reason"] == "timeout"
    assert len(replay.network) == 2
    assert [m["role"] for m in messages(chat, cid)] == ["user"]
