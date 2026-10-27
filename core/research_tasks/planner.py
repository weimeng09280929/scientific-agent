"""有界查询规划：默认最多 3 条查询，保留用户原始意图。

模型规划失败或不可用时回退规则规划；查询可做中英文术语转换，
但不得擅自增加用户没有提出的设备、数据或研究约束。
"""

from __future__ import annotations

import json
import re
import logging

from core.research_tasks.limits import SearchLimits
from core.llm.public_errors import fallback_note, public_error

logger = logging.getLogger(__name__)

_PLAN_PROMPT = (
    "你是学术检索查询规划器。根据用户的检索请求，生成不超过 {max_queries} 条学术数据库查询词。"
    "要求：1) 保留用户原始意图，不做概念替换；2) 可补充对应的英文术语便于检索；"
    "3) 不得添加用户没有提出的设备、数据集、方法或研究约束；4) 输出 JSON："
    '{{"queries": [{{"query": "...", "rationale": "为何这条查询有用"}}]}}。'
    "只输出 JSON，不要输出其他文字。\n\n用户请求：{request}\n主题：{topic}"
)


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _rule_queries(topic: str, request_text: str, limits: SearchLimits):
    """规则回退：主题本身 + （若含中文）不臆造英文翻译。"""
    queries = [{"query": topic, "rationale": "用户请求中的原始主题"}]
    cleaned = topic.strip()
    if cleaned and cleaned != topic:
        queries.append({"query": cleaned, "rationale": "主题的规范化形式"})
    return queries[: limits.max_queries]


def plan_queries(
    topic: str,
    request_text: str,
    limits: SearchLimits,
    model_fn=None,
) -> tuple[list[dict], str, str]:
    """返回 (queries, planned_by, note)。

    note 为空表示模型规划正常；否则携带降级原因（路由缺失、输出截断、
    输出不可解析等），必须随任务持久化展示，不允许静默降级。
    """
    if model_fn is not None:
        try:
            raw = model_fn(
                "plan",
                {
                    "prompt": _PLAN_PROMPT.format(
                        max_queries=limits.max_queries,
                        request=request_text[:2000],
                        topic=topic[:200],
                    )
                },
            )
            text = raw if isinstance(raw, str) else raw.get("text", "")
            data = json.loads(_strip_fences(text))
            planned = []
            for item in data.get("queries", [])[: limits.max_queries]:
                query = str(item.get("query") or "").strip()
                if not query:
                    continue
                planned.append(
                    {
                        "query": query[:300],
                        "rationale": str(item.get("rationale") or "")[:300],
                    }
                )
            if planned:
                return planned, "model", ""
            return _rule_queries(topic, request_text, limits), "rules", "模型规划输出为空"
        except Exception as exc:  # 模型链路任何失败（路由缺失/截断/网络）都明确降级
            logger.warning("查询规划模型输出不可用，回退规则规划: %s", public_error(exc))
            return (
                _rule_queries(topic, request_text, limits),
                "rules",
                fallback_note("planning", exc),
            )
    return _rule_queries(topic, request_text, limits), "rules", ""
