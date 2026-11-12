from types import SimpleNamespace

import pytest

from core.workspace import guide
from core.workspace.paper_reading import provided_paper, reading_query
from core.workspace.reading_analysis import MethodReadingDraft
from tests.local_backup_fixture import expect
from tests.question_paper_reading_fixture import seed
from tests.research_partner_fixture import create, sample, send


@pytest.mark.parametrize("text", [
    "现成模型拿来后，先用哪批文字继续练，再用哪批？都需要标注吗？",
    "前一步和后一步各自喂什么材料？两次有没有人工标签？",
    "What data does each stage train on and are labels required?",
    "它具体怎么跑起来的？",
])
def test_natural_procedure_questions(text):
    assert reading_query(text)[0] == "method"


def test_procedural_cues_do_not_override_explicit_experiment():
    assert reading_query("表 2 对比有标签和无标签的数据，哪个结果更好？")[0] == "experiments"


def test_candidate_prefers_direct_definitions_and_stage_order():
    paper = SimpleNamespace(paper_id="p", title="Synthetic alignment procedure", abstract="First overview.",
                            source_stage="upload", metadata={"chunk_origin": "body"})
    bodies = [
        "2 Background\nFirst, generic models are trained on a large corpus, then reused in supervised training.",
        "3 Domain Training\nWe continue training the encoder on unlabeled domain-specific text.",
        "4 Task Training\nTask fitting refers to pretraining on the unlabeled training set for a given task.",
        "5 Combined Approach\nWe begin with the baseline and apply domain fitting then task fitting.",
        "8 Results\nAn experiment compares classifier accuracy across twelve configurations.",
    ]
    chunks = [SimpleNamespace(chunk_index=i, chunk_id=f"c{i}", page=i+1, text=text) for i,text in enumerate(bodies)]
    result = provided_paper(paper, chunks, text="先拿哪批数据继续练，再拿哪些数据？需要标注吗？", candidate=True)
    assert {e["page"] for e in result["excerpts"]} == {2, 3, 4}
    assert result["abstract"] == "" and not result["selection_trace"]["missing_after"]
    assert {p["id"] for p in result["question_coverage"]} == {"order", "inputs", "labels"}


def test_candidate_reports_uncovered_motivation():
    paper = SimpleNamespace(paper_id="p", title="Synthetic filtering", abstract="", source_stage="upload",
                            metadata={"chunk_origin": "body"})
    chunk = SimpleNamespace(chunk_index=1, chunk_id="m1", page=3,
                            text="The model uses a continuous embedding and computes an inner product.")
    result = provided_paper(paper, [chunk], text="重排为什么采用这种表示？", candidate=True)
    assert result["selection_trace"]["missing_after"] == ["motivation"]
    assert "作者为什么做这个设计" in result["selection_note"]


def test_candidate_profile_reaches_real_guide_only_in_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(guide, "READING_CANDIDATE", True)
    with sample(tmp_path) as state:
        paper = seed(state)
        cid = create(state)
        def answer(ctx, text):
            if not ctx.get("reading_targets"):
                from core.workspace.schema import GuideDraft
                return GuideDraft(reply="继续聊聊你的问题。")
            excerpt = next(e for e in ctx["papers"][0]["excerpts"] if e["page"] == 4)
            ref = excerpt["evidence_id"]
            return MethodReadingDraft(reply="输入包括表格与说明，输出排序结果。",
                reading_citations=[{"evidence_id": ref}], method_facts=[{
                    "subject": "模型输入", "status": "explicit", "value": "表格与说明", "evidence_ids": [ref],
                    "point_ids": ["inputs"]}])
        state["control"]["result"] = answer
        response = expect(send(state,cid,"只看这篇论文，模型用什么输入，最后返回什么？",
                               selected_paper_ids=[paper],rid="candidate-reading"))
        assert state["calls"][-1]["automatic_retry"] is False
        packet = state["calls"][-1]["context"]
        assert {t["id"] for t in packet["reading_targets"]} == {"inputs","output"}
        assert response["messages"][-1]["reading_citations"][0]["page"] == 4
        expect(send(state,cid,"我想聊聊怎么安排学习时间。",rid="ordinary-chat"))
        assert not state["calls"][-1]["context"].get("reading_targets")


def test_explicit_source_definition_has_a_locatable_number():
    from core.workspace.reading_analysis import delivered_symbol_definitions
    papers = [{"excerpts":[{"text": "The binary code of query aq and that of each item, ap. "
                                     "We compute an inner product between the continuous embedding of query eq and ap.",
                             "evidence_id":"ev-original"}]}]
    assert delivered_symbol_definitions(papers) == [
        {"symbol": "aq", "type": "binary", "evidence_id": "ev-original"},
        {"symbol": "ap", "type": "binary", "evidence_id": "ev-original"},
        {"symbol": "eq", "type": "continuous", "evidence_id": "ev-original"},
    ]


def test_single_declarations_keep_separate_sources_and_ignore_plain_descriptions():
    from core.workspace.reading_analysis import delivered_symbol_definitions

    papers = [
        {"excerpts": [{"text": "Where z is a binary code. It is binary. It is stored on disk.",
                       "evidence_id": "ev-a"}]},
        {"excerpts": [{"text": "We call the continuous embedding (z) the query feature.",
                       "evidence_id": "ev-b"}]},
    ]
    assert delivered_symbol_definitions(papers) == [
        {"symbol": "z", "type": "binary", "evidence_id": "ev-a"},
        {"symbol": "z", "type": "continuous", "evidence_id": "ev-b"},
    ]


def test_delivered_type_hint_uses_the_current_request_evidence_id(tmp_path, monkeypatch):
    from core.knowledge.schema import PaperChunk

    monkeypatch.setattr(guide, "READING_CANDIDATE", True)
    with sample(tmp_path) as state:
        seed(state)
        state["store"].save_paper_chunks([PaperChunk(
            paper_id="birch", chunk_id="birch-method", chunk_index=0, page=3,
            text="Methods\nThe encoder represents the input with a binary code (z).",
        )])
        cid = create(state)

        def answer(context, _text):
            excerpt = next(e for p in context["papers"] for e in p["excerpts"] if "binary code (z)" in e["text"])
            evidence_id = excerpt["evidence_id"]
            assert context["source_symbol_definitions"] == [
                {"symbol": "z", "type": "binary", "evidence_id": evidence_id},
            ]
            return MethodReadingDraft(reply="输入用二值码 z 表示。", reading_citations=[{"evidence_id": evidence_id}],
                                      method_facts=[{"subject": "输入表示", "symbol": "z", "kind": "definition",
                                                     "status": "explicit", "value": "z 是二值码",
                                                     "evidence_ids": [evidence_id]}])

        state["control"]["result"] = answer
        for rid in ("first-definition", "second-definition"):
            expect(send(state, cid, "只看这篇论文，输入是什么，作者用什么表示？",
                        rid=rid, selected_paper_ids=["birch"], reading_purpose="method"))
        refs = [call["context"]["source_symbol_definitions"][0]["evidence_id"] for call in state["calls"]]
        assert len(set(refs)) == 2


@pytest.mark.parametrize("q", [
    "训练时，遮住（ABC）和配对（DEF）分别让模型猜什么？答案从哪里来？",
    "遮盖(ABC)和配对(DEF)分别预测什么？",
    "ABC 和 DEF 分别预测什么？",
    "遮住的词和两句话分别猜什么？",
])
def test_answer_targets_prefer_objective_text_over_ablation(q):
    assert reading_query(q)[0] == "method"
    paper = SimpleNamespace(paper_id="p", title="Objectives", abstract="", source_stage="upload",
                            metadata={"chunk_origin":"body"})
    bodies = [
        "6 Ablation\nABC and DEF were disabled in one experiment. The baseline scores were higher.",
        "3 Pretraining Method\nTask ABC predicts the original token from corrupted input.",
        "3.1 Pretraining Tasks\nFor DEF, actual sentence pairs and random sentence pairs create the labels.",
        "5 Related Work\nA published technique also uses training tasks for various objectives.",
        "References\nABC predicts the original token. DEF predicts whether sentences are consecutive.",
    ]
    chunks = [SimpleNamespace(chunk_index=i, chunk_id=f"p{i}", page=i+1, text=text) for i,text in enumerate(bodies)]
    out = provided_paper(paper,chunks,text=q,candidate=True)
    assert {x["page"] for x in out["excerpts"]} == {2, 3}
    assert not out["selection_trace"]["missing_after"]


@pytest.mark.parametrize("q", ["训练目标（ABC）和（DEF）分别预测什么？", "ABC 和 DEF 分别预测什么？"])
def test_adjacent_objective_heading_does_not_claim_previous_definition(q):
    from core.workspace.reading_coverage import asked_points, coverage_signals

    text = ("The network predicts the original token. We call this ABC. "
            "Task #2: Pair Validation (DEF). Results are reported elsewhere.")
    signals, _ = coverage_signals(asked_points(q), text, text, q)
    assert signals["term:ABC"] == 6
    assert signals["term:DEF"] == 1


@pytest.mark.parametrize("stale_checkbox", [False, True])
def test_short_method_followup_uses_failed_immediately_previous_user_question(tmp_path,monkeypatch,stale_checkbox):
    from core.workspace.schema import GuideDraft
    from core.workspace.service import WorkspaceError
    from tests.question_paper_reading_fixture import EXPERIMENT
    monkeypatch.setattr(guide,"READING_CANDIDATE",True)
    with sample(tmp_path) as state:
        paper=seed(state); cid=create(state)
        def answer(ctx,text):
            if "它怎样先输入" in text or text == "重排呢？":
                raise WorkspaceError("本题结构未通过")
            if text.startswith("只看这篇论文，实验"):
                excerpt=next(e for e in ctx["papers"][0]["excerpts"] if EXPERIMENT in e["text"])
                return GuideDraft(reply="比较用同一批问题。",reading_citations=[{"evidence_id":excerpt["evidence_id"]}])
            return GuideDraft(reply="这一阶段用原问题指定的材料。")
        state["control"]["result"]=answer
        expect(send(state,cid,"只看这篇论文，实验怎样比较？",selected_paper_ids=[paper],rid="old-table"))
        q = ("只看《Cedar Table Retrieval》，它怎样先输入、再输出？" if stale_checkbox
             else "只看这篇论文，它怎样先输入、再输出？")
        response=send(state,cid,q,selected_paper_ids=["birch" if stale_checkbox else paper],rid="failed-method")
        assert response.status_code in (200,422)
        stale = ["birch"] if stale_checkbox else [paper]
        response = send(state, cid, "重排呢？", selected_paper_ids=stale, rid="failed-followup")
        assert response.status_code in (200, 422)
        assert state["calls"][-1]["context"]["reading_question"] == q
        url = f"/api/projects/{state['pid']}/conversations/{cid}"
        assert expect(state["a"].get(url + "/requests/failed-followup"))["status"] == "failed"
        expect(state["a"].get(url + "/messages"))
        expect(send(state, cid, "第二步呢？", selected_paper_ids=stale, rid="followup"))
        ctx=state["calls"][-1]["context"]
        assert ctx["reading_question"] == q
        assert ctx["reading_selection"]["intent"] == "method"
        assert [p["paper_id"] for p in ctx["papers"]] == [paper]


@pytest.mark.parametrize("switch_by", ["title", "selection", "removed"])
def test_failed_method_followup_respects_current_scope(tmp_path, monkeypatch, switch_by):
    from core.workspace.schema import GuideDraft
    from core.workspace.service import WorkspaceError

    monkeypatch.setattr(guide, "READING_CANDIDATE", True)
    with sample(tmp_path) as state:
        paper = seed(state)
        other = state["store"].get_paper("birch")
        cid = create(state)
        first = "只看这篇论文，它怎样先输入、再输出？"

        def answer(ctx, text):
            if text == first:
                raise WorkspaceError("本题结构未通过")
            return GuideDraft(reply="请看当前论文的步骤。")

        state["control"]["result"] = answer
        send(state, cid, first, selected_paper_ids=[paper], rid="first")
        if switch_by == "removed":
            # Simulate a removed paper in this test's isolated project.
            import sqlite3
            with sqlite3.connect(state["api"]._CONFIG.paths.project_db(state["pid"])) as conn:
                conn.execute("DELETE FROM papers WHERE paper_id=?", (paper,))
            count = len(state["calls"])
            result = send(state, cid, "第二步呢？", rid="removed")
            assert result.status_code == 422
            assert len(state["calls"]) == count
            return
        if switch_by == "title":
            question, selection = f"换到《{other.title}》，它怎样先输入、再输出？", [paper]
        else:
            question, selection = "第二步呢？", ["birch"]
        expect(send(state, cid, question, selected_paper_ids=selection, rid="switch"))
        context = state["calls"][-1]["context"]
        assert [p["paper_id"] for p in context["papers"]] == ["birch"]
        assert context["reading_question"] == question
        assert context["reading_selection"]["intent"] == ("method" if switch_by == "title" else "terms")


def test_moved_failed_reading_cannot_recover_source_project_scope(tmp_path, monkeypatch):
    from core.workspace.schema import GuideDraft
    from core.workspace.service import WorkspaceError

    monkeypatch.setattr(guide, "READING_CANDIDATE", True)
    with sample(tmp_path) as state:
        seed(state)
        cid = create(state)
        first = "只看《Cedar Table Retrieval》，它怎样先输入、再输出？"

        def answer(ctx, text):
            if text == first:
                raise WorkspaceError("本题结构未通过")
            return GuideDraft(reply="请看当前项目的文献。")

        state["control"]["result"] = answer
        send(state, cid, first, rid="failed-original")
        client = state["a"]
        url = f"/api/projects/{state['pid']}/conversations/{cid}"
        preflight = expect(client.get(url + "/move-precheck", params={"target_project_id": state["target"]}))
        assert preflight["can_move"], preflight["reasons"]
        expect(client.post(url + "/move", json={
            "target_project_id": state["target"], "expected_token": preflight["token"],
            "request_id": "move", "acknowledge_history": True,
        }))
        target = {**state, "pid": state["target"]}
        question = "第二步呢？"
        expect(send(target, cid, question, rid="target-followup", selected_paper_ids=["other-project"]))
        context = state["calls"][-1]["context"]
        assert [p["paper_id"] for p in context["papers"]] == ["other-project"]
        assert context["reading_question"] == question
        assert context["reading_selection"]["intent"] == "terms"
