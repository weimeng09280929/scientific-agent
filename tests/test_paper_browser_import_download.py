from pathlib import Path
from types import SimpleNamespace

import requests

from core.config import ProjectPaths
from core.knowledge.store import KnowledgeStore
from core.orchestration.context import ExecutionContext
from stages.common import DRY_RUN, LLM_REGISTRY
from stages.research.agents import PaperRelevanceFilterAgent
from stages.research.io_schema import PaperRelevanceFilterInput
from web import api


def _project(monkeypatch, tmp_path):
    paths = ProjectPaths.from_root(tmp_path)
    paths.project_dir("test").mkdir(parents=True)
    monkeypatch.setattr(api, "_CONFIG", SimpleNamespace(paths=paths))
    monkeypatch.setattr(api, "_require_project", lambda project_id: object())
    return KnowledgeStore(paths.project_db("test"))


def test_imported_arxiv_paper_can_download_pdf(monkeypatch, tmp_path):
    store = _project(monkeypatch, tmp_path)
    store.log_evidence({
        "subquery": "federated learning", "source": "arxiv",
        "title": "A verified arXiv paper", "external_id": "2401.12345v2",
        "snippet": "An abstract", "evidence_score": 0.7,
    })
    result = api.import_unlinked_paper("test", {
        "external_id": "2401.12345v2", "title": "A verified arXiv paper",
    })
    assert result["created"] is True
    paper = store.get_paper(result["paper_id"])
    assert paper.arxiv_id == "2401.12345"
    assert paper.url == "https://arxiv.org/abs/2401.12345"
    assert paper.metadata["pdf_url"] == "https://arxiv.org/pdf/2401.12345.pdf"
    assert not store.list_unlinked_evidence()

    pdf_bytes = b"%PDF-1.4\n" + b"0" * 1100
    requested = []
    closed = []

    def fake_get(url, **kwargs):
        requested.append(url)
        assert kwargs.get("stream") is True
        return SimpleNamespace(
            raise_for_status=lambda: None,
            iter_content=lambda chunk_size: (
                pdf_bytes[start:start + chunk_size]
                for start in range(0, len(pdf_bytes), chunk_size)
            ),
            close=lambda: closed.append(url),
        )

    monkeypatch.setattr(requests, "get", fake_get)
    response = api.download_paper_pdf("test", paper.paper_id)
    assert response.media_type == "application/pdf"
    assert Path(response.path).read_bytes() == pdf_bytes
    assert requested == ["https://arxiv.org/pdf/2401.12345.pdf"]
    assert closed == requested
    assert store.get_paper(paper.paper_id).pdf_path == response.path


def test_imported_crossref_paper_keeps_doi_and_is_not_duplicated(monkeypatch, tmp_path):
    store = _project(monkeypatch, tmp_path)
    store.log_evidence({
        "subquery": "security", "source": "crossref",
        "title": "A DOI paper", "external_id": "10.1234/example",
    })
    first = api.import_unlinked_paper("test", {
        "external_id": "10.1234/example", "title": "A DOI paper",
    })
    paper = store.get_paper(first["paper_id"])
    assert paper.doi == "10.1234/example"
    assert paper.url == "https://doi.org/10.1234/example"
    second = api.import_unlinked_paper("test", {
        "external_id": "10.1234/example", "title": "A DOI paper",
    })
    assert second["created"] is False
    assert len(store.list_papers()) == 1


def test_imported_s2_paper_keeps_doi_from_evidence_extra(monkeypatch, tmp_path):
    """S2 候选的 S2 paperId 无法直接还原 DOI；导入必须沿用证据链 extra 里的标识。"""
    store = _project(monkeypatch, tmp_path)
    store.log_evidence({
        "subquery": "fed security", "source": "s2",
        "title": "An S2 paper with DOI", "external_id": "s2paperid1234567890",
        "snippet": "An abstract", "evidence_score": 0.0,
        "doi": "10.5678/s2-doi", "url": "https://doi.org/10.5678/s2-doi",
    })
    result = api.import_unlinked_paper("test", {
        "external_id": "s2paperid1234567890", "title": "An S2 paper with DOI",
    })
    paper = store.get_paper(result["paper_id"])
    assert paper.doi == "10.5678/s2-doi"
    assert paper.url == "https://doi.org/10.5678/s2-doi"
    assert paper.metadata["pdf_url"] == ""


def test_unlinked_listing_flags_s2_duplicate_and_merges_cross_source_hits(monkeypatch, tmp_path):
    """同论文被 arXiv + S2 双源命中只出一张候选卡；已入库的 S2 候选标记 dedup_merged。"""
    store = _project(monkeypatch, tmp_path)
    # 同一标题、两个不同外部 ID（arXiv / S2）
    store.log_evidence({
        "subquery": "q", "source": "arxiv", "title": "Dual-source paper",
        "external_id": "2501.99999", "snippet": "a",
    })
    store.log_evidence({
        "subquery": "q", "source": "s2", "title": "Dual-Source Paper",
        "external_id": "s2id-abcdef", "snippet": "b",
        "doi": "10.9999/dual",
    })
    listing = api.list_unlinked_papers("test")
    assert listing["total"] == 1
    cand = listing["papers"][0]
    assert cand["external_id"] == "2501.99999"  # 先出现的 ID 保留
    assert cand["hit_count"] == 2

    # 导入后（以 S2 ID 入库），两源候选都应标记 dedup_merged 而非可再次入库
    from core.knowledge.schema import Paper
    store.save_paper(Paper(
        paper_id="p_s2_dup", title="Another S2 paper",
        metadata={"source": "s2", "doc_id": "s2id-xxxx"},
        source_stage="research",
    ))
    store.log_evidence({
        "subquery": "q", "source": "s2", "title": "Another S2 paper",
        "external_id": "s2id-xxxx", "snippet": "c",
    })
    listing2 = api.list_unlinked_papers("test")
    reasons = {p["title"]: p["reason"] for p in listing2["papers"]}
    assert reasons["Another S2 paper"] == "dedup_merged"


def test_relevance_filter_does_not_score_and_append_each_paper_twice(monkeypatch):
    node = PaperRelevanceFilterAgent("paper_filter")
    registry = SimpleNamespace(structured_output=lambda **kw: (_ for _ in ()).throw(
        AssertionError("batch scoring must not call per-paper scoring")
    ))
    monkeypatch.setattr(node, "_batch_score", lambda input_obj, metas, reg: (
        [{**m, "relevance_score": 0.8} for m in metas], []
    ))
    ctx = ExecutionContext(project_id="test")
    ctx.set(DRY_RUN, False)
    ctx.set(LLM_REGISTRY, registry)
    result = node._execute(PaperRelevanceFilterInput(
        topic="security", subqueries=["security"],
        paper_metas=[{"title": "Paper A", "source": "crossref"}],
    ), ctx)
    assert len(result.output.filtered_paper_metas) == 1
