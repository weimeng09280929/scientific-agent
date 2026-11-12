import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.knowledge.schema import PaperChunk
from core.workspace import guide
from core.workspace.method_evidence import AspectReadingDraft, aspect_analysis, declared_symbols, method_objects
from core.workspace.paper_reading import numbered_papers, provided_paper
from core.workspace.service import WorkspaceError
from tests.local_backup_fixture import expect
from tests.question_paper_reading_fixture import seed
from tests.research_partner_fixture import create, sample, send


def excerpt(text, index=1):
    return {"text": text, "excerpt_id": f"c{index}:0:{len(text)}", "chunk_id": f"c{index}",
            "chunk_index": index, "start": 0, "end": len(text), "page": index}


def deliver(*sources, scope="method-test"):
    papers = numbered_papers(list(sources), {"request_id": scope})
    return {"papers": papers, "method_objects": method_objects(papers, {"request_id": scope}),
            "reading_targets": [{"id": "representations"}]}


def source(pid, *texts):
    return {"paper_id": pid, "title": pid, "abstract": "",
            "excerpts": [excerpt(text, i) for i, text in enumerate(texts, 1)]}


def claim(obj, ref, **values):
    return {"claim_id": "c1", "subject_id": obj["object_id"], "aspect": "type", "status": "explicit",
            "type_value": "binary", "value": "x 是二值码。", "evidence_ids": [ref], **values}


def verify(delivered, reply, *claims):
    return list(aspect_analysis(AspectReadingDraft(reply=reply, method_claims=list(claims)), delivered))


def test_declarations_capture_pdf_offsets_and_exclude_prose():
    text = ("The binary code of the question and (2) scoring. It is a binary code. "
            "The binary code of each object. If r is a binary code, use a different step. "
            "Where X_{2} is a bi-\nnary code. "
            "The continuous embedding (xₚ) is used for eﬃcient ranking.")
    found = declared_symbols(text)
    assert [(d.symbol, d.type) for d in found] == [("X_{2}", "binary"), ("xₚ", "continuous")]
    assert all(text[d.symbol_start:d.symbol_end] == d.symbol for d in found)
    assert text[found[0].start:found[0].end] == "Where X_{2} is a bi-\nnary code"
    assert text[found[1].start:found[1].end] == "continuous embedding (xₚ)"
    ligature = "This paper uses eﬃcient retrieval. Where Q is a continuous vector."
    d, = declared_symbols(ligature)
    assert ligature[d.start:d.end] == "Where Q is a continuous vector"


def test_distinct_scopes_and_papers_do_not_create_a_conflict():
    stages = "Stage 1: Encoding\nWhere x is a binary code.\nStage 2: Decoding\nWhere x is a continuous vector."
    delivered = deliver(source("p", stages, "An inner product uses x and returns a score."),
                        source("q", "Where x is a binary code."))
    objects = [o for o in delivered["method_objects"] if o["symbol"] == "x"]
    assert len({o["object_id"] for o in objects}) == 3
    assert [o["scope"]["heading"] for o in objects if o["paper_id"] == "p"] == [
        "Stage 1: Encoding", "Stage 2: Decoding",
    ]
    a, b, c = objects
    def evidence(o):
        return o["definition_evidence_ids"][0]

    verify(delivered, "两个阶段使用各自的表示。",
           claim(a, evidence(a)), claim(b, evidence(b), claim_id="c2", type_value="continuous", value="x 是连续向量。"))
    with pytest.raises(WorkspaceError, match="不相容声明"):
        verify(delivered, "类型有冲突。", claim(a, evidence(a), status="conflict", type_value=None, value="类型冲突。"))
    with pytest.raises(WorkspaceError, match="编号与提交的来源"):
        verify(delivered, "已核对。", claim(a, evidence(c)))
    usage = delivered["papers"][0]["excerpts"][1]["evidence_id"]
    with pytest.raises(WorkspaceError, match="无法确认其作用域"):
        verify(delivered, "使用内积。", claim(a, usage, aspect="operation", type_value=None,
                                            value="使用内积。", participant_ids=[a["object_id"]]))


def test_conflict_requires_two_explicit_incompatible_declarations_of_same_object():
    text = ("Stage 1: Encoding\nWhere x is a binary code. "
            "In the current stage, the same x is instead a continuous vector.")
    delivered = deliver(source("p", text))
    obj, = [o for o in delivered["method_objects"] if o["symbol"]]
    ref, = obj["definition_evidence_ids"]
    assert len(obj["declarations"]) == 2
    assert verify(delivered, "同一作用域下两处定义冲突，需核对。",
                  claim(obj, ref, status="conflict", type_value=None, value="两处定义冲突。"))[0][
                      "mechanical_coverage"] == "type_declaration_checked"


def test_scope_limited_missing_and_symbol_free_motivation_are_not_semantic_certificates():
    delivered = deliver(source("p", "The authors introduce a filter to reduce the index size. No tests are reported in this excerpt."))
    obj, = delivered["method_objects"]
    ref = delivered["papers"][0]["excerpts"][0]["evidence_id"]
    unknown = claim(obj, ref, status="missing", type_value=None, value="本轮材料未说明输入表示类型。")
    motivation = claim(obj, ref, claim_id="c2", aspect="motivation", type_value=None, value="作者想缩小索引。")
    assert all(row["mechanical_coverage"] == "not_mechanically_verified" for row in verify(
        delivered, "作者希望缩小索引。当前材料未说明输入表示类型。", unknown, motivation))
    with pytest.raises(WorkspaceError, match="限定"):
        verify(delivered, "整篇论文没有给出输入类型。", unknown)
    with pytest.raises(WorkspaceError, match="性能因果"):
        verify(delivered, "作者要缩小索引，由此证明一定提升准确率。", motivation)


def test_direct_type_predicates_preserve_case_subscripts_and_negation():
    delivered = deliver(source("p", "Where x_{2} is a binary code. Where X₂ is a continuous vector."))
    obj = next(o for o in delivered["method_objects"] if o["symbol"] == "x_{2}")
    ref, = obj["definition_evidence_ids"]
    operation = claim(obj, ref, aspect="operation", type_value=None,
                      value="x₂ 与连续查询 X_2 做内积。")
    verify(delivered, "x₂ 不是连续向量。", operation)
    negative = {**operation, "value": "x₂ 不是二值码。"}
    with pytest.raises(WorkspaceError, match="方法事实对类型"):
        verify(delivered, "x₂ 是二值码。", negative)
    with pytest.raises(WorkspaceError, match="正文对类型"):
        verify(delivered, "x₂ 是连续向量而不是二值码。", operation)
    wrong_other_operand = {**operation, "value": "X_2 是二值码。"}
    with pytest.raises(WorkspaceError, match="方法事实对类型"):
        verify(delivered, "x₂ 是二值码。", wrong_other_operand)


def test_direct_type_predicates_respect_paper_and_declaration_scope():
    delivered = deliver(source("p", "Stage 1: Encoding\nWhere x is a binary code.",
                              "Stage 2: Ranking\nWhere x is a continuous vector."),
                        source("q", "The second stage sorts the candidates."))
    first, second = [o for o in delivered["method_objects"] if o["symbol"] == "x"]
    first_ref, = first["definition_evidence_ids"]
    second_ref, = second["definition_evidence_ids"]
    detail = claim(second, second_ref, aspect="implementation_detail", type_value=None, value="x 是连续向量。")
    verify(delivered, "第二阶段的 x 是连续向量。", detail)
    with pytest.raises(WorkspaceError, match="同名对象"):
        verify(delivered, "第二阶段的 x 是连续向量。", {**detail, "evidence_ids": [first_ref]})
    other = next(o for o in delivered["method_objects"] if o["paper_id"] == "q")
    other_ref = delivered["papers"][1]["excerpts"][0]["evidence_id"]
    with pytest.raises(WorkspaceError, match="须绑定对应对象"):
        verify(delivered, "第二阶段对候选排序。",
               claim(other, other_ref, aspect="implementation_detail", type_value=None, value="x 是连续向量。"))


def test_page_boundary_adds_only_the_adjacent_purpose_prefix():
    paper = SimpleNamespace(paper_id="p", title="Filtering", abstract="", source_stage="upload",
                            metadata={"chunk_origin": "body"})
    chunks = [SimpleNamespace(chunk_index=i, chunk_id=f"c{i}", page=i, text=text)
              for i, text in enumerate([
                  "Other content ends here. We chose this design to reduce the size of the\n1Footnote contents.",
                  "102\npassage index by storing it as binary codes. The method computes an inner product.",
              ], 1)]
    args = dict(text="重排为什么采用这种表示？", candidate=True)
    before = provided_paper(paper, chunks, **args)
    after = provided_paper(paper, chunks, **args, complete_motivation=True)
    front, back = after["selection_trace"]["motivation_boundary"]
    added = next(e for e in after["excerpts"] if e["excerpt_id"] == front["excerpt_id"])
    assert added["text"] == "We chose this design to reduce the size of the"
    assert back["page"] == 2 and added["page"] == 1
    assert {e["excerpt_id"] for e in before["excerpts"]} <= {e["excerpt_id"] for e in after["excerpts"]}


def test_aspect_contract_matches_wire_shape():
    from pydantic import ValidationError

    schema = AspectReadingDraft.model_json_schema()
    assert set(schema["properties"]) == set(AspectReadingDraft.model_fields)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["MethodCitation"]["required"] == ["evidence_id"]
    with pytest.raises(ValidationError):
        AspectReadingDraft(reply="说明", method_claims=[], reading_citations=[{"quote": "text"}])


def test_aspect_generator_saves_only_consistent_current_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(guide, "METHOD_ASPECTS", True)
    monkeypatch.setattr(guide, "READING_CANDIDATE", True)
    with sample(tmp_path) as state:
        seed(state)
        cid = create(state)
        body = ("3.1 Ranking\nWhere x is a binary code. Where Q is a continuous vector. "
                "The first step selects candidates. In the second step Q and x form an inner product.")
        q = "只看这篇论文，第二步怎样重排，段落用什么表示？有交代存储精度吗？"

        def install(text):
            state["store"].save_paper_chunks([PaperChunk(
                paper_id="birch", chunk_id="birch-method", chunk_index=0, text=text, page=3,
            )])

        install(body)
        old_ids = {}
        variant = "valid"

        def answer(ctx, _text):
            x, = [o for o in ctx["method_objects"] if o["symbol"] == "x"]
            query, = [o for o in ctx["method_objects"] if o["symbol"] == "Q"]
            ref, = x["definition_evidence_ids"]
            e = next(e for e in ctx["papers"][0]["excerpts"] if e["evidence_id"] == ref)
            declaration, = x["declarations"]
            assert e["text"][declaration["quote_start"]:declaration["quote_end"]] == "Where x is a binary code"
            reply = "x 是二值码。重排时 Q 与 x 做内积。本轮材料未说明存储精度。"
            typed = claim(x, ref, point_ids=["representations"])
            detail = claim(x, ref, claim_id="c2", aspect="implementation_detail", status="missing",
                           type_value=None, value="本轮材料未说明 x 的存储精度。")
            operation = claim(x, ref, claim_id="c3", aspect="operation", type_value=None,
                              value="Q 与 x 计算内积。", participant_ids=[x["object_id"], query["object_id"]])
            if variant == "valid":
                old_ids.update(object_id=x["object_id"], ref=ref)
            elif variant == "wrong_fact":
                typed.update(status="missing", type_value=None, value="本轮材料未说明 x 的类型。")
            elif variant == "wrong_reply":
                reply = "x 是连续向量。"
            elif variant == "negated_reply":
                reply = "x 不是二值码。"
            elif variant == "wrong_operation":
                reply = "x 是二值码。重排时 Q 与 x 做余弦相似度。"
            elif variant == "wrong_detail":
                detail.update(value="本轮材料没有说明 x 是否另有连续嵌入的定义或维度。")
            elif variant == "old_ref":
                typed.update(evidence_ids=[old_ids["ref"]])
            elif variant == "old_object":
                typed.update(subject_id=old_ids["object_id"])
            elif variant == "unknown_point":
                typed.update(point_ids=["nonexistent"])
            return AspectReadingDraft(reply=reply, method_claims=[typed, detail, operation],
                                      reading_citations=[{"evidence_id": ref}])

        state["control"]["result"] = answer
        url = f"/api/projects/{state['pid']}/conversations/{cid}"
        for variant in ("valid", "wrong_fact", "wrong_reply", "negated_reply", "wrong_operation", "wrong_detail",
                        "old_ref", "old_object", "unknown_point"):
            if variant == "old_ref":
                install(body + " The candidates are limited to the local index.")
            count = len(state["calls"])
            send(state, cid, q, rid=variant, selected_paper_ids=["birch"], reading_purpose="method")
            result = expect(state["a"].get(url + "/requests/" + variant))
            assert len(state["calls"]) == count + 1
            assert state["calls"][-1]["automatic_retry"] is False
            replies = [m for m in result.get("messages", []) if m["role"] == "assistant"]
            if variant == "valid":
                assert result["status"] == "completed"
                assert replies[0]["reading_citations"][0]["evidence_id"] == old_ids["ref"]
                assert replies[0]["reading_materials"][0]["excerpts"][0]["text"] == body
            else:
                assert result["status"] == "failed", (variant, result)
                assert not replies


def test_bpr_type_bypasses_fail_before_normal_assistant_save(tmp_path, monkeypatch):
    monkeypatch.setattr(guide, "METHOD_ASPECTS", True)
    monkeypatch.setattr(guide, "READING_CANDIDATE", True)
    fixture = Path(__file__).resolve().parents[1] / "docs/method-evidence-prototype/bpr-followup-B.json"
    if not fixture.is_file():
        pytest.skip("historical acceptance receipts are kept in the local validation archive")
    body = json.loads(fixture.read_bytes())
    frozen = json.JSONDecoder().raw_decode(body["messages"][-1]["content"].split("\n", 1)[1])[0]
    excerpt = next(e for e in frozen["papers"][0]["excerpts"] if e["page"] == 3)
    question = "只看这篇论文。重排如何打分，段落是什么表示？这段材料有交代存储精度吗？"

    with sample(tmp_path) as state:
        seed(state)
        cid = create(state)
        state["store"].save_paper_chunks([PaperChunk(
            paper_id="birch", chunk_id="birch-method", chunk_index=0, page=3, text=excerpt["text"],
        )])
        source_before = state["store"].get_paper_chunks("birch")
        url = f"/api/projects/{state['pid']}/conversations/{cid}"

        def answer(ctx, _text):
            hp = next(o for o in ctx["method_objects"] if o["symbol"] == "hp")
            eq = next(o for o in ctx["method_objects"] if o["symbol"] == "eq")
            material = next(o for o in ctx["method_objects"] if o["scope"]["kind"] == "method_material")
            ref, = hp["definition_evidence_ids"]
            assert any(e["text"] == excerpt["text"] and e["evidence_id"] == ref
                       for e in ctx["papers"][0]["excerpts"])
            reply = "hp 是二值码。"
            if variant == "material_type":
                facts = [claim(material, ref, type_value="continuous", value="hp 是连续向量。")]
            elif variant == "wrong_aspect":
                facts = [claim(hp, ref, aspect="implementation_detail", type_value=None, value="hp 是连续向量。")]
            elif variant == "symbol_free":
                reply = "重排后只从候选中返回前 k 段。"
                facts = [claim(material, ref, aspect="output_scope", type_value=None, value=reply)]
            else:
                reply += "hp 与连续查询 eq 做内积。本轮材料未说明 hp 的存储精度。"
                facts = [
                    claim(hp, ref, value="hp 是二值码。"),
                    claim(hp, ref, claim_id="c2", aspect="operation", type_value=None,
                          value="hp 与连续查询 eq 做内积。", participant_ids=[hp["object_id"], eq["object_id"]]),
                    claim(hp, ref, claim_id="c3", aspect="implementation_detail", status="missing", type_value=None,
                          value="本轮材料未说明 hp 的存储精度。"),
                ]
            return AspectReadingDraft(reply=reply, method_claims=facts, reading_citations=[{"evidence_id": ref}])

        state["control"]["result"] = answer
        for variant, error in (
            ("material_type", "明确符号的类型断言须绑定对应对象"),
            ("wrong_aspect", "方法事实对类型的说明与本轮原文声明不一致"),
            ("symbol_free", None),
            ("valid", None),
        ):
            count = len(state["calls"])
            send(state, cid, question, rid=variant, selected_paper_ids=["birch"], reading_purpose="method")
            result = expect(state["a"].get(url + "/requests/" + variant))
            assert len(state["calls"]) == count + 1
            assert state["calls"][-1]["automatic_retry"] is False
            assert any(m["role"] == "user" and m["content"] == question for m in result["messages"])
            assert state["store"].get_paper_chunks("birch") == source_before
            assistant = [m for m in result["messages"] if m["role"] == "assistant"]
            if error:
                assert result["status"] == "failed" and not assistant
                assert error in result["error"]
            else:
                assert result["status"] == "completed" and len(assistant) == 1
                assert assistant[0]["reading_citations"][0]["page"] == 3
