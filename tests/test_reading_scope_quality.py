"""Citation locations preserve declared scope instead of inferring it from storage."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from core.knowledge.schema import Paper, PaperChunk
from core.workspace.plan_quality import assess_plan, render_quality
from core.workspace.teaching_packages import plan_for as teaching_plan
from tests.test_material_plan_quality import plan_for
from tests.test_project_conversations import chat, create, tree
from tests.test_research_plan_templates import answer, generate, model, attach_exports


@pytest.mark.parametrize("storage", ["abstract", "chunks", "both"])
def test_publisher_catalog_and_news_scope_is_never_relabelled_abstract_or_body(storage):
    quote = "Public catalog description for solid-state electrolytes."
    scope = "出版方目录简介、书目信息和机构报道（未读全文）"
    paper = Paper(paper_id="p1", title="Readable source", doi="10.0000/readable",
                  abstract=quote if storage != "chunks" else "",
                  metadata={"reading_scope": scope})
    chunks = {"p1": [PaperChunk(chunk_id="c1", paper_id="p1", chunk_index=0,
                                text=quote, page=7)]} if storage != "abstract" else {}
    before = deepcopy(paper.model_dump())
    report = assess_plan(plan_for(quote), {"p1": paper}, chunks,
                         target_id="plan1", workspace_revision=2)
    entry = report.evidence[0]
    assert entry.matched and scope in entry.location
    assert "摘要" not in entry.location and "正文" not in entry.location
    assert entry.page == (7 if chunks else None)
    assert ("第 7 页" in entry.location) == bool(chunks)
    assert entry.location in render_quality(report)
    assert "来源：Readable source；DOI：10.0000/readable" in render_quality(report)
    assert paper.model_dump() == before


@pytest.mark.parametrize("declared", [None, "", {}, 1])
@pytest.mark.parametrize("has_chunk", [False, True])
def test_undeclared_or_malformed_scope_does_not_assert_fulltext(declared, has_chunk):
    quote = "Stored scientific passage."
    paper = Paper(paper_id="p1", title="Source", abstract=quote,
                  metadata={"reading_scope": declared})
    chunks = {"p1": [PaperChunk(chunk_id="c1", paper_id="p1", chunk_index=0,
                                text=quote)]} if has_chunk else {}
    entry = assess_plan(plan_for(quote), {"p1": paper}, chunks,
                        target_id="plan1", workspace_revision=2).evidence[0]
    assert entry.matched and "未核对全文" in entry.location
    assert "正文" not in entry.location
    if has_chunk:
        assert "已存片段" in entry.location and "阅读范围未声明" in entry.location
        assert "未标页码" in entry.location and entry.page is None
    else:
        assert entry.location == "摘要字段（未核对全文）"


def test_chapter_and_page_scope_survives_and_unmatched_sources_keep_failure_reason():
    quote = "Stored chapter excerpt."
    paper = Paper(paper_id="p1", title="Readable source",
                  metadata={"reading_scope": "公开第 2 章与表 3（未核对全部内容）"})
    chunks = {"p1": [PaperChunk(chunk_id="c1", paper_id="p1", chunk_index=0,
                                text=quote, page=9)]}
    plan = plan_for(quote)
    entry = assess_plan(plan, {"p1": paper}, chunks, target_id="plan1", workspace_revision=2).evidence[0]
    assert "第 2 章与表 3" in entry.location and "第 9 页" in entry.location and entry.page == 9
    missing = assess_plan(plan, {}, chunks, target_id="plan1", workspace_revision=2)
    assert not missing.evidence[0].matched and missing.evidence[0].page is None
    assert missing.evidence[0].location == "来源不在当前文献库"
    assert missing.evidence[0].title == "来源未匹配"
    plan.references = []
    absent_ref = assess_plan(plan, {"p1": paper}, chunks, target_id="plan1", workspace_revision=2)
    assert absent_ref.evidence[0].location == "引用未列入参考来源"
    assert not absent_ref.evidence[0].matched and absent_ref.evidence[0].page is None


def test_real_sulfide_teaching_sources_preserve_three_distinct_reading_scopes():
    package = json.loads((Path(__file__).parents[1] / "content/teaching-cases/sulfide-design/1.1.0.json").read_text(encoding="utf-8"))
    mapping = {s["source_id"]: "internal-source-" + str(i) for i, s in enumerate(package["sources"])}
    question, scope = next(iter(package["variants"])).split(":")
    plan = teaching_plan(package, question, scope, "question1", mapping)
    papers = {mapping[s["source_id"]]: Paper(paper_id=mapping[s["source_id"]],
              title=s["title"], doi=s.get("doi"), abstract=s["summary"],
              metadata={"reading_scope": s["reading_scope"]}) for s in package["sources"]}
    report = assess_plan(plan, papers, {}, target_id="plan1", workspace_revision=2)
    assert len(report.evidence) == 3
    for entry, source in zip(report.evidence, package["sources"]):
        assert source["reading_scope"] in entry.location and entry.matched
    assert "摘要" in report.evidence[0].location
    assert "摘要" not in report.evidence[2].location
    text = render_quality(report)
    assert package["sources"][2]["reading_scope"] in text
    assert all(paper.title in text for paper in papers.values())
    assert all(pid not in text for pid in papers)


def test_quality_endpoint_and_export_share_declared_scope_without_writes(chat):
    attach_exports(chat)
    service = chat.services["p1"]
    paper = service.store.list_papers()[0]
    paper.metadata["reading_scope"] = "出版方目录简介、书目信息和机构报道（未读全文）"
    service.store.save_paper(paper)
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: model(ctx, literature=True)
    draft = answer(generate(chat, cid))["suggestions"][0]
    before = tree(chat.root)
    report = answer(chat.client.get("/api/projects/p1/workspace/plan-quality", params={
        "conversation_id": cid, "suggestion_id": draft["suggestion_id"]}))
    location = report["evidence"][0]["location"]
    assert paper.metadata["reading_scope"] in location and "摘要" not in location
    assert any(c["code"] == "reading_scope" for c in report["checks"])
    exported = chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions/{draft['suggestion_id']}/export")
    assert exported.status_code == 200 and location in exported.text
    assert tree(chat.root) == before and len(chat.calls) == 1


def test_material_export_uses_readable_sources_and_marks_missing_sources():
    quote = "DFT predicts ionic conductivity of 1 mS/cm."
    plan = plan_for(quote)
    plan.references = ["internal-source-1"]
    plan.sections[1].statements[0].paper_ids = ["internal-source-1"]
    missing = assess_plan(plan, {}, {}, target_id="plan1", workspace_revision=2)
    text = render_quality(missing)
    assert "internal-source-1" not in text and "来源未匹配" in text
    paper = Paper(paper_id="internal-source-1", title="Readable paper", doi="10.0000/example")
    unmatched = assess_plan(plan, {paper.paper_id: paper}, {}, target_id="plan1", workspace_revision=2)
    text = render_quality(unmatched)
    assert "internal-source-1" not in text
    assert "Readable paper" in text and "10.0000/example" in text and "片段未匹配" in text


def test_legacy_citation_title_equal_to_internal_id_is_not_exposed_in_document():
    quote = "DFT predicts ionic conductivity."
    report = assess_plan(plan_for(quote), {}, {}, target_id="plan1", workspace_revision=2)
    report.evidence[0].title = report.evidence[0].paper_id
    internal_id = report.evidence[0].paper_id
    text = render_quality(report)
    assert internal_id not in text and "引用：来源未匹配" in text
    assert report.evidence[0].title == internal_id  # Presentation does not rewrite API data.
