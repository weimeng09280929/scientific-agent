"""OpenAlex 论文检索工具。

用于在 arXiv 暂时不可用或 Semantic Scholar 匿名接口限流时提供稳定回退。
OpenAlex 为公开学术图谱，不需要 API Key；返回结果仍需经过方向相关性校验。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_OPENALEX_API = "https://api.openalex.org/works"
_CONTACT = "sciresearch.agent@outlook.com"
_HEADERS = {"User-Agent": f"SciResearchAgent/1.0 (mailto:{_CONTACT})"}


@dataclass
class OpenAlexPaper:
    openalex_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    abstract: str = ""
    venue: str = ""
    arxiv_id: Optional[str] = None
    doi: Optional[str] = None
    citation_count: int = 0
    url: str = ""
    pdf_url: str = ""
    source_subquery: str = ""

    def to_meta_dict(self) -> dict:
        return {
            "title": self.title.strip(),
            "authors": list(self.authors),
            "year": self.year,
            "abstract": self.abstract,
            "arxiv_id": self.arxiv_id,
            "doi": self.doi,
            "venue": self.venue,
            "citation_count": self.citation_count,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "source_subquery": self.source_subquery,
        }


def _plain_doi(value: str) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value or "", flags=re.I)


def _abstract_from_index(index: object) -> str:
    """把 OpenAlex 的倒排摘要还原为普通文本。"""
    if not isinstance(index, dict) or not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, offsets in index.items():
        if not isinstance(offsets, list):
            continue
        for offset in offsets:
            if isinstance(offset, int):
                positions.append((offset, str(word)))
    return " ".join(word for _, word in sorted(positions))


def search_openalex(
    query: str,
    max_results: int = 10,
    source_subquery: str = "",
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    timeout: int = 30,
) -> list[OpenAlexPaper]:
    """按关键词搜索 OpenAlex；失败时返回空列表。"""
    cleaned = re.sub(r"[?:\"']", " ", query or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []

    params: dict[str, object] = {
        "search": cleaned,
        "per-page": min(max(1, max_results), 100),
        "mailto": _CONTACT,
    }
    filters = []
    if year_from is not None:
        filters.append(f"from_publication_date:{int(year_from)}-01-01")
    if year_to is not None:
        filters.append(f"to_publication_date:{int(year_to)}-12-31")
    if filters:
        params["filter"] = ",".join(filters)

    try:
        resp = requests.get(_OPENALEX_API, params=params, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        items = resp.json().get("results", []) or []
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("OpenAlex 检索失败（query=%r）: %s", query, exc)
        return []

    papers: list[OpenAlexPaper] = []
    for item in items:
        try:
            primary = item.get("primary_location") or {}
            best_oa = item.get("best_oa_location") or {}
            source = primary.get("source") or {}
            ids = item.get("ids") or {}
            authors = [
                ((entry.get("author") or {}).get("display_name") or "").strip()
                for entry in (item.get("authorships") or [])
            ]
            authors = [name for name in authors if name]
            doi = _plain_doi(item.get("doi") or "")
            landing = (primary.get("landing_page_url") or "").strip()
            url = (f"https://doi.org/{doi}" if doi else landing) or (item.get("id") or "")
            pdf_url = (best_oa.get("pdf_url") or primary.get("pdf_url") or "").strip()
            arxiv = ids.get("arxiv") or ""
            arxiv_id = arxiv.rsplit("/", 1)[-1] if arxiv else None
            papers.append(OpenAlexPaper(
                openalex_id=item.get("id") or "",
                title=(item.get("title") or "").strip(),
                authors=authors,
                year=item.get("publication_year"),
                abstract=_abstract_from_index(item.get("abstract_inverted_index")),
                venue=source.get("display_name") or "OpenAlex",
                arxiv_id=arxiv_id,
                doi=doi or None,
                citation_count=item.get("cited_by_count") or 0,
                url=url,
                pdf_url=pdf_url,
                source_subquery=source_subquery,
            ))
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("解析 OpenAlex 论文失败: %s", exc)
    return papers
