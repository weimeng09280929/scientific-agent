"""Crossref 论文检索工具。

Crossref 无需 API Key，结果带 DOI，可作为方向推荐的可核验证据主源。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_CROSSREF_API = "https://api.crossref.org/works"
_CONTACT = "sciresearch.agent@outlook.com"
_HEADERS = {"User-Agent": f"SciResearchAgent/1.0 (mailto:{_CONTACT})"}


@dataclass
class CrossrefPaper:
    doi: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: Optional[int] = None
    abstract: str = ""
    venue: str = ""
    citation_count: int = 0
    url: str = ""
    source_subquery: str = ""

    def to_meta_dict(self) -> dict:
        return {
            "title": self.title.strip(),
            "authors": list(self.authors),
            "year": self.year,
            "abstract": self.abstract,
            "doi": self.doi,
            "venue": self.venue,
            "citation_count": self.citation_count,
            "url": self.url,
            "pdf_url": "",
            "source_subquery": self.source_subquery,
        }


def _first(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0] or "")
    return str(value or "")


def _year(item: dict) -> Optional[int]:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and isinstance(parts[0][0], int):
            return parts[0][0]
    return None


def _strip_markup(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def search_crossref(
    query: str,
    max_results: int = 10,
    source_subquery: str = "",
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    timeout: int = 20,
) -> list[CrossrefPaper]:
    cleaned = re.sub(r"\s+", " ", query or "").strip()
    if not cleaned:
        return []
    params: dict[str, object] = {
        "query.bibliographic": cleaned,
        "rows": min(max(1, max_results), 100),
        "mailto": _CONTACT,
        "select": "DOI,title,author,published-print,published-online,published,issued,created,URL,is-referenced-by-count,container-title,abstract",
    }
    filters = []
    if year_from is not None:
        filters.append(f"from-pub-date:{int(year_from)}-01-01")
    if year_to is not None:
        filters.append(f"until-pub-date:{int(year_to)}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    try:
        response = requests.get(_CROSSREF_API, params=params, headers=_HEADERS, timeout=timeout)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", []) or []
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Crossref 检索失败（query=%r）: %s", query, exc)
        return []

    papers: list[CrossrefPaper] = []
    for item in items:
        try:
            doi = (item.get("DOI") or "").strip()
            title = _first(item.get("title")).strip()
            if not doi or not title:
                continue
            authors = []
            for author in item.get("author") or []:
                name = " ".join(part for part in (author.get("given", ""), author.get("family", "")) if part).strip()
                if name:
                    authors.append(name)
            papers.append(CrossrefPaper(
                doi=doi,
                title=title,
                authors=authors,
                year=_year(item),
                abstract=_strip_markup(item.get("abstract") or ""),
                venue=_first(item.get("container-title")),
                citation_count=item.get("is-referenced-by-count") or 0,
                url=f"https://doi.org/{doi}",
                source_subquery=source_subquery,
            ))
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            logger.warning("解析 Crossref 论文失败: %s", exc)
    return papers
