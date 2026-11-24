"""Exercise upload, prompt, quote validation and persisted ranges through real routes."""
import json
import os
from pathlib import Path

import pytest

from core.knowledge.schema import Paper, PaperChunk
from core.workspace.paper_reading import INPUT_CHARS, PAPER_TEXT_CHARS, provided_paper
from core.workspace.schema import GuideDraft
from tests.local_backup_fixture import expect
from tests.question_paper_reading_fixture import BIRCH_DOI, COMPARE, EXPERIMENT, LIMIT, METHOD, explain, seed, upload_pdf
from tests.research_partner_fixture import brief, create, sample, send, workspace


@pytest.fixture
def reading(tmp_path):
    with sample(tmp_path) as s:
        s["pdf"] = seed(s)
        s["control"]["result"] = explain
        yield s


def trace(s):
    return s["calls"][-1]["context"]


def base(s,cid):
    return f'/api/projects/{s["pid"]}/conversations/{cid}'


def test_actual_pdf_upload_and_topic_specific_late_pages(reading):
    s=reading
    chunks=s["store"].get_paper_chunks(s["pdf"])
    assert {p.page for p in chunks}==set(range(1,7))
    assert not any(METHOD in c.text for c in chunks[:2])
    assert s["store"].get_paper(s["pdf"]).metadata["text_origin"]=="body_opening"
    cid=create(s)
    receipts=[]
    for i,(question,quote,page) in enumerate([
        ("这篇论文的方法怎样工作？",METHOD,4),("这篇论文的实验怎样比较？",EXPERIMENT,5),
        ("这篇论文有哪些局限？",LIMIT,6),
    ]):
        response=expect(send(s,cid,question,rid=f"read-{i}",selected_paper_ids=[s["pdf"]]))
        packet=trace(s)
        assert "【按问题阅读论文】" in s["calls"][-1]["system"]
        supplied=packet["papers"][0]["excerpts"]
        assert any(quote in e["text"] and e["page"]==page for e in supplied)
        assert not any(e["page"] in (1,2) for e in supplied)
        message=response["messages"][-1]
        assert message["reading_materials"]==packet["papers"]
        assert any(c["quote"]==quote and c["page"]==page for c in message["reading_citations"])
        assert message["reading_basis_changed"] is False
        assert packet["papers"][0]["preview_origin"]=="body_opening"
        receipts.append({"question":question,"paper_context":packet["papers"],"validated":message["reading_citations"]})
    assert s["state"]["source_calls"]==s["state"]["public_calls"]==0
    assert not workspace(s)["items"]
    if directory := os.environ.get("SRA_READING_EVIDENCE"):
        (Path(directory)/"pdf-reading-receipts.json").write_text(json.dumps({
            "engine":"pypdf fallback","stored_pages":[c.page for c in chunks],"turns":receipts,
        },ensure_ascii=False,indent=2),encoding="utf-8")


def test_comparison_single_focus_and_ownership(reading):
    s=reading
    cid=create(s)
    result=expect(send(s,cid,"比较这两篇论文的 method 输入。",selected_paper_ids=[s["pdf"],"birch"]))
    assert {c["quote"] for c in result["messages"][-1]["reading_citations"]}=={METHOD,COMPARE}
    assert {p["paper_id"] for p in trace(s)["papers"]}=={s["pdf"],"birch"}
    expect(send(s,cid,"只看这篇论文 Cedar Table Retrieval 的方法。",rid="one",selected_paper_ids=[s["pdf"],"birch"]))
    packet=trace(s)
    assert packet["reading_selection"]["single_paper"]
    assert [p["paper_id"] for p in packet["papers"]]==[s["pdf"]]
    assert [p["paper_id"] for p in packet["paper_inventory"]]==[s["pdf"]]
    assert not packet["history"] and "existing_system" not in packet
    assert send(s,cid,"只看这篇论文的方法",rid="ambiguous",selected_paper_ids=[s["pdf"],"birch"]).status_code==422
    assert send(s,cid,"解释论文方法",rid="wrong-project",selected_paper_ids=["other-project"]).status_code==422
    assert send(s,cid,"解释论文方法",rid="wrong-account",selected_paper_ids=["other-account"]).status_code==422
    assert s["b"].get(base(s,cid)+"/messages").status_code==404


@pytest.mark.parametrize("question,multiple", [
    ("只看这篇论文，Birch Caption Retrieval 的方法是什么？", False),
    ("只读《Birch Caption Retrieval》的方法。", False),
    ("只看 DOI: https://doi.org/" + BIRCH_DOI.upper() + "，它的方法是什么？", False),
    ("只看这篇论文 Birch Caption Retrieval 的方法。", True),
    ("只读《Birch Caption Retrieval》，解释里面的 Literature Review。", False),
])
def test_explicit_target_overrides_old_selection(reading,question,multiple):
    s=reading
    cid=create(s)
    selected=[s["pdf"],"legacy-opening"] if multiple else [s["pdf"]]
    message=expect(send(s,cid,question,selected_paper_ids=selected))["messages"][-1]
    packet=trace(s)
    assert packet["reading_selection"]["single_paper"]
    assert [p["paper_id"] for p in packet["papers"]]==["birch"]
    assert [p["paper_id"] for p in packet["paper_inventory"]]==["birch"]
    assert message["reading_materials"]==packet["papers"]
    assert [(q["paper_id"],q["quote"]) for q in message["reading_citations"]]==[("birch",COMPARE)]


@pytest.mark.parametrize("question,selected,reason", [
    ("只看这篇论文的方法。", [], "只勾选一篇"),
    ("只看这篇论文的方法。", ["birch","legacy-opening"], "只勾选一篇"),
    ("只读《Twin Retrieval Study》的方法。", ["birch"], "匹配多篇"),
    ("只读《Twin Retrieval Study》的方法。", ["twin-1"], "匹配多篇"),
    ("只读《Unavailable Target》的方法。", ["birch"], "未匹配"),
    ("只读 Unavailable Target 的方法。", ["birch"], "未匹配"),
    ("只读《Graph Literature Review》的方法。", ["birch"], "未匹配"),
    ("只看 DOI 10.5555/other-project 的方法。", ["birch"], "未匹配"),
    ("只看 DOI 10.5555/other-account 的方法。", ["birch"], "未匹配"),
    ("只看 DOI 10.5555/birch-caption-extended 的方法。", ["birch"], "未匹配"),
    ("只读《Twin Retrieval Study》，DOI " + BIRCH_DOI, ["birch"], "不一致"),
])
def test_unresolved_reading_target_has_no_model_or_saved_request(reading,question,selected,reason):
    s=reading
    cid=create(s)
    result=send(s,cid,question,selected_paper_ids=selected)
    assert result.status_code==422
    assert result.json()["detail"]["code"]=="paper_reading_target"
    assert reason in result.json()["detail"]["message"]
    assert not s["calls"]
    assert s["a"].get(base(s,cid)+"/requests/turn").status_code==404
    assert not expect(s["a"].get(base(s,cid)+"/messages"))["messages"]
    assert not expect(s["a"].get(base(s,cid)+"/research-tasks"))["tasks"]


def test_demonstrative_uses_the_single_selected_paper(reading):
    s=reading
    cid=create(s)
    result=expect(send(s,cid,"只看这篇论文的方法。",selected_paper_ids=[s["pdf"]]))
    assert [p["paper_id"] for p in trace(s)["papers"]]==[s["pdf"]]
    assert result["messages"][-1]["reading_citations"][0]["quote"]==METHOD


@pytest.mark.parametrize("pid,origin,available", [
    ("title-only","unknown","metadata"), ("abstract-only","unknown","abstract"),
    ("known-abstract","author_abstract","abstract"),
    ("legacy-opening","body_opening","abstract"), ("legacy-unknown","unknown","abstract"),
    ("model-summary","generated_summary","metadata"),
])
def test_reading_scope_does_not_invent_abstract_or_content(reading,pid,origin,available):
    s=reading
    cid=create(s)
    message=expect(send(s,cid,"请解释这篇论文的局限",selected_paper_ids=[pid]))["messages"][-1]
    paper=message["reading_materials"][0]
    assert paper["preview_origin"]==origin and paper["content_available"]==available
    assert not paper["excerpts"] and not message["reading_citations"]
    if origin=="unknown" and paper["abstract"]:
        assert "摘要来源未核实" in paper["scope"]
    if origin=="generated_summary":
        assert not paper["abstract"] and "未作为论文原文" in paper["selection_note"]


def test_empty_parse_and_no_match_are_distinct(reading):
    s=reading
    response=upload_pdf(s,[""],"empty-pdf")
    assert response.status_code==422 and "未入库" in response.json()["detail"]
    assert len(list((s["api"]._CONFIG.paths.project_dir(s["pid"])/"uploads").glob("*.pdf")))==1
    cid=create(s)
    answer=expect(send(s,cid,"请解释这篇论文中的“ZirconNet”是什么意思。",selected_paper_ids=[s["pdf"]]))
    paper=answer["messages"][-1]["reading_materials"][0]
    assert not paper["excerpts"] and "没有选到" in paper["selection_note"]


def test_long_unbroken_text_dedup_and_adjacent_qualifier():
    paper=Paper(paper_id="p",title="Fictional paper",metadata={"chunk_origin":"body"})
    text="无关"*750+"\nMethods\nThe Cedar encoder uses a caption."
    chunks=[
        PaperChunk(chunk_id="long",paper_id="p",chunk_index=3,text=text),
        PaperChunk(chunk_id="adjacent",paper_id="p",chunk_index=4,text="However, it does not accept scanned pages."),
        PaperChunk(chunk_id="duplicate",paper_id="p",chunk_index=5,text="Methods\nThe Cedar encoder uses a caption."),
    ]
    selected=provided_paper(paper,chunks,text="论文的 Cedar 方法如何工作？")["excerpts"]
    assert any("The Cedar encoder uses" in e["text"] and e["start"]>0 for e in selected)
    assert any("does not accept scanned" in e["text"] for e in selected)
    assert len({e["text"].strip() for e in selected})==len(selected)<=3
    assert all(len(e["text"])<=1050 and e["page"] is None for e in selected)


@pytest.mark.parametrize("fault",["unselected","other_project","other_account","invented_id","wrong_quote"])
def test_quote_must_match_final_excerpt_and_project(reading,fault):
    s=reading
    cid=create(s)
    def bad(context,text):
        p=context["papers"][0]
        quote={"paper_id":p["paper_id"],"part":"excerpt",
               "excerpt_id":p["excerpts"][0]["excerpt_id"],"quote":METHOD}
        if fault=="unselected":
            quote.update(paper_id="birch",excerpt_id="birch-method:0:1",quote=COMPARE)
        if fault in ("other_project","other_account"):
            quote.update(paper_id=fault.replace("_","-"),excerpt_id=fault.replace("_","-")+"-chunk")
        if fault=="invented_id":
            quote["excerpt_id"]="not-delivered"
        if fault=="wrong_quote":
            quote["quote"]="The appendix supplies a hidden result."
        return GuideDraft(reply="离线无效来源故障。",reading_citations=[quote])
    s["control"]["result"]=bad
    assert send(s,cid,"只看这篇论文的 Cedar 方法",selected_paper_ids=[s["pdf"]]).status_code==422
    result=expect(s["a"].get(base(s,cid)+"/requests/turn"))
    assert result["failure"]["reason"]=="structure_invalid"
    assert not result["failure"]["can_retry"]
    assert all(m["role"]=="user" for m in result["messages"])


def test_budget_is_fair_and_bounds_entire_prompt(reading):
    s=reading
    cid=create(s)
    brief(s,goal="目标"*950,background="基础"*950,resources="条件"*950,time_budget="时间"*450)
    ids=[]
    for idx in range(3):
        pid=f"budget-{idx}";ids.append(pid)
        s["store"].save_paper(Paper(paper_id=pid,title=f"Budget source {idx}",
            abstract=("Preview text. "*120),metadata={"text_origin":"source_abstract","chunk_origin":"body"}))
        s["store"].save_paper_chunks([
            PaperChunk(chunk_id=f"{pid}-{i}",paper_id=pid,chunk_index=i,
                       text=f"Method section number {i}. "+(f" Cedar pipeline branch {i}. "*45))
            for i in range(6)
        ])
    for i in range(8):
        expect(send(s,cid,"请帮我讨论条件。"+str(i)+"x"*4200,rid=f"history-{i}"))
    result=expect(send(s,cid,"比较这几篇论文的 Cedar method。",rid="bounded",selected_paper_ids=ids))
    call=s["calls"][-1]
    packet=call["context"]
    assert len(call["system"])+len(call["prompt"])<=INPUT_CHARS
    assert [p["paper_id"] for p in packet["papers"]]==ids
    assert all(p["excerpts"] for p in packet["papers"])
    assert sum(len(p["abstract"])+sum(len(e["text"]) for e in p["excerpts"]) for p in packet["papers"])<=PAPER_TEXT_CHARS
    assert result["messages"][-1]["reading_materials"]==packet["papers"]
    assert any("预算未提供" in p["selection_note"] for p in packet["papers"])
    def excluded(context,text):
        p=context["papers"][-1]
        actual={e["chunk_id"] for e in p["excerpts"]}
        missing=next(c for c in s["store"].get_paper_chunks(p["paper_id"]) if c.chunk_id not in actual)
        return GuideDraft(reply="尝试引用被排除的片段。",reading_citations=[
            {"paper_id":p["paper_id"],"part":"excerpt","excerpt_id":missing.chunk_id+":0:1050","quote":missing.text[:50]}])
    s["control"]["result"]=excluded
    assert send(s,cid,"比较这几篇论文的 Cedar method。",rid="excluded",selected_paper_ids=ids).status_code==422


def test_repeat_quote_unknown_page_and_historical_snapshot(reading):
    s=reading
    cid=create(s)
    def duplicate(context,text):
        draft=explain(context,text)
        return draft.model_copy(update={"reading_citations":draft.reading_citations*2})
    s["control"]["result"]=duplicate
    result=expect(send(s,cid,"请读这篇论文的 Birch 方法",selected_paper_ids=["birch"]))
    message=result["messages"][-1]
    quote=message["reading_citations"][0]
    assert len(message["reading_citations"])==1 and quote["page"] is None
    assert quote["chunk_id"]=="birch-method"
    p=s["store"].get_paper("birch")
    s["store"].save_paper(p.model_copy(update={"title":"Changed Birch"}))
    changed=expect(s["a"].get(base(s,cid)+"/messages"))["messages"][-1]
    assert changed["reading_basis_changed"] and changed["reading_citations"]==[quote]
    with s["store"]._connect() as conn:
        conn.execute("DELETE FROM papers WHERE paper_id='birch'")
    removed=expect(s["a"].get(base(s,cid)+"/messages"))["messages"][-1]
    assert removed["reading_materials"]==message["reading_materials"] and removed["reading_basis_changed"]


def test_duplicate_text_does_not_invent_unique_position():
    from core.workspace.paper_reading import resolve_quote
    from core.workspace.schema import GapSource

    text="One repeated original sentence."
    papers={"p":{"title":"Sample","excerpts":[
        {"excerpt_id":"one","text":text,"page":1},
        {"excerpt_id":"two","text":text,"page":3},
    ]}}
    source=GapSource(paper_id="p",part="excerpt",quote=text)
    ambiguous=resolve_quote(source,papers)
    assert ambiguous.page is None and ambiguous.excerpt_id is None
    exact=resolve_quote(source.model_copy(update={"excerpt_id":"two"}),papers)
    assert exact.excerpt_id=="two" and exact.page==3
