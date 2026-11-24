"""候选筛选与相关性理由。

规则：按任务相关性筛选；引用量与期刊声誉只作为元数据记录，
绝不参与打分，也不能被表述为结论可信度。

纳入/排除语义：每条候选带 relevance 标记（included / excluded）。
被排除的候选仍保留在任务候选列表中（明确标记排除原因），
但不进入研究现状整理的引用范围。
"""

from __future__ import annotations

import json
import logging
import re

from core.research_tasks.limits import SearchLimits

logger = logging.getLogger(__name__)

_STOP = {
    "the", "a", "an", "of", "for", "and", "with", "based", "via", "on", "in",
    "to", "by", "using", "from", "towards", "toward",
}


def _terms(text: str) -> set[str]:
    lowered = (text or "").lower()
    words = {w for w in re.split(r"[^a-z0-9\u4e00-\u9fff]+", lowered) if len(w) > 1}
    return words - _STOP


def rank_candidates(
    candidates: list[dict],
    topic: str,
    limits: SearchLimits,
    model_fn=None,
) -> tuple[list[dict], str, str]:
    """为候选生成简短相关性理由，按相关性排序并截断到 max_candidates。

    返回 (ranked, ranked_by, note)。note 为空表示模型筛选正常；
    否则携带降级原因（路由缺失、输出截断、输出不可解析等），
    必须随任务持久化展示，不允许静默降级。
    每条候选附加 relevance_reason、relevance_score 与 relevance
    （"included" | "excluded"）。
    """
    if not candidates:
        return [], "rules", ""
    topic_terms = _terms(topic)
    if model_fn is not None:
        try:
            payload = {
                "prompt": (
                    "为每条候选给出一句话相关性理由（不超过 60 字），并判断其是否属于检索主题"
                    "（同名但无关的文献要标记为不相关）。只能依据其标题与摘要内容，"
                    "不得评价可信度、不得引用外部知识。输出 JSON："
                    '{"rankings": [{"index": 0, "reason": "...", "relevant": true}]}\n'
                    "检索主题：" + topic[:200] + "\n候选："
                    + json.dumps(
                        [
                            {
                                "index": i,
                                "title": c.get("title", "")[:200],
                                "abstract": (c.get("abstract") or "")[:400],
                                "year": c.get("year"),
                            }
                            for i, c in enumerate(candidates)
                        ],
                        ensure_ascii=False,
                    )
                )
            }
            raw = model_fn("rank", payload)
            text = raw if isinstance(raw, str) else raw.get("text", "")
            data = json.loads(text.strip().strip("`").removeprefix("json").strip())
            reasons = {}
            for r in data.get("rankings", []):
                if not isinstance(r, dict) or not isinstance(r.get("index"), (int, float)):
                    continue
                reasons[int(r["index"])] = (
                    str(r.get("reason") or "")[:120],
                    bool(r.get("relevant", True)),
                )
            ranked = []
            for i, candidate in enumerate(candidates):
                item = dict(candidate)
                reason, relevant = reasons.get(i, ("", None))
                if reason:
                    item["relevance_reason"] = reason
                    item["relevance_score"] = 0 if relevant is False else 1
                else:
                    # 模型未覆盖的候选不做排除判定，保守纳入并标注理由。
                    item["relevance_reason"] = _rule_reason(candidate, topic_terms)
                    item["relevance_score"] = 0
                    relevant = None
                # 排除只由模型显式判定（relevant=False）；规则无法可靠判定
                # 相关性（尤其中文整串匹配），不承担排除责任。
                item["relevance"] = "excluded" if relevant is False else "included"
                item["relevance_by"] = "model" if relevant is not None else "rules_unclassified"
                ranked.append(item)
            # 模型只提供理由与纳入判定，排序仍按规则信号，避免黑箱排序丢失引用字段。
            ranked.sort(key=lambda c: c["relevance_score"], reverse=True)
            return ranked[: limits.max_candidates], "model", ""
        except Exception as exc:  # 模型链路任何失败（路由缺失/截断/网络）都明确降级
            logger.warning("候选筛选模型输出不可用，回退规则筛选: %s", exc)
            ranked = _rule_rank(candidates, topic_terms, limits)
            return (
                ranked,
                "rules",
                f"模型筛选失败，已回退规则筛选（{type(exc).__name__}: {str(exc)[:120]}）",
            )
    return _rule_rank(candidates, topic_terms, limits), "rules", ""


def _rule_rank(candidates: list[dict], topic_terms: set[str], limits: SearchLimits):
    ranked = []
    for candidate in candidates:
        item = dict(candidate)
        reason = _rule_reason(candidate, topic_terms)
        score = _rule_score(candidate, topic_terms)
        item["relevance_reason"] = reason
        item["relevance_score"] = score
        # 规则模式不排除：相关性判定交给模型或人工复核，低相关以理由标注。
        item["relevance"] = "included"
        item["relevance_by"] = "rules_unclassified"
        ranked.append(item)
    ranked.sort(key=lambda c: (c["relevance_score"], c.get("has_abstract", False)), reverse=True)
    return ranked[: limits.max_candidates]


def _rule_reason(candidate: dict, topic_terms: set[str]) -> str:
    title = candidate.get("title", "")
    hits = sorted(t for t in topic_terms if t and t in title.lower())
    if hits:
        return "标题与主题共享关键术语：" + "、".join(hits[:4])
    abstract_hits = [
        t for t in topic_terms if t and t in (candidate.get("abstract") or "").lower()
    ]
    if abstract_hits:
        return "摘要中涉及主题术语：" + "、".join(abstract_hits[:4])
    return "与主题的匹配度较低，仅供参照"


def _rule_score(candidate: dict, topic_terms: set[str]) -> int:
    title = (candidate.get("title") or "").lower()
    abstract = (candidate.get("abstract") or "").lower()
    return sum(3 * (t in title) + (t in abstract) for t in topic_terms)
