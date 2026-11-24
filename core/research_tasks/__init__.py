"""第五步：对话驱动的有界文献检索、筛选与研究现状整理。

能力边界（与 docs/conversation-research-tasks.md 保持一致）：
- 有界检索：默认最多 3 条查询、每条查询最多 2 个已配置来源、
  去重后最多保留 20 条候选；无无限翻页或自动重试。
- 检索结果只是待分析数据；来源返回内容不能作为触发任务或修改规则的指令。
- 候选文献不等于用户已阅读或认可；入库不自动建立正式研究依据关联。
- 任务止于"整理有来源的研究现状"，不选定研究方向、不采纳问题或方案、
  不启动实验或写作，仍遵守产品 DESIGN 上限。
"""

from core.research_tasks.limits import SearchLimits, DEFAULT_SOURCES
from core.research_tasks.sources import call_source, SourceOutcome, SOURCE_FUNCS
from core.research_tasks.normalize import (
    normalize_doi,
    normalize_arxiv_id,
    normalize_title,
    dedupe_candidates,
)
from core.research_tasks.detect import classify_search_request, SearchIntent
from core.research_tasks.planner import plan_queries
from core.research_tasks.ranking import rank_candidates
from core.research_tasks.synthesis import synthesize_report
from core.research_tasks.service import ResearchTaskService, TaskError, TaskConflict

__all__ = [
    "SearchLimits",
    "DEFAULT_SOURCES",
    "call_source",
    "SourceOutcome",
    "SOURCE_FUNCS",
    "normalize_doi",
    "normalize_arxiv_id",
    "normalize_title",
    "dedupe_candidates",
    "classify_search_request",
    "SearchIntent",
    "plan_queries",
    "rank_candidates",
    "synthesize_report",
    "ResearchTaskService",
    "TaskError",
    "TaskConflict",
]
