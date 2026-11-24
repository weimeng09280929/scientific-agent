"""Server-authored search facts. Not generated or re-estimated by a model."""

from datetime import datetime, timezone


def coverage_facts(
    queries,
    calls,
    ranked,
    *,
    unique_count=None,
    saved=(),
    linked=(),
    limits=None,
    ranking_mode=None,
):
    latest = {}
    for c in calls:
        latest[(c.get("query"), c["source"])] = c
    current = list(latest.values())
    included = [c for c in ranked if c.get("relevance") != "excluded"]
    excluded = [c for c in ranked if c.get("relevance") == "excluded"]
    return {
        "version": 1,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
        "source_calls": current,
        "raw_returned_attempt_total": (
            sum(c["raw_count"] for c in calls)
            if all(c.get("raw_count") is not None for c in calls)
            else None
        ),
        "valid_returned_attempt_total": sum(c.get("count", 0) for c in calls),
        "latest_valid_returned_total": sum(c.get("count", 0) for c in current),
        "unique_before_limit": unique_count,
        "screened_candidates": len(ranked),
        "omitted_by_candidate_limit": (
            max(0, unique_count - len(ranked)) if unique_count is not None else None
        ),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "newly_saved_count": len(saved),
        "linked_existing_count": len(linked),
        "excluded": [
            {
                "paper_id": c["paper_id"],
                "title": c.get("title", ""),
                "reason": c.get("relevance_reason", "待复核"),
                "decision_source": c.get(
                    "relevance_by", ranking_mode or "historical_unspecified"
                ),
            }
            for c in excluded
        ],
        "metadata_only_count": sum(not c.get("has_abstract") for c in included),
        "abstract_count": sum(bool(c.get("has_abstract")) for c in included),
        "full_text_read_count": 0,
        "sources_at_result_limit": [
            c["source"]
            for c in current
            if c.get("count", 0) >= (limits or {}).get("max_results_per_call", 10)
        ],
        "limits": limits or {},
        "notice": "仅当前查询与来源的有限资料整理；排除不等于未返回，入库不等于纳入综合；未读取全文。",
    }


def scope_text(facts):
    failed = sorted(
        {c["source"] for c in facts["source_calls"] if c["status"] == "error"}
    )
    zero = sorted({c["source"] for c in facts["source_calls"] if c["status"] == "zero"})
    degraded = sorted({c["source"] for c in facts["source_calls"] if c.get("degraded")})
    return (
        f"本次为有限资料整理：来源各次有效返回合计 {facts['valid_returned_attempt_total']} 条（含重复），"
        f"处理 {facts['screened_candidates']} 条去重候选，其中 {facts['included_count']} 条纳入、{facts['excluded_count']} 条已返回但筛选排除；"
        f"新保存 {facts['newly_saved_count']} 篇、关联已有 {facts['linked_existing_count']} 篇。"
        + ("失败来源：" + "、".join(failed) + "。" if failed else "")
        + ("成功无结果来源：" + "、".join(zero) + "。" if zero else "")
        + (
            "部分记录无法解析的来源：" + "、".join(degraded) + "，已保留有效资料。"
            if degraded
            else ""
        )
        + "纳入资料中摘要可用 "
        + str(facts["abstract_count"])
        + " 条，仅元信息 "
        + str(facts["metadata_only_count"])
        + " 条；未读取全文。"
        + (
            f"候选上限另限制 {facts['omitted_by_candidate_limit']} 条未处理。"
            if facts["omitted_by_candidate_limit"]
            else ""
        )
    )
