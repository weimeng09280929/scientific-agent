"""Version-local report sources and display helpers. Never read the live library.

Machine IDs stay intact. Only known citation tokens in prose are formatted;
titles, stored reports, and the underlying reference associations are not replaced.
"""
from copy import deepcopy
import re


READING = {
    "metadata": "仅元信息，未据此分析方法或结论",
    "abstract": "摘要，未阅读全文",
    "abstract_excerpt": "摘要节选（本次提供至多 1200 字符），未阅读全文",
    "body_excerpt": "正文节选，不等于阅读全文",
    "full_text": "全文（仅按该版本明确保存的阅读记录）",
    "unknown": "阅读范围未记录，不能认定已读摘要或全文",
}


def freeze_sources(report, candidates, mode):
    """Freeze precisely the metadata/reading input used by THIS synthesis.

    The synthesis interface currently receives metadata + abstract excerpts,
    never library full text. Library availability cannot upgrade that scope.
    """
    result = deepcopy(report)
    catalog = []
    for candidate in candidates:
        included = candidate.get("relevance") != "excluded"
        readable = mode == "model" and included and candidate.get("has_abstract") and bool(candidate.get("abstract"))
        catalog.append({
            "paper_id": candidate["paper_id"], "title": candidate.get("title") or None,
            "authors": deepcopy(candidate.get("authors") or []), "year": candidate.get("year"),
            "doi": candidate.get("doi") or None, "url": candidate.get("url") or None,
            "reading_scope": "abstract_excerpt" if readable else "metadata",
            "included_in_synthesis": included,
        })
    result["source_snapshot"] = {"schema_version": 2, "sources": [deepcopy(p) for p in catalog if p["included_in_synthesis"]], "catalog": catalog}
    # Persist the exact numbering used by this delivery, independent of later
    # library changes or renderer evolution. Unknown IDs are never resolved.
    result["source_snapshot"]["reference_order"] = _all_reference_ids(result)
    return result


def _sources(report):
    snapshot = report.get("source_snapshot") or {}
    sources = snapshot.get("sources") or []
    if not snapshot:
        # Only metadata embedded IN the original report is version-owned. In
        # particular, current task candidates/library records may be newer.
        sources = [p for p in report.get("representative", []) if isinstance(p, dict) and p.get("title")]
    return {p["paper_id"]: p for p in sources if isinstance(p, dict) and isinstance(p.get("paper_id"), str)}


def _token_pattern(ids):
    known = "|".join(re.escape(p) for p in sorted(ids, key=len, reverse=True))
    token = (known + "|" if known else "") + r"cand-[A-Za-z0-9_-]+|paper:[A-Za-z0-9_-]+"
    return re.compile(r"(?<![A-Za-z0-9_-])(?:paper:)?(" + token + r")(?![A-Za-z0-9_-])")


def _prose_ids(text, known):
    return [m.group(1).removeprefix("paper:") for m in _token_pattern(known).finditer(str(text or ""))]


def prose_reference_ids(text, report):
    return _prose_ids(text, _sources(report))


def entry_reference_ids(item, report):
    """Structured and explicit prose references are one evidence obligation."""
    structured = [item["paper_id"]] if item.get("paper_id") else item.get("paper_ids") or []
    return list(dict.fromkeys([*structured, *prose_reference_ids(item.get("point"), report)]))


def _all_reference_ids(report):
    ids = []
    for item in report.get("representative") or []:
        ids.extend(entry_reference_ids(item, report))
    ids.extend(prose_reference_ids(report.get("methods_relations"), report))
    for item in report.get("limitations") or []:
        ids.extend(entry_reference_ids(item, report))
    return list(dict.fromkeys(ids))


def reference_index(report):
    sources = _sources(report)
    ids = []
    for item in report.get("representative") or []:
        if isinstance(item, dict) and item.get("paper_id"):
            ids.append(item["paper_id"])
    ids.extend(_prose_ids(report.get("methods_relations"), sources))
    for item in report.get("limitations") or []:
        if isinstance(item, dict):
            ids.extend(item.get("paper_ids") or [])
    # v1 had numbered structured IDs and relations only. Preserve those
    # numbers; append missed cross-references without GET migrations. v2 has
    # a frozen, version-owned order. Neither path consults the current library.
    frozen = (report.get("source_snapshot") or {}).get("reference_order")
    ids = [*(frozen if frozen is not None else ids), *_all_reference_ids(report)]
    return [{"number": n, "paper_id": pid, **{k:v for k,v in sources.get(pid, {}).items() if k != "paper_id"},
             "missing_snapshot": pid not in sources}
            for n,pid in enumerate(dict.fromkeys(p for p in ids if isinstance(p,str)), 1)]


def readable(source):
    return not source.get("missing_snapshot") and source.get("reading_scope") in {"abstract", "abstract_excerpt", "body_excerpt", "full_text"}


def format_prose(text, references):
    numbers = {p["paper_id"]:p["number"] for p in references}
    def replace(match):
        pid = match.group(1).removeprefix("paper:")
        return f"[{numbers[pid]}]" if pid in numbers else "[引用未解析，需核对]"
    return _token_pattern(numbers).sub(replace, str(text or ""))


def cited_text(text, ids, references):
    numbers = {p["paper_id"]:p["number"] for p in references}
    labels = " ".join(f"[{numbers[pid]}]" for pid in dict.fromkeys(ids) if pid in numbers)
    return format_prose(text, references) + (" " + labels if labels else "（原记录未附可核对的来源）")


def research_points(report, maximum=3):
    """Only already-produced, source-bound content, not a new summarizer."""
    if report.get("mode") != "model":
        return []
    references = reference_index(report)
    sources = {p["paper_id"]:p for p in references}
    result = []
    for item in report.get("representative") or []:
        ids = entry_reference_ids(item, report)
        if item.get("point") and ids and all(readable(sources.get(pid, {})) for pid in ids):
            result.append((item["point"], ids))
        if len(result) >= maximum:
            break
    return result


def supported_relations(report):
    refs = reference_index(report)
    by_id = {p["paper_id"]:p for p in refs}
    text = report.get("methods_relations") or ""
    ids = _prose_ids(text, by_id)
    return text if ids and all(readable(by_id.get(pid, {})) for pid in ids) and report.get("mode") == "model" else ""


def supported_limitations(report):
    refs = {p["paper_id"]:p for p in reference_index(report)}
    return [{**p, "paper_ids":entry_reference_ids(p, report)} for p in report.get("limitations") or []
            if entry_reference_ids(p, report) and all(readable(refs.get(pid, {})) for pid in entry_reference_ids(p, report))
            and report.get("mode") == "model"]


def delivery_reference_ids(report):
    """Match exactly the sections _delivery_text actually displays."""
    points = research_points(report)
    if not points:
        return []
    ids = [pid for _, refs in points for pid in refs]
    if supported_relations(report):
        ids.extend(prose_reference_ids(report["methods_relations"], report))
    for item in supported_limitations(report)[:1]:
        ids.extend(item["paper_ids"])
    return list(dict.fromkeys(ids))


def source_lines(source, *, label):
    def clean(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("[", "\\[").replace("]", "\\]")
    authors = source.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    lines = [f"{label} {clean(source.get('title') or '题名缺失（该版本无法可靠还原）')}",
             "  - 作者：" + ("、".join(clean(a) for a in authors) if authors else "缺失"),
             "  - 年份：" + clean(source.get("year") or "缺失"),
             "  - DOI：" + clean(source.get("doi") or "缺失")]
    url = str(source.get("url") or "")
    if not url and source.get("doi"):
        url = "https://doi.org/" + str(source["doi"]).removeprefix("https://doi.org/")
    lines.append("  - 原文链接：" + (url if re.fullmatch(r"https?://[^\s<>]+",url) else "缺失或链接格式无法核验"))
    lines.append("  - 实际阅读范围：" + READING.get(source.get("reading_scope"), READING["unknown"]))
    if source.get("missing_snapshot"):
        lines.append("  - 此版本未保存可确认的来源快照，未从当前文献库猜补；请回到原始资料核对。")
    return lines


def references_markdown(report):
    refs = reference_index(report)
    lines = ["## 参考文献与阅读范围", "", "编号仅绑定本报告版本；存在引用不代表科学正确性已经验证。"]
    if not refs:
        lines.append("本报告未提供可解析的正文引用；资料目录不等于正文依据。")
    for source in refs:
        lines.extend(["", *source_lines(source,label=f"[{source['number']}]"),
                      f"  - 原始关联 ID（技术核对）：`{source['paper_id']}`"])
    return "\n".join(lines)


def catalog_markdown(task):
    report = task.get("report") or {}
    snapshot = report.get("source_snapshot") or {}
    catalog = snapshot.get("catalog") or []
    lines = [f"# 本轮保存／关联的文献目录：{task.get('topic','')}", "",
             f"报告版本：v{task.get('report_version') or '历史未标版'}", "",
             "内容状态：" + task.get("status_label", "未记录"), "",
             "这是本轮保存／关联的资料目录，不代表每篇都支撑报告正文，也不表示已经阅读全文。"]
    if not catalog:
        lines.append("该版本没有绑定资料目录快照，无法可靠还原；未使用后来新增的论文补齐。")
    cited = {p['paper_id']:p['number'] for p in reference_index(report)}
    for n, source in enumerate(catalog,1):
        label = f"目录 {n}" + (f" · 正文引用 [{cited[source['paper_id']]}]" if source['paper_id'] in cited else " · 非正文引用")
        lines.extend(["", *source_lines(source,label=label),
                      "  - 综合范围：" + ("纳入综合候选；是否用于正文见引用标记" if source.get('included_in_synthesis') else "已保存但筛选排除，不作为综合依据")])
    return "\n".join(lines)
