"""来源调用的状态封装。

既有检索函数（search_arxiv / search_openalex 等）为保护 research 阶段
不中断，会把异常吞掉并返回空列表。任务层必须区分：

- 来源调用失败（网络、解析、限流）；
- 来源调用成功但没有结果；
- 有结果但筛选后没有合适文献；
- 只有元信息，缺少可供分析的摘要或正文。

工具返回兼容 list 的 SearchResults，业务状态只来自显式诊断。
普通 list 仅用于原有自定义适配器/离线夹具，视为适配器声明的成功结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.tools.arxiv_search import search_arxiv
from core.tools.semantic_scholar import search_semantic_scholar
from core.tools.openalex_search import search_openalex
from core.tools.crossref_search import search_crossref
from core.tools.search_result import SearchResults, error_details

# 已配置来源注册表。测试通过 monkeypatch 此 dict 注入可控响应。
SOURCE_FUNCS = {
    "arxiv": search_arxiv,
    "semantic_scholar": search_semantic_scholar,
    "openalex": search_openalex,
    "crossref": search_crossref,
}


@dataclass
class SourceOutcome:
    """一次来源调用的完整审计记录。"""

    source: str
    query: str
    status: str  # ok | zero | error
    papers: list[dict] = field(default_factory=list)
    error: str = ""
    error_class: str = (
        ""  # 诊断分类：proxy_error/timeout/rate_limited/auth_or_blocked/network/unknown
    )
    degraded: str = ""  # 部分论文解析失败的降级说明
    started_at: str = ""
    finished_at: str = ""
    count: int = 0
    raw_count: int | None = None
    available: int | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None
    attempts: list[dict] = field(default_factory=list)

    def to_dict(self):
        return {
            "source": self.source,
            "query": self.query,
            "status": self.status,
            "count": self.count,
            "error": self.error,
            "error_class": self.error_class,
            "degraded": self.degraded,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "raw_count": self.raw_count,
            "available": self.available,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "attempts": self.attempts,
        }


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize_paper(source: str, query: str, raw: dict) -> dict:
    """把各工具的 meta dict 统一为任务层候选结构。"""
    abstract = (raw.get("abstract") or "").strip()
    title = (raw.get("title") or "").strip()
    return {
        "title": title,
        "authors": list(raw.get("authors") or []),
        "year": raw.get("year"),
        "abstract": abstract,
        "doi": (raw.get("doi") or "").strip() or None,
        "arxiv_id": (raw.get("arxiv_id") or "").strip() or None,
        "external_id": (
            raw.get("openalex_id") or raw.get("s2_id") or raw.get("url") or ""
        ).strip()
        or None,
        "url": (raw.get("url") or "").strip(),
        "pdf_url": (raw.get("pdf_url") or "").strip(),
        "venue": (raw.get("venue") or "").strip(),
        "citation_count": raw.get("citation_count"),
        "source": source,
        "query": query,
        "retrieved_at": _now(),
        # 检索取得的是元信息、摘要还是全文片段：本步只取元信息/摘要。
        "has_abstract": bool(abstract),
    }


def call_source(
    source: str,
    query: str,
    *,
    max_results: int,
    timeout: int,
    year_from=None,
    year_to=None,
    attempt: int = 1,
) -> SourceOutcome:
    """调用单个已配置来源，返回区分失败/零结果的状态封装。

    网络期间不持有任何数据库事务；异常一律不得向上抛出为未封装错误。
    """
    outcome = SourceOutcome(
        source=source, query=query, status="zero", started_at=_now()
    )
    func = SOURCE_FUNCS.get(source)
    if func is None:
        outcome.status = "error"
        outcome.error = f"来源 {source} 未配置"
        outcome.finished_at = _now()
        return outcome
    try:
        papers = func(
            query,
            max_results=max_results,
            year_from=year_from,
            year_to=year_to,
            timeout=timeout,
        )
    except Exception as exc:  # 既有工具通常不抛；此处兜底保证状态封装完整
        outcome.status = "error"
        diagnostic = error_details(exc)
        for key in ("error", "error_class", "retryable", "retry_after_seconds"):
            setattr(outcome, key, diagnostic[key])
        outcome.finished_at = _now()
        return outcome

    normalized = []
    parse_issues = 0
    for paper in papers or []:
        try:
            meta = (
                paper.to_meta_dict() if hasattr(paper, "to_meta_dict") else dict(paper)
            )
            normalized.append(_normalize_paper(source, query, meta))
        except Exception:
            parse_issues += 1
    outcome.papers = normalized
    outcome.count = len(normalized)
    if isinstance(papers, SearchResults):
        for key in (
            "raw_count",
            "available",
            "error",
            "error_class",
            "retryable",
            "retry_after_seconds",
            "attempts",
        ):
            setattr(outcome, key, getattr(papers, key))
        parse_issues += papers.parse_errors
        outcome.status = (
            "error" if papers.status == "error" else "ok" if normalized else "zero"
        )
    else:
        outcome.raw_count = len(papers or [])
        outcome.status = "ok" if normalized else "error" if parse_issues else "zero"
    if parse_issues:
        outcome.degraded = f"{parse_issues} 条结果解析失败，" + (
            "已保留可读取的资料" if normalized else "没有可纳入的有效资料"
        )
        if not normalized:
            outcome.status = "error"
        outcome.error_class = "parse_error"
    outcome.finished_at = _now()
    return outcome
