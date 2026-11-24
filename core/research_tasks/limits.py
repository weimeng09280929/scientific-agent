"""有界检索的限流配置。全部为服务端配置，测试可注入，不由请求方决定。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchLimits:
    """一次检索任务的硬上限。实际执行数量必须被记录，不能静默放宽。"""

    max_queries: int = 3
    sources_per_query: int = 2
    max_candidates: int = 20
    max_results_per_call: int = 10
    call_timeout: int = 30

    def __post_init__(self):
        if min(
            self.max_queries,
            self.sources_per_query,
            self.max_candidates,
            self.max_results_per_call,
        ) < 1 or self.call_timeout <= 0:
            raise ValueError("检索上限必须为正数")

    def as_dict(self):
        return {
            "max_queries": self.max_queries,
            "sources_per_query": self.sources_per_query,
            "max_candidates": self.max_candidates,
            "max_results_per_call": self.max_results_per_call,
            "call_timeout": self.call_timeout,
        }


# 每条查询实际使用的来源（按顺序取前 sources_per_query 个）。
# 全部为仓库已配置的公开来源；不新增付费全文获取或绕过访问控制的下载。
DEFAULT_SOURCES = ("openalex", "arxiv", "semantic_scholar", "crossref")
