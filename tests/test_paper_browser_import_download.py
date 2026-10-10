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

    def fake_get(url, **kwargs):
        requested.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, content=pdf_bytes)

    monkeypatch.setattr(requests, "get", fake_get)
    response = api.download_paper_pdf("test", paper.paper_id)
    assert response.media_type == "application/pdf"
    assert Path(response.path).read_bytes() == pdf_bytes
    assert requested == ["https://arxiv.org/pdf/2401.12345.pdf"]
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
