"""Test source-local selection, fact checks and server-calculated comparisons."""
from types import SimpleNamespace

import pytest

from core.knowledge.schema import Paper, PaperChunk
from core.workspace.paper_reading import numbered_papers, provided_paper, reading_query
from core.workspace.reading_analysis import (
    MethodReadingDraft, TableComparison, TableReadingDraft, apply_table_calculations, method_analysis, table_calculation,
)
from core.workspace.service import WorkspaceError
from tests.local_backup_fixture import expect
from tests.question_paper_reading_fixture import COMPARE, METHOD, explain, seed
from tests.research_partner_fixture import create, sample, send


@pytest.mark.parametrize("question,expected", [
    ("同一条记录怎么变成正样本，负例又从哪里来？", "method"),
    ("How do they construct the training pairs?", "method"),
    ("去掉缓存后会怎样？哪组更快？", "experiments"),
    ("命中具体指的是什么？", "experiments"),
    ("这里的窗口指的是什么？", "definition"),
])
def test_natural_reading_intent(question, expected):
    assert reading_query(question)[0] == expected


@pytest.mark.parametrize("have_source", [True, False])
def test_local_supplement_only_once_and_reports_missing(have_source):
    paper = Paper(paper_id="article", title="An article", metadata={"chunk_origin": "body"})
    bodies = [
        "We encode the input. We then use the second stage and return the resulting list. "
        "We construct these as positive pairs.",
        "The candidate generation uses an encoder and a first stage. We produce the output.",
        "We then rerank the result by inner product during retrieval.",
    ]
    if have_source:
        bodies += ["In-batch negatives come from other observations in the batch."]
    chunks = [PaperChunk(chunk_id=f"window-{i}", paper_id="article", chunk_index=i, page=i+1, text=text)
              for i, text in enumerate(bodies)]
    data = provided_paper(paper, chunks, text="正例怎样构造，负样本又从哪里来？")
    trace = data["selection_trace"]
    assert trace["missing_before"] == ["负样本来源"]
    assert len(data["excerpts"]) <= 3
    assert len(trace["supplement"]) == int(have_source)
    assert trace["missing_after"] == ([] if have_source else ["负样本来源"])
    assert any("In-batch negatives" in e["text"] for e in data["excerpts"]) == have_source
    if not have_source:
        assert "未定位到负样本来源" in data["selection_note"]


@pytest.mark.parametrize("followup", ["接下来呢？", "这是什么意思？"])
def test_short_followup_and_explicit_change_keep_only_current_paper(tmp_path, followup):
    with sample(tmp_path) as state:
        source = seed(state)
        state["control"]["result"] = explain
        cid = create(state)
        expect(send(state, cid, "只看这篇论文，输入怎样转换得到分数？", rid="natural",
                    selected_paper_ids=[source]))
        expect(send(state, cid, followup, rid="continued", selected_paper_ids=[source]))
        previous = state["calls"][-1]["context"]
        assert previous["reading_selection"]["intent"] == "method"
        assert previous["reading_question"].startswith("只看这篇")
        assert any(METHOD in e["text"] for e in previous["papers"][0]["excerpts"])
        result = expect(send(state, cid, "再简单一点，改读《Birch Caption Retrieval》的方法。", rid="switched",
                             selected_paper_ids=[source]))
        packet = state["calls"][-1]["context"]
        assert [p["paper_id"] for p in packet["papers"]] == ["birch"]
        assert [(c["paper_id"], c["quote"]) for c in result["messages"][-1]["reading_citations"]] == [("birch", COMPARE)]


def evidence(scope="current"):
    p = {"paper_id": "source", "title": "Mixed representations", "abstract": "", "excerpts": [
        {"excerpt_id": "chapter:0:93", "page": 3,
         "text": "The binary code of input ha and that of each item, hb. We rank with the inner product "
                 "between the continuous embedding of input ea and hb."}
    ]}
    p = numbered_papers([p], {"project_id": scope})[0]
    return {"source": p}, p["excerpts"][0]["evidence_id"]


def fact(ref, subject="hb", value="段落二值码", status="explicit"):
    return {"subject": subject, "value": value, "status": status, "evidence_ids": [ref]}


def test_mixed_representation_and_real_missing_implementation():
    papers, ref = evidence()
    draft = MethodReadingDraft(reply="连续向量 ea 与二值码 hb 做内积。浮点实现细节未知。", method_facts=[
        fact(ref), fact(ref, "ea", "连续表示"), fact(ref, "实现精度", "未写明", "missing"),
    ])
    result = list(method_analysis(draft, papers))
    assert [row["status"] for row in result] == ["explicit", "explicit", "missing"]
    assert all(r["sources"][0]["page"] == 3 for r in result)


@pytest.mark.parametrize("failure", ["invented", "unknown", "repeated", "reply_reversal", "old_reference"])
def test_fact_and_reply_inconsistencies_fail_closed(failure):
    papers, ref = evidence()
    facts = [fact(ref)]
    reply = "连续向量 ea 与二值码 hb 做内积。"
    if failure == "invented":
        facts = [fact(ref, value="连续向量")]
    elif failure == "unknown":
        facts = [fact(ref, value="未知", status="missing")]
    elif failure == "repeated":
        facts += [fact(ref, subject="另一步的定义", value="连续向量") | {"symbol": "hb", "kind": "definition"}]
    elif failure == "reply_reversal":
        reply += "片段不足以确认 hb 是哪种表示。"
    else:
        _, obsolete = evidence("other-project-or-revision")
        facts = [fact(obsolete)]
    with pytest.raises(WorkspaceError):
        list(method_analysis(MethodReadingDraft(reply=reply, method_facts=facts), papers))


@pytest.mark.parametrize("op,question,expected", [
    ("percentage_points", "准确率差多少", "-3.8 个百分点"),
    ("relative_percent", "相对变化", "-7.1%"),
    ("difference", "差多少", None),
])
def test_table_arithmetic_uses_verified_cells_and_separates_units(op, question, expected):
    source = SimpleNamespace(quote="Method | Accuracy | Other\nNew | 49.7% | 82%\nOld | 53.5% | 89%\nTable 3: Test results.")
    selection = TableComparison(evidence_id="ev", left_row="New", right_row="Old", column=1, operation=op)
    result = table_calculation(source, selection, question)
    assert result is None if expected is None else expected in result


@pytest.mark.parametrize("quote,question,column", [
    ("Data Method Score Other\nA New 50 80\nB Old 53 90\nTable 1: Accuracy", "", 1),
    ("Method Top-1 Top-20\nNQ NQ\nNew 49 60\nOld 50 58\nTable 2: Accuracy", "只看 Top-20", 1),
    ("Method Score Runtime\nNew 49% 10ms\nOld 53% 1s\nTable 1: Test", "", 2),
    ("Method Score Runtime\nNew 49% 10ms\nNew 51% 10ms\nOld 53% 2ms\nTable 1: Test", "", 1),
])
def test_table_refuses_mixed_conditions_ragged_header_or_ambiguous_rows(quote, question, column):
    source = SimpleNamespace(quote=quote)
    selection = TableComparison(evidence_id="ev", left_row="New", right_row="Old", column=column,
                                operation="difference")
    assert table_calculation(source, selection, question) is None


@pytest.mark.parametrize("symbol_field", [None, "hb"])
@pytest.mark.parametrize("problem", ["wrong_type", "unknown", "reply"])
def test_symbol_checks_work_with_human_titles(symbol_field, problem):
    papers, ref = evidence()
    value, status = {"wrong_type": ("hb 是连续表示", "explicit"),
                     "unknown": ("未核对", "missing"), "reply": ("hb 是二值码", "explicit")}[problem]
    text = "正文清楚说明采用内积。"
    if problem == "reply":
        text += "结尾却无法确定 hb 是哪种表示。"
    with pytest.raises(WorkspaceError):
        list(method_analysis(MethodReadingDraft(reply=text, method_facts=[
            fact(ref, "重排阶段的段落表示 hb", value, status) | {"symbol": symbol_field, "kind": "definition"},
        ]), papers))


def test_symbol_uses_can_repeat_and_implementation_can_be_unknown():
    papers, ref = evidence()
    draft = MethodReadingDraft(reply="hb 是二值码。实现中 hb 的浮点精度尚不明确。", method_facts=[
        fact(ref, "对象定义", "hb 是二值码") | {"symbol": "hb", "kind": "definition"},
        fact(ref, "候选", "比较两个二值码 hb 与 ha") | {"symbol": "hb", "kind": "use"},
        fact(ref, "重排", "用连续向量 ea 与二值码 hb 计算内积") | {"symbol": "hb", "kind": "use"},
        fact(ref, "实现精度", "未写明", "missing"),
    ])
    report = list(method_analysis(draft, papers))
    assert [x["symbol_binding"]["identity"] for x in report] == ["hb", "hb", "hb", None]
    assert report[2]["symbol_binding"]["coverage"] == "source_type_found_no_comparable_fact_value"
    assert report[3]["symbol_binding"]["coverage"] == "no_symbol_identified"


def test_distinct_subscripts_and_scoped_or_missing_definitions():
    paper = {"paper_id": "source", "title": "Distinct symbols", "abstract": "", "excerpts": [
        {"excerpt_id": "a", "page": 2, "text": "hp is a binary code. hₚ is a continuous vector. h_2 is binary. z is used."},
        {"excerpt_id": "b", "page": 4, "text": "h_2 is continuous."},
    ]}
    paper = numbered_papers([paper], {"project_id": "test"})[0]
    a, b = [e["evidence_id"] for e in paper["excerpts"]]
    draft = MethodReadingDraft(reply="已区分两处表示；精度仍待核对。", method_facts=[
        fact(a, "紧排变量", "二值码") | {"symbol": "hp"},
        fact(a, "下标变量", "连续向量") | {"symbol": "h_{p}"},
        fact(a, "精度", "原文未定义", "missing") | {"symbol": "z"},
        fact(a, "第二个下标", "两处表示不一致", "conflict") | {"symbol": "h_2", "evidence_ids": [a, b]},
    ])
    result = [r["symbol_binding"] for r in method_analysis(draft, {"source": paper})]
    assert result[0]["identity"] == "hp" and result[1]["identity"] == "h_p"
    assert result[2]["coverage"] == "no_recognized_type_definition"
    assert result[3]["coverage"] == "conflicting_or_scoped_source_definitions"


@pytest.mark.parametrize("valid", [True, False])
def test_final_table_reply_does_not_keep_unverified_delta(valid):
    paper = {"paper_id": "source", "title": "Table", "abstract": "", "excerpts": [
        {"excerpt_id": "a", "page": 2,
         "text": "Method | Accuracy | Other\nNew | 49.7% | 82%\nOld | 53.5% | 89%\nTable 3: Test results."},
    ]}
    paper = numbered_papers([paper], {"project_id": "test"})[0]
    ref = paper["excerpts"][0]["evidence_id"]
    draft = TableReadingDraft(reply="原表数值为 49.7% 对 53.5%，低 5.8 个百分点。实验条件仍需核对。",
        table_comparisons=[{"evidence_id": ref if valid else "unavailable", "left_row": "New", "right_row": "Old",
                            "column": 1, "operation": "percentage_points"}])
    review = []
    final = apply_table_calculations(draft, {"source": paper}, "", review)
    assert "5.8" not in final.reply and "49.7% 对 53.5%" in final.reply and "实验条件仍需核对" in final.reply
    assert ("-3.8 个百分点" in final.reply) == valid
    assert ("未计算" in final.reply) != valid
    assert bool(final.reading_citations) == valid


@pytest.mark.parametrize("question,column,expected", [
    ("表 8 中 North 的 Top-1", 1, "-3.8 个百分点"),
    ("表 8 中 North 的 Top-1", 2, None),
    ("表 9 中 North 的 Top-1", 1, None),
    ("North 的 Top-1", 1, None),
])
def test_named_first_table_and_actual_dataset_columns(question, column, expected):
    source = SimpleNamespace(quote="256\nMethod Top-1 Top-20\nNorth South North South\n"
                                   "New 49.7 35 80 70\nOld 53.5 30 84 72\n"
                                   "Table 8: Top-k recall.\nOther table rows\nTable 9: Latency.")
    selection = TableComparison(evidence_id="ev", left_row="New", right_row="Old", column=column,
                                operation="percentage_points")
    result = table_calculation(source, selection, question)
    assert result is None if expected is None else expected in result
