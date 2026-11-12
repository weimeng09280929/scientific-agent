"""Evidence projection and turn continuity without paper-specific rules."""

from core.knowledge.schema import Paper, PaperChunk
from core.workspace.paper_reading import numbered_papers, provided_paper, resolve_quote
from core.workspace.research_coach import gap_requested
from core.workspace.schema import DirectPaperWireDraft, GapSource
from tests.local_backup_fixture import expect
from tests.question_paper_reading_fixture import METHOD, explain
from tests.research_partner_fixture import create, send
from tests.test_question_guided_paper_reading import reading


def test_table_window_keeps_header_rows_definition_and_qualified_result():
    paper = Paper(paper_id="p", title="Independent evaluation", metadata={"chunk_origin": "body"})
    table = (
        "Scope notes before the measured comparison.\n" * 21
        + "Model Top-5 Top-10 Time\n"
        + "Baseline 42.0 61.2 12.0\n"
        + "Candidate 39.1 63.5 17.8\n"
        + "Table 7: The scores under the two settings.\n"
    )
    definition = "The retrieval accuracy, which is the fraction of questions with any matching text, is measured per question."
    qualifier = "However, the candidate is worse than the baseline at the smaller setting."
    windows = [
        PaperChunk(paper_id="p", chunk_id="def", chunk_index=0, page=2, text=definition),
        PaperChunk(paper_id="p", chunk_id="tab", chunk_index=1, page=4, text=table),
        PaperChunk(paper_id="p", chunk_id="limits", chunk_index=2, page=4, text=qualifier),
    ]
    projected = provided_paper(paper, windows, text="表 7 的两种设置效果一样吗？请核对分母。")
    by_id = {e["chunk_id"]: e for e in projected["excerpts"]}
    assert set(by_id) == {"def", "tab", "limits"}
    for chunk in windows:
        part = by_id[chunk.chunk_id]
        assert part["text"] == chunk.text[part["start"]:part["end"]]
    assert all(text in by_id["tab"]["text"] for text in (
        "Model Top-5 Top-10 Time", "Baseline 42.0 61.2 12.0",
        "Candidate 39.1 63.5 17.8", "Table 7:",
    ))


def test_direct_followups_preserve_paper_and_user_question_without_assistant_claims(reading):
    s = reading
    cid = create(s)
    question = "只看这篇论文。Cedar 方法的输入和步骤是什么？"
    bad_claim = "PRIOR_ASSISTANT_ERROR_MUST_NOT_BE_EVIDENCE"

    def first(ctx, text):
        return explain(ctx, text).model_copy(update={"reply": bad_claim})

    s["control"]["result"] = first
    result = expect(send(s, cid, question, rid="reading-start", selected_paper_ids=[s["pdf"]]))
    assert result["messages"][-1]["content"] == bad_claim
    s["control"]["result"] = explain
    for i, prompt in enumerate(("为什么？", "重排呢？", "没看懂，请再简单一点")):
        expect(send(s, cid, prompt, rid=f"reading-follow-{i}"))
        call = s["calls"][-1]
        context = call["context"]
        assert context["reading_question"] == question and context["history"] == []
        assert [p["paper_id"] for p in context["papers"]] == [s["pdf"]]
        assert any(METHOD in e["text"] for e in context["papers"][0]["excerpts"])
        assert bad_claim not in call["prompt"]
    expect(send(s, cid, "只看这篇论文。讲讲方法。", rid="switch-paper", selected_paper_ids=["birch"]))
    assert [p["paper_id"] for p in s["calls"][-1]["context"]["papers"]] == ["birch"]


def test_direct_reading_contract_omits_other_task_schemas():
    schema = DirectPaperWireDraft.model_json_schema()
    assert set(schema["properties"]) == {"reply", "cited_paper_ids", "reading_citations"}
    assert set(schema["$defs"]) == {"GapSource"}
    assert not gap_requested("能判断论文没有研究这种设定吗？不要提出候选研究问题。")
    assert gap_requested("请整理研究缺口，但不要生成候选研究问题。")


def test_moved_history_cannot_supply_the_current_followup_target(reading, monkeypatch):
    from core.workspace.conversations import ConversationService

    s = reading
    cid = create(s)
    expect(send(s, cid, "只看这篇论文。Cedar 方法怎样工作？", rid="before-move", selected_paper_ids=[s["pdf"]]))
    original = ConversationService._recent

    def from_previous_project(service, conversation):
        return [{**message, "historical_project_context": True, "history_project_id": "old-project"}
                for message in original(service, conversation)]

    monkeypatch.setattr(ConversationService, "_recent", from_previous_project)
    expect(send(s, cid, "为什么？", rid="after-move"))
    ctx = s["calls"][-1]["context"]
    assert not ctx["reading_selection"]["active"]
    assert ctx["reading_question"] is None


def test_full_numbered_table_quote_fits_the_server_snapshot():
    text = "Model | Hits | Time\n" + "Configuration row 12.7 14.4\n" * 45
    assert 1200 < len(text) <= 1600
    paper = {"paper_id": "p", "title": "Table", "abstract": "", "excerpts": [{
        "excerpt_id": "e", "text": text, "page": 4, "start": 0, "end": len(text),
    }]}
    delivered = numbered_papers([paper], {"request_id": "only-this-request"})[0]
    source = GapSource(evidence_id=delivered["excerpts"][0]["evidence_id"])
    result = resolve_quote(source, {"p": delivered})
    assert result.quote == text and result.quote_end == len(text) and result.page == 4
