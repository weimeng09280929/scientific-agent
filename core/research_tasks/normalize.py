"""候选去重与标识规范化。

优先按规范化 DOI、arXiv 标识或可靠来源 ID 去重，再使用规范化标题的
精确相等辅助比较。不使用相似度：相似标题可能是不同研究，不得因此合并。
"""

from __future__ import annotations

import re
import unicodedata

_DOI_PREFIX = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)
_ARXIV_VERSION = re.compile(r"v\d+$", re.I)
_NOISE = re.compile(r"[^\w]+", re.UNICODE)


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    cleaned = _DOI_PREFIX.sub("", str(value).strip())
    return cleaned.lower().rstrip(".")


def normalize_arxiv_id(value: str | None) -> str:
    if not value:
        return ""
    cleaned = str(value).strip()
    cleaned = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^arxiv:", "", cleaned, flags=re.I)
    return _ARXIV_VERSION.sub("", cleaned).lower()


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return _NOISE.sub(" ", text).strip()


def _identity(candidate: dict) -> tuple[str, str, str]:
    """返回 (doi, arxiv_id, normalized_title) 三级标识。"""
    return (
        normalize_doi(candidate.get("doi")),
        normalize_arxiv_id(candidate.get("arxiv_id")),
        normalize_title(candidate.get("title")),
    )


def dedupe_candidates(raw: list[dict]) -> list[dict]:
    """合并同一论文的多来源记录；不因标题相似合并不同研究。

    合并策略：
    - DOI 或 arXiv ID 相同 => 同一论文，合并来源与检索记录；
    - DOI/arXiv 缺失时，规范化标题精确相等才合并（保守）；
    - 三项标识全空且标题为空的记录按来源外部 ID 去重，不与人工判断混淆。
    """
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for candidate in raw or []:
        doi, arxiv, title = _identity(candidate)
        if doi:
            key = ("doi", doi)
        elif arxiv:
            key = ("arxiv", arxiv)
        elif title:
            key = ("title", title)
        else:
            key = ("external", str(candidate.get("external_id") or id(candidate)))
        if key in merged:
            existing = merged[key]
            sources = existing.setdefault("sources", [existing.get("source")])
            if candidate.get("source") not in sources:
                sources.append(candidate.get("source"))
            # 只补缺失字段，不覆盖已有更完整元数据；冲突信息保留出处。
            for field in ("abstract", "year", "venue", "url", "pdf_url", "authors"):
                incoming = candidate.get(field)
                current = existing.get(field)
                if incoming and not (current if not isinstance(current, list) else current):
                    existing[field] = incoming
                elif incoming and current and field in ("abstract", "venue", "year") and incoming != current:
                    conflicts = existing.setdefault("field_conflicts", {})
                    conflicts[field] = sorted({str(current), str(incoming)})
            if candidate.get("has_abstract"):
                existing["has_abstract"] = True
            existing.setdefault("queries", [])
            if candidate.get("query") and candidate["query"] not in existing["queries"]:
                existing["queries"].append(candidate["query"])
        else:
            record = dict(candidate)
            record["sources"] = [candidate.get("source")] if candidate.get("source") else []
            record["queries"] = [candidate["query"]] if candidate.get("query") else []
            merged[key] = record
            order.append(key)
    return [merged[key] for key in order]
