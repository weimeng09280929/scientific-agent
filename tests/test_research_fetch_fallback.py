from types import SimpleNamespace

from stages.research import agents
from stages.research.io_schema import PaperFetchInput


def test_research_fetch_uses_crossref_after_primary_sources_empty(monkeypatch):
    calls = []
    monkeypatch.setattr(agents, "search_arxiv", lambda **kw: [])
    monkeypatch.setattr(agents, "search_semantic_scholar", lambda **kw: [])
    monkeypatch.setattr(agents, "sciverse_is_available", lambda: False)

    def crossref(**kw):
        calls.append(kw["query"])
        return [SimpleNamespace(to_meta_dict=lambda: {
            "title": "A real paper", "doi": "10.1234/example",
            "url": "https://doi.org/10.1234/example", "venue": "Journal",
        })]

    monkeypatch.setattr(agents, "search_crossref", crossref)
    node = agents.PaperFetchAgent("paper_fetch")
    node._year_min = None
    node._year_max = None
    node._venue_hint = ""
    metas, chain = node._real_fetch(PaperFetchInput(
        keywords=["long context retrieval"], query_strategy="",
        subqueries=["Why do long-context LLMs still need retrieval augmentation: motivation, failure modes, and decision criteria?"],
    ))
    assert len(metas) == 1
    assert metas[0]["source"] == "crossref"
    assert chain[0]["external_id"] == "10.1234/example"
    assert len(calls[0].split()) <= 8


def test_research_fetch_keyword_retry(monkeypatch):
    monkeypatch.setattr(agents, "search_arxiv", lambda **kw: [])
    monkeypatch.setattr(agents, "search_semantic_scholar", lambda **kw: [])
    monkeypatch.setattr(agents, "search_openalex", lambda **kw: [])
    monkeypatch.setattr(agents, "sciverse_is_available", lambda: False)
    seen = []

    def crossref(**kw):
        seen.append(kw["query"])
        if kw["query"] == "retrieval augmentation":
            return [SimpleNamespace(to_meta_dict=lambda: {
                "title": "Relevant paper", "doi": "10.1234/second",
                "url": "https://doi.org/10.1234/second", "venue": "Journal",
            })]
        return []

    monkeypatch.setattr(agents, "search_crossref", crossref)
    node = agents.PaperFetchAgent("paper_fetch")
    node._year_min = None
    node._year_max = None
    node._venue_hint = ""
    metas, _ = node._real_fetch(PaperFetchInput(
        keywords=["retrieval augmentation"], query_strategy="",
        subqueries=["Unrelated concept and another concept?"],
    ))
    assert len(metas) == 1
    assert "retrieval augmentation" in seen
