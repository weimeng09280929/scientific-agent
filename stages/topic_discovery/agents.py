"""topic_discovery 阶段 Agent / Human 节点实现。

节点拓扑（方向推荐）：
    TrendFetchAgent（获取论文数据 + 提取关键词频率）
    → TrendAnalysisAgent（计算增长率，分类新兴/稳定/饱和方向）
    → TopicRecommendAgent（LLM 生成推荐主题 + 解释）
    → TopicSelectHuman（用户选择推荐主题，写入 RESEARCH_TOPIC）

执行模式：
- dry_run=True  ：用占位趋势数据返回，不调用 LLM、不访问 arXiv API
- dry_run=False ：真实调用 arXiv API 获取论文，真实调用配置的 LLM（glm-5.3-flash） 生成推荐
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from core.llm import LLMRegistry
from core.knowledge.innovation_playbook import INNOVATION_PATTERNS_PROMPT
from core.tools.arxiv_search import search_arxiv
from core.tools.semantic_scholar import search_semantic_scholar
from core.tools.direction_evidence import (
    fallback_directions,
    federated_directions,
    fetch_direction_evidence,
    fetch_zh_evidence,
    is_federated_learning,
    is_large_model_security,
    large_model_security_directions,
    supporting_papers_for_topic,
)
from core.orchestration.context import ExecutionContext
from core.orchestration.node import (
    AgentNode,
    HumanNode,
    HumanResponse,
    NodeInput,
    NodeOutput,
    NodeResult,
    NodeStatus,
)
from core.tools.trend_analyzer import (
    TrendData,
    compute_growth_rates,
    fetch_keyword_trends,
    placeholder_trends,
)

from stages.common import (
    DRY_RUN,
    LLM_REGISTRY,
    PROJECT_DOMAIN,
    RESEARCH_TOPIC,
    TOPIC_DISCOVERY_ANALYSIS,
    TOPIC_DISCOVERY_INTEREST,
    TOPIC_DISCOVERY_RECOMMENDATIONS,
    TOPIC_DISCOVERY_SELECTED_TOPIC,
    TOPIC_DISCOVERY_TRENDS,
)

from stages.topic_discovery.io_schema import (
    TrendAnalysisInput,
    TrendAnalysisOutput,
    TrendFetchInput,
    TrendFetchOutput,
    TopicRecommendInput,
    TopicRecommendOutput,
    TopicSelectOutput,
)

logger = logging.getLogger(__name__)


# ===== 结构化输出 Schema（供 LLM structured_output 使用）=====

class TopicRecommendationItem(BaseModel):
    """单条推荐主题 schema。"""

    topic: str = Field(description="推荐的研究主题名称（简洁明确）")
    rationale: str = Field(description="为什么值得研究（引用趋势数据与领域分析）")
    innovation_point: str = Field(description="创新切入点（具体可操作的研究方向）")
    innovation_pattern: str = Field(
        default="",
        description="创新套路标签：application / modification / fusion / comparison / theory",
    )
    # 注：原字段名 recommended_materials 已更名为 recommended_objects——
    # 该字段名会经 structured_output 注入 LLM 的 system prompt（"需要填充的字段：..."），
    # 字段名里的 "materials" 本身就是强烈的学科锚点，会把推荐主题带向材料学。
    # 语义：「该领域推荐探索的研究对象」（材料体系 / 细胞系 / 模型 / 靶点…由领域决定）。
    # 前端读取时兼容旧字段名（recommended_materials）。
    recommended_objects: list[str] = Field(
        default_factory=list,
        description="推荐探索的具体研究对象（由研究领域决定：材料体系 / 细胞系 / 模型 / 靶点等）",
    )
    trend_summary: str = Field(description="该方向的趋势摘要（增长率、论文数等）")
    difficulty: str = Field(description="实现难度：easy / medium / hard")
    novelty: str = Field(description="创新程度：low / medium / high")
    # ===== 新增维度（热门 → 难度/创新性/关联性 结构化评估）=====
    relevance: str = Field(description="与用户研究兴趣的关联度：low / medium / high")
    popularity_score: int = Field(description="本轮检索样本活跃度 0-100；不是全领域热门度")
    growth_rate: float = Field(
        default=0.0, description="该方向对应关键词的年度增长率（如 0.35 表示 35%）"
    )
    evidence_id: str = Field(default="", description="支撑此推荐的细分方向 ID")
    direction_type: str = Field(default="", description="hot 或 innovation；创新性是待验证的研究假设")


class TopicRecommendationSchema(BaseModel):
    """主题推荐输出 schema。"""

    recommendations: list[TopicRecommendationItem] = Field(
        description="3-5 个推荐研究主题，按推荐优先级排序"
    )


class HotDirectionItem(BaseModel):
    """第一步：热门方向锁定。"""

    keyword: str = Field(description="热门关键词/方向名")
    popularity_score: int = Field(description="热门度 0-100")
    trend_summary: str = Field(description="一句话趋势摘要（增长率、论文数）")


class HotDirectionSchema(BaseModel):
    """热门方向列表输出 schema（第一步）。"""

    directions: list[HotDirectionItem] = Field(
        description="按热门度降序的 5-8 个热门方向"
    )


class DirectionSearchItem(BaseModel):
    id: str = Field(description="唯一英文短 ID，如 subfield_1")
    name: str = Field(description="该领域具体科研问题，不是泛泛的学科标签")
    query: str = Field(description="可用于 arXiv/Semantic Scholar 的英文标准学术检索词")


class DirectionSearchPlan(BaseModel):
    directions: list[DirectionSearchItem] = Field(description="3-5 个差异显著且与用户兴趣相关的细分问题")


# ===== TrendFetchAgent =====

class TrendFetchAgent(AgentNode):
    """趋势数据获取 Agent。

    根据用户研究兴趣，调用 arXiv API 获取论文，
    从标题/摘要中提取关键词并按年份统计频率。

    产出 TOPIC_DISCOVERY_TRENDS，供后续趋势分析与主题推荐使用。
    """

    node_type = "topic_trend_fetch"
    task_type = "topic_trend_analysis"
    input_schema = TrendFetchInput
    output_schema = TrendFetchOutput
    output_keys = {
        "trend_data": TOPIC_DISCOVERY_TRENDS,
    }

    DEFAULT_MAX_RESULTS = 200

    # 中文兴趣 → 英文 arXiv 检索词的系统提示（arXiv API 对中文查询返回 0 结果甚至报错，
    # 之前「不规则微带线」直接 502 → 空趋势数据 → LLM 无锚点 → 推荐被训练记忆带向材料学）
    _EN_QUERY_SYSTEM = (
        "你是学术检索助手。把用户的研究兴趣翻译成适合 arXiv 检索的英文关键词短语。"
        "要求：只输出检索短语本身（3-8 个英文单词，可含连字符复合词），"
        "使用该研究领域的标准学术术语，不要解释，不要引号，不要句号。"
    )

    @staticmethod
    def _has_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    def _translate_to_english(self, registry: LLMRegistry, interest: str) -> str:
        """把中文研究兴趣翻译为英文检索词；已是英文则原样返回。

        失败时（LLM 异常/超时）原样返回，由下游的 arXiv 兜底逻辑处理，
        不阻塞主流程。
        """
        if not self._has_cjk(interest):
            return interest
        try:
            resp = registry.complete(
                task_type=self.task_type,  # 复用 topic_trend_analysis 路由
                prompt=f"研究兴趣：{interest}",
                system=self._EN_QUERY_SYSTEM,
                temperature_override=0.1,
                max_tokens_override=100,
            )
            query = (resp.text or "").strip().strip('"').strip("。").strip()
            # 基本合法性：非空、长度合理、不含 CJK（翻译成功）
            if query and len(query) <= 120 and not self._has_cjk(query):
                logger.info("研究兴趣已翻译为英文检索词: %r -> %r", interest, query)
                return query
            logger.warning("研究兴趣翻译结果不可用（%r），按原文检索", query[:50])
        except Exception as e:  # noqa: BLE001
            logger.warning("研究兴趣翻译失败，按原文检索: %s", e)
        return interest

    def _build_input(self, ctx: ExecutionContext) -> TrendFetchInput:
        interest = ctx.get(TOPIC_DISCOVERY_INTEREST, "")
        return TrendFetchInput(interest=interest)

    def _execute(self, input_obj: TrendFetchInput, ctx: ExecutionContext) -> NodeResult:
        dry_run: bool = ctx.get(DRY_RUN, True)
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)

        if dry_run:
            trend_data = placeholder_trends(input_obj.interest).to_dict()
        else:
            # 中文兴趣先翻译为英文检索词（arXiv 只认英文）
            search_query = (
                self._translate_to_english(registry, input_obj.interest)
                if registry is not None else input_obj.interest
            )
            domain = ctx.get(PROJECT_DOMAIN) or ""
            # ===== 统一主路径（任意领域）：先「热门扫描」再规划——
            # 1) 把用户兴趣翻译成规范英文短语（如 成员推理 → membership inference attack）；
            # 2) 按该短语扫 S2 高被引经典 + arXiv 近两年新作，还原研究者在
            #    谷歌学术搜索时看到的真实热门图景；
            # 3) 规划器参考热门图景，按核心话题的侧面（攻击/防御/评测/组合/综述）
            #    出方向，检索式锚定核心短语，不偏题、不过度细分。
            hot = self._hot_scan(
                search_query,
                zh_query=input_obj.interest if self._has_cjk(input_obj.interest) else "",
            ) if registry else {}
            directions = (
                self._plan_directions(registry, input_obj.interest, domain, search_query, hot=hot)
                if registry else []
            )
            if directions:
                # 领域方向表仅作广度补充（最多 3 条），保证领域特有问题不缺位
                if is_federated_learning(input_obj.interest, domain):
                    supplement = federated_directions(input_obj.interest, domain)
                elif is_large_model_security(input_obj.interest, domain):
                    supplement = large_model_security_directions(input_obj.interest, domain)
                else:
                    supplement = []
                base_ids = {d["id"] for d in directions}
                directions = directions + [
                    d for d in supplement if d["id"] not in base_ids
                ][:3]
                directions = directions[:6]
            else:
                # 规划器不可用/未产出时才落领域方向表与关键词兜底
                if is_federated_learning(input_obj.interest, domain):
                    directions = federated_directions(input_obj.interest, domain)
                elif is_large_model_security(input_obj.interest, domain):
                    directions = large_model_security_directions(input_obj.interest, domain)
                else:
                    logger.warning("模型未生成有效检索计划，使用关键词检索兜底")
                    directions = fallback_directions(input_obj.interest, domain, search_query)

            evidence = fetch_direction_evidence(directions) if directions else []
            # 中文文献补充：用中文原词检索 OpenAlex/S2 的中文期刊论文，
            # 作为独立证据条目并入，推荐时可引用中英文两路证据
            if self._has_cjk(input_obj.interest):
                try:
                    zh_ev = fetch_zh_evidence(input_obj.interest)
                except Exception as e:  # noqa: BLE001
                    logger.warning("中文文献证据获取失败: %s", e)
                    zh_ev = None
                if zh_ev:
                    evidence.append(zh_ev)
            trend_data = {
                "query": search_query, "direction_evidence": evidence,
                "sample_papers": [p for d in evidence for p in d["papers"]][:20],
                "keyword_frequencies": {}, "total_papers_by_year": {},
                "total_fetched": sum(d["sample_count"] for d in evidence),
                "evidence_status": "retrieved" if evidence else "unavailable",
            }

            if not evidence:
                return NodeResult(
                    status=NodeStatus.FAILED,
                    error="细分方向检索未获得可核验论文，已停止推荐；请调整关键词或检查学术数据源连接",
                    summary="方向检索失败：0 篇可核验论文；不会使用占位主题或编造增长率",
                )

        # 确保查询关键词存入 context（供下游节点使用；保留用户原始兴趣原文）
        ctx.set(TOPIC_DISCOVERY_INTEREST, input_obj.interest)

        output = TrendFetchOutput(trend_data=trend_data)
        direction_count = len(trend_data.get("direction_evidence", []))
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=(
                f"细分方向检索完成：{direction_count} 个方向，{trend_data.get('total_fetched', 0)} 篇相关论文"
                if direction_count else
                f"趋势数据获取完成：{trend_data.get('total_fetched', 0)} 篇论文，"
                f"{len(trend_data.get('keyword_frequencies', {}))} 个关键词"
            ),
        )

    def _hot_scan(self, english_query: str, zh_query: str = "") -> dict:
        """热门扫描：还原研究者在学术搜索里看到的图景（中英文结合）。

        - classics：S2 按被引数排序的高被引论文（综述/开山作，如 Shokri 2017）；
          兴趣为中文时，另用中文原词扫 S2 的中文期刊论文
        - recent：arXiv 近两年的新作（当前社区正在做什么）

        失败返回空 dict，不阻塞主流程。
        """
        query = (english_query or "").strip()
        if not query or self._has_cjk(query):
            return {}
        classics: list[dict] = []
        recent: list[dict] = []
        try:
            s2 = search_semantic_scholar(query=query, max_results=20)
            s2.sort(key=lambda p: p.citation_count or 0, reverse=True)
            classics = [
                {"title": p.title, "year": p.year,
                 "citation_count": p.citation_count, "venue": p.venue}
                for p in s2[:8] if p.title
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("热门扫描 S2 失败（query=%r）: %s", query, e)
        # 中文原词补充扫描：S2/OpenAlex 索引了中文期刊论文，可反映中文社区热点
        zh = (zh_query or "").strip()
        if self._has_cjk(zh):
            try:
                s2_zh = search_semantic_scholar(query=zh, max_results=10)
                s2_zh.sort(key=lambda p: p.citation_count or 0, reverse=True)
                classics.extend(
                    {"title": p.title, "year": p.year,
                     "citation_count": p.citation_count, "venue": p.venue,
                     "language": "zh"}
                    for p in s2_zh[:4] if p.title
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("热门扫描 S2 中文失败（query=%r）: %s", zh, e)
        try:
            current_year = datetime.now().year
            arx = search_arxiv(
                query=query, max_results=20,
                year_from=current_year - 2,
            )
            recent = [
                {"title": p.title, "year": p.year}
                for p in arx[:8] if p.title
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("热门扫描 arXiv 失败（query=%r）: %s", query, e)
        if not classics and not recent:
            return {}
        return {"query": query, "classics": classics, "recent": recent}

    def _plan_directions(
        self,
        registry: LLMRegistry,
        interest: str,
        domain: str,
        english_query: str,
        hot: Optional[dict] = None,
    ) -> list[dict]:
        """研究方向规划：参考热门图景，按核心话题的侧面出方向。

        粒度对齐研究者的真实搜索习惯（如「成员推理」→ 攻击/防御/评测/组合/综述），
        方向检索式锚定核心话题短语，不偏题、不过度细分。
        """
        from core.knowledge.domain_profile import build_core_topics_hint, build_topic_recommend_hint

        hot = hot or {}
        hot_lines = []
        for p in hot.get("classics", [])[:5]:
            cites = p.get("citation_count") or 0
            hot_lines.append(f"- 经典（被引 {cites}）：{p.get('title','')} ({p.get('year') or ''})")
        for p in hot.get("recent", [])[:5]:
            hot_lines.append(f"- 近期：{p.get('title','')} ({p.get('year') or ''})")
        hot_block = ("\n\n该主题下的热门论文图景：\n" + "\n".join(hot_lines)) if hot_lines else ""

        try:
            plan = registry.structured_output(
                task_type=self.task_type,
                output_schema=DirectionSearchPlan,
                system=(
                    "你是学术检索规划专家。核心主题已确定为英文检索短语（见用户消息），"
                    "请像领域研究者在谷歌学术搜索这个主题一样，规划 3-5 个研究问题方向。"
                    "要求："
                    "1）每个方向是核心主题的一个侧面（如：攻击方法、防御与缓解、评测基准、"
                    "与其他技术结合的交叉场景、综述与实证分析），"
                    "核心主题短语必须完整保留在每个方向的 query 中，只通过附加侧面词区分；"
                    "2）方向不得偏移到其他主题——核心主题是成员推理，所有方向仍须围绕成员推理；"
                    "3）可参考热门论文图景判断社区当前关注哪些侧面，热门度不得编造；"
                    "4）方向之间差异显著，不复用跨领域通用词（智能筛选、可复现实证等）。"
                    "英文 query 必须是具体学术术语，不得凭空声称其热门或创新已获证实。"
                ),
                prompt=(
                    f"领域：{domain}\n研究兴趣：{interest}\n核心主题（英文检索短语）：{english_query}\n"
                    f"领域核心：{build_core_topics_hint(domain)}\n研究对象：{build_topic_recommend_hint(domain)}"
                    f"{hot_block}"
                ),
                temperature_override=0.3,
            )
        except Exception as exc:
            logger.warning("细分检索规划失败: %s", exc)
            return []
        output = []
        seen = set()
        for item in plan.directions[:5]:
            query = item.query.strip()
            if not query or self._has_cjk(query) or query.lower() in seen:
                continue
            seen.add(query.lower())
            output.append({"id": f"direction_{len(output) + 1}", "name": item.name.strip(), "query": query})
        return output


# ===== TrendAnalysisAgent =====

class TrendAnalysisAgent(AgentNode):
    """趋势分析 Agent。

    对关键词年度频率数据计算增长率，分类为：
    - emerging（新兴方向）：最近一年增长率 > 50%
    - stable（稳定方向）：增长率 10%-50%
    - saturated（饱和方向）：增长率 < 10% 或负增长

    纯统计计算，不调用 LLM。
    """

    node_type = "topic_trend_analysis"
    task_type = "topic_trend_analysis"
    input_schema = TrendAnalysisInput
    output_schema = TrendAnalysisOutput
    output_keys = {
        "analysis": TOPIC_DISCOVERY_ANALYSIS,
    }

    def _build_input(self, ctx: ExecutionContext) -> TrendAnalysisInput:
        return TrendAnalysisInput(
            trend_data=ctx.get(TOPIC_DISCOVERY_TRENDS, {})
        )

    def _execute(
        self, input_obj: TrendAnalysisInput, ctx: ExecutionContext
    ) -> NodeResult:
        keyword_frequencies = input_obj.trend_data.get("keyword_frequencies", {})
        direction_evidence = input_obj.trend_data.get("direction_evidence", [])

        if direction_evidence:
            analysis = {
                "emerging": [], "stable": [], "saturated": [], "all_keywords": [],
                "direction_samples": [
                    {"id": d["id"], "name": d["name"], "sample_count": d["sample_count"],
                     "recent_sample_count": d["recent_sample_count"]}
                    for d in direction_evidence
                ],
                "method": "细分方向论文检索样本；不估计年度增长率",
            }
        elif not keyword_frequencies:
            analysis = {
                "emerging": [],
                "stable": [],
                "saturated": [],
                "all_keywords": [],
            }
        else:
            analysis = compute_growth_rates(keyword_frequencies)

        output = TrendAnalysisOutput(analysis=analysis)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=(
                f"方向证据整理完成：{len(analysis['direction_samples'])} 个细分方向；年度增长率未验证"
                if direction_evidence else
                f"趋势分析完成：{len(analysis['emerging'])} 个新兴方向，"
                f"{len(analysis['stable'])} 个稳定方向，"
                f"{len(analysis['saturated'])} 个饱和方向"
            ),
        )


# ===== TopicRecommendAgent =====

class TopicRecommendAgent(AgentNode):
    """主题推荐 Agent。

    根据趋势分析结果，调用 LLM 生成 3-5 个推荐研究主题。
    每个主题包含：名称、理由、创新切入点、推荐研究对象、趋势摘要、难度、创新程度。

    LLM 深度参与：
    - 解读趋势数据的科学含义
    - 结合所选领域的核心研究主题推荐具体研究对象
    - 评估创新性与可行性

    领域锚定：prompt 中会注入领域核心研究主题提示（build_core_topics_hint），
    并要求所有命名示例保持学科中性，防止 LLM 被训练记忆里的高频学科带偏。
    """

    node_type = "topic_recommend"
    task_type = "topic_recommend"
    input_schema = TopicRecommendInput
    output_schema = TopicRecommendOutput
    output_keys = {
        "recommendations": TOPIC_DISCOVERY_RECOMMENDATIONS,
    }

    def _build_input(self, ctx: ExecutionContext) -> TopicRecommendInput:
        return TopicRecommendInput(
            interest=ctx.get(TOPIC_DISCOVERY_INTEREST, ""),
            trend_data=ctx.get(TOPIC_DISCOVERY_TRENDS, {}),
            analysis=ctx.get(TOPIC_DISCOVERY_ANALYSIS, {}),
        )

    def _execute(
        self, input_obj: TopicRecommendInput, ctx: ExecutionContext
    ) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        dry_run: bool = ctx.get(DRY_RUN, True)
        # 研究领域（领域自适应：决定推荐主题时「具体」体现在哪些名词与指标上）
        domain: str = ctx.get(PROJECT_DOMAIN) or ""

        if not dry_run and registry is not None and input_obj.trend_data.get("direction_evidence"):
            try:
                recommendations = self._evidence_recommend(input_obj, registry, domain)
                if not recommendations:
                    # 模型可能省略/写错 evidence_id，或课题超出论文范围。
                    # 保留真实检索证据，退回证据限定候选，不把有论文误报为零检索。
                    recommendations = self._evidence_fallback(input_obj, domain)
            except Exception as e:
                logger.warning("方向证据推荐失败: %s", e, exc_info=True)
                recommendations = self._evidence_fallback(input_obj, domain)
        elif not dry_run:
            recommendations = []
        else:
            recommendations = self._placeholder(input_obj, domain)

        if not dry_run and not recommendations:
            return NodeResult(
                status=NodeStatus.FAILED,
                error="没有通过论文证据及领域相关性校验的研究主题",
                summary="方向推荐失败：0 个有效主题；请调整领域或检索关键词",
            )
        output = TopicRecommendOutput(recommendations=recommendations)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=f"主题推荐完成：生成 {len(recommendations)} 个推荐主题",
        )

    def _evidence_recommend(self, input_obj: TopicRecommendInput, registry: LLMRegistry, domain: str) -> list[dict]:
        """联邦学习等细分方向按逐方向论文证据推荐，不使用单词频率推算热度。"""
        import json

        evidence = input_obj.trend_data["direction_evidence"]
        compact = [{
            "id": d["id"], "name": d["name"], "query": d["query"],
            "sample_count": d["sample_count"], "recent_sample_count": d["recent_sample_count"],
            "papers": [{"title": p["title"], "year": p.get("year"),
                        "url": p["url"], "citation_count": p.get("citation_count")}
                       for p in d["papers"][:5]],
        } for d in evidence]
        result = registry.structured_output(
            task_type=self.task_type,
            output_schema=TopicRecommendationSchema,
            system=(
                "你是科研选题顾问。只根据提供的细分方向及真实检索论文提出 3-5 个不同的课题。"
                "每条必须填写对应 evidence_id，且课题与该方向论文直接相关。"
                "至少区分成熟热门课题 hot 和具有可检验新研究问题的 innovation；"
                "创新机会是研究假设，不能宣称已经证明无人研究。"
                "用具体矛盾、研究对象、算法或评测场景写出创新切入点，不得套用通用模板。"
                "sample_count 仅为检索样本数，不是谷歌学术总量；"
                "没有跨年份可比样本，growth_rate 必须为 0，不能编造增长率、引用数或热度。"
                "popularity_score 由系统依据检索样本计算，不要自行估计热门度。"
            ),
            prompt=f"用户兴趣：{input_obj.interest}\n领域：{domain}\n检索证据：{json.dumps(compact, ensure_ascii=False)}",
            temperature_override=0.3,
        )
        by_id = {d["id"]: d for d in evidence}
        recommendations = []
        seen = set()
        used_directions = set()
        security_fl = is_federated_learning(input_obj.interest, domain) and any(
            d["id"] in {"fl_membership_inference", "fl_poisoning", "fl_privacy_leak",
                        "fl_byzantine", "fl_secure_aggregation", "fl_adversarial"}
            for d in evidence
        )
        for item in result.recommendations:
            rec = item.model_dump()
            direction = by_id.get(rec["evidence_id"])
            title = rec["topic"].strip()
            normalized = "".join(ch.lower() for ch in title if ch.isalnum())
            if not direction or not title or normalized in seen or rec["evidence_id"] in used_directions:
                continue
            if any(term in title for term in ("核心机制解析", "智能筛选", "多尺度建模", "对象A", "体系X")):
                continue
            if security_fl and (
                not any(term in title for term in ("联邦", "federated", "Federated"))
                or not any(term in f"{title} {rec['innovation_point']}" for term in
                           ("安全", "隐私", "投毒", "后门", "鲁棒", "聚合", "拜占庭", "恶意", "泄露", "攻击", "加密", "security", "privacy", "poison", "backdoor"))
            ):
                continue
            support = supporting_papers_for_topic(title, direction["papers"]) if security_fl else direction["papers"][:3]
            if not support:
                continue
            seen.add(normalized)
            used_directions.add(rec["evidence_id"])
            rec["evidence_papers"] = [{"title": p["title"], "year": p.get("year"), "url": p["url"]}
                                      for p in support[:3]]
            rec["sample_count"] = direction["sample_count"]
            rec["growth_rate"] = 0.0
            rec["trend_summary"] = f"检索样本 {direction['sample_count']} 篇，其中近三年 {direction['recent_sample_count']} 篇；样本不代表全领域总量，年度增长率未验证"
            rec["direction_type"] = rec["direction_type"] if rec["direction_type"] in ("hot", "innovation") else "innovation"
            recommendations.append(self._explain_evidence_scores(rec, direction, evidence, input_obj.interest))
        if recommendations:
            fallback_by_id = {
                item["evidence_id"]: item
                for item in self._evidence_fallback(input_obj, domain)
            }
            for field in ("rationale", "innovation_point"):
                seen_text: set[str] = set()
                for rec in recommendations:
                    normalized_text = "".join(str(rec.get(field) or "").lower().split())
                    if not normalized_text or normalized_text in seen_text:
                        rec[field] = fallback_by_id.get(rec["evidence_id"], {}).get(field, rec.get(field, ""))
                    seen_text.add("".join(str(rec.get(field) or "").lower().split()))
        recommendations.sort(key=lambda r: r["popularity_score"], reverse=True)
        return recommendations[:5]

    def _evidence_fallback(self, input_obj: TopicRecommendInput, domain: str) -> list[dict]:
        """当模型未返回合法 JSON 时，仍从已核验论文生成可展示的保守推荐。

        这里不虚构增长率或论文结论；主题由细分方向定义，证据直接取自该方向
        的 DOI/arXiv 论文。这样单个模型的结构化输出能力不会让整个检索流程失败。
        """
        evidence = input_obj.trend_data.get("direction_evidence", [])
        concrete = {
            "llm_jailbreak": (
                "大模型自适应越狱与提示注入的分层防御研究",
                "在直接提示、检索片段和工具返回三个入口构造攻击，比较拦截率、误拒率与正常任务完成率。",
                ["提示注入", "越狱攻击", "分层防御"],
            ),
            "llm_privacy": (
                "大模型训练数据泄露与成员推断的风险评测",
                "控制训练样本重复度和查询预算，测量成员推断与数据抽取成功率及模型效用损失。",
                ["成员推断", "训练数据抽取", "隐私防护"],
            ),
            "llm_poisoning": (
                "大模型预训练数据投毒与后门触发机制检测",
                "改变投毒比例和触发形式，比较攻击成功率、检测误报率与干净任务性能。",
                ["数据投毒", "模型后门", "异常检测"],
            ),
            "llm_agent_security": (
                "大模型智能体工具调用越权与最小权限控制",
                "构造恶意工具响应和跨步骤指令，评估参数校验、权限隔离对越权率的影响。",
                ["智能体", "工具调用", "权限隔离"],
            ),
            "llm_multimodal_security": (
                "多模态大模型跨模态攻击与一致性防御",
                "分别在图像、文本及混合输入中植入攻击，测量跨模态迁移率和正常样本准确率。",
                ["视觉语言模型", "跨模态攻击", "一致性检测"],
            ),
            "fl_poisoning": (
                "非独立同分布场景下的联邦学习后门攻击检测与鲁棒聚合评测",
                "比较异常更新检测与鲁棒聚合在不同攻击比例、数据异构程度下的防御效果。",
                ["后门攻击", "鲁棒聚合", "非独立同分布数据"],
            ),
            "fl_privacy_leak": (
                "联邦学习梯度反演风险评测与差分隐私保护权衡",
                "量化隐私预算、模型可用性与梯度重建成功率之间的关系。",
                ["梯度反演", "差分隐私", "隐私预算"],
            ),
            "fl_byzantine": (
                "面向恶意客户端的拜占庭鲁棒联邦聚合方法评测",
                "在攻击强度和客户端异构程度变化时比较聚合精度、误报率与收敛速度。",
                ["拜占庭客户端", "恶意更新识别", "鲁棒聚合"],
            ),
            "fl_secure_aggregation": (
                "联邦学习安全聚合协议的隐私、通信开销与容错能力评测",
                "围绕掉线客户端和不同参与规模，验证安全聚合的开销与可用性边界。",
                ["安全聚合", "隐私保护", "客户端掉线"],
            ),
            "fl_adversarial": (
                "非独立同分布条件下联邦学习投毒防御的可复现安全基准",
                "构建统一攻击强度和异构划分，检验防御方法在现实数据偏移下是否稳定。",
                ["安全基准", "投毒攻击", "数据异构"],
            ),
        }
        recommendations: list[dict] = []
        seen_topics: set[str] = set()
        for index, direction in enumerate(evidence[:5]):
            papers = direction.get("papers", [])[:3]
            if not papers:
                continue
            topic, innovation, objects = concrete.get(direction.get("id"), (None, None, None))
            if not topic:
                topic, innovation, objects = self._generic_evidence_topic(direction)
            if is_federated_learning(input_obj.interest, domain) and "大模型" not in input_obj.interest:
                # 通用安全档案中的 poisoning/privacy 不等于大模型安全。
                # 保留规划阶段的具体问题，不能用大模型模板替换联邦学习课题。
                name = str(direction.get("name") or "").strip()
                topic = name if "联邦" in name else f"联邦学习中的{name}"
                text = f"{name} {direction.get('query', '')}".lower()
                if any(term in text for term in ("membership", "成员推", "成员推断")):
                    question = "明确攻击者可见信息，在相同训练预算下比较成员推断效果、模型效用和不同客户端条件，核对已有隐私风险结论的适用范围"
                elif any(term in text for term in ("gradient inversion", "梯度反演")):
                    question = "固定攻击者观察范围与重建预算，改变批量大小和通信信息，比较重建误差与防御代价"
                elif any(term in text for term in ("non-iid", "非独立", "部分参与")):
                    question = "固定攻击比例与训练预算，分别改变数据异质性和客户端参与率，区分两者造成的性能变化"
                elif any(term in text for term in ("convergence", "收敛", "权衡")):
                    question = "记录不同聚合器的逐轮损失、最终准确率与收敛速度，检查鲁棒性改善是否伴随效用或收敛代价"
                elif any(term in text for term in ("指标", "基准", "benchmark", "metric")):
                    question = "在相同种子、数据划分和预算下报告准确率下降与种子间波动，检查指标选择是否改变防御方法排序"
                elif any(term in text for term in ("median", "krum", "中位数", "截断")):
                    question = "对均值、坐标中位数及所选稳健聚合器保持公平训练预算，改变恶意客户端比例以定位各自失效条件"
                else:
                    question = "明确标签翻转攻击者的客户端控制范围，分别改变恶意客户端比例与投毒轮次，检验防御在不同威胁强度下的表现"
                innovation = f"针对“{name}”的待检验切入点：{question}。需先与所列最接近工作核对，不能据检索样本宣称无人研究。"
                objects = [name, "联邦学习客户端", "可复现对照实验"]
            # 主题唯一性守卫：不同细分方向可能命中同一语义档案（例如多个隐私
            # 相关方向都落到同一句 privacy 模板），冲突时退回规划阶段生成的
            # 方向名——每个方向名天然不同，保证推荐不会出现一模一样的主题。
            if topic in seen_topics:
                name = str(direction.get("name") or "").strip()
                if name and name not in seen_topics:
                    topic = name
                    innovation = (
                        f"围绕“{name}”复用所列论文的公开任务和指标设置对照实验，"
                        "通过消融实验定位有效条件与失效边界。"
                    )
                    objects = [name, "论文公开任务", "关键评价指标"]
                else:
                    suffix = 2
                    while f"{topic}（方案{suffix}）" in seen_topics:
                        suffix += 1
                    topic = f"{topic}（方案{suffix}）"
            seen_topics.add(topic)
            lead_paper = next((p for p in papers if p.get("title")), papers[0])
            rec = {
                "topic": topic,
                "rationale": (
                    f"聚焦“{direction.get('name', topic)}”。本轮检索到"
                    f" {direction.get('sample_count', len(papers))} 篇可核验论文，"
                    f"例如《{lead_paper['title']}》；可据此比较已有方法的适用条件。"
                ),
                "innovation_point": innovation,
                "recommended_objects": objects,
                "trend_summary": (
                    f"检索样本 {direction.get('sample_count', len(papers))} 篇，其中近三年 "
                    f"{direction.get('recent_sample_count', 0)} 篇；年度增长率未验证"
                ),
                "difficulty": "medium",
                "novelty": "medium",
                "relevance": "medium",
                "popularity_score": 0,
                "growth_rate": 0.0,
                "evidence_id": direction.get("id", ""),
                "direction_type": "hot" if index < 2 else "innovation",
                "evidence_papers": [
                    {"title": p["title"], "year": p.get("year"), "url": p["url"]}
                    for p in papers
                ],
                "sample_count": direction.get("sample_count", len(papers)),
            }
            recommendations.append(self._explain_evidence_scores(rec, direction, evidence, input_obj.interest))
        recommendations.sort(key=lambda rec: rec["popularity_score"], reverse=True)
        return recommendations

    @staticmethod
    def _explain_evidence_scores(rec: dict, direction: dict, evidence: list[dict], interest: str) -> dict:
        """评分只描述本轮检索样本，附上能从输入与样本复核的口径。"""
        count = max(0, int(direction.get("sample_count") or 0))
        recent = min(count, max(0, int(direction.get("recent_sample_count") or 0)))
        largest = max(1, *(int(item.get("sample_count") or 0) for item in evidence))
        # 60% 为本轮相对样本量，40% 为近三年样本比例；不是领域发文热度。
        rec["popularity_score"] = round(100 * (0.6 * count / largest + 0.4 * recent / max(1, count)))
        rec["popularity_basis"] = (
            f"本轮检索样本量 {count}/{largest}（权重60%），"
            f"近三年 {recent}/{count or 1}（权重40%）；各方向检索词和采样上限不同，"
            "仅供本轮比较，不代表全领域热门度。"
        )
        name = str(direction.get("name") or "")
        interest_text = (interest or "").strip().lower()
        specific = [part for part in ("提示注入", "越狱", "隐私", "泄露", "投毒", "后门", "智能体", "工具", "多模态", "图像", "拜占庭", "梯度", "聚合", "非独立同分布") if part in interest_text]
        hits = [part for part in specific if part in name.lower() or part in str(direction.get("query") or "").lower()]
        rec["relevance"] = "high" if hits else "medium"
        rec["relevance_basis"] = (
            f"与你输入的具体关注点“{'、'.join(hits)}”直接匹配。" if hits else
            f"方向“{name}”属于输入兴趣“{interest}”的细分问题；输入未指定更具体的关注点。"
        )
        rec["novelty_basis"] = "创新度是研究假设，需要与所列论文逐项比较；当前不能从检索样本证明尚无人研究。"
        rec["difficulty_basis"] = "难度为初步估计，需根据可获取的数据、模型规模、算力和复现实验范围确认。"
        return rec

    @staticmethod
    def _generic_evidence_topic(direction: dict) -> tuple[str, str, list[str]]:
        """按细分方向语义生成差异化兜底，避免所有学科套用同一句模板。"""
        name = str(direction.get("name") or "细分研究问题").strip()
        query = str(direction.get("query") or "").lower()
        text = f"{name.lower()} {query}"
        profiles = (
            (("multi-agent", "multi agent", "多智能体"),
             "多智能体大模型协作中的对齐失效与价值漂移检测",
             "构造协作、竞争和恶意智能体混合场景，测量错误传播、权限越界与群体决策偏移。",
             ["多智能体系统", "对齐失效", "价值漂移"]),
            (("multimodal", "vision-language", "多模态"),
             "多模态大模型的跨模态提示注入攻击与一致性防御",
             "比较文本、图像及组合扰动的攻击迁移性，并以攻击成功率和正常样本精度评价防御。",
             ["多模态模型", "跨模态攻击", "一致性检测"]),
            (("prompt injection", "jailbreak", "越狱", "提示注入"),
             "大模型自适应越狱与提示注入攻击的分层防御研究",
             "分别测试输入过滤、系统提示隔离和输出审查，比较攻击成功率、误拒率与正常任务性能。",
             ["提示注入", "越狱攻击", "分层防御"]),
            (("hallucination", "factuality", "幻觉", "事实性"),
             "大模型幻觉的事实性检测与证据一致性校准",
             "比较检索证据约束、自一致性检查和不确定性估计对事实错误率与拒答率的影响。",
             ["事实性检测", "证据一致性", "不确定性校准"]),
            (("privacy", "membership inference", "data extraction", "隐私", "成员推断", "数据泄露"),
             "大模型训练数据泄露风险与隐私保护权衡",
             "在成员推断或训练数据抽取攻击下，量化泄露成功率、模型效用与防护成本之间的关系。",
             ["训练数据泄露", "隐私攻击", "模型效用"]),
            (("backdoor", "poison", "后门", "投毒"),
             "大模型预训练数据投毒与后门触发机制检测",
             "改变投毒比例、触发形式与模型规模，验证检测方法的攻击成功率、误报率和泛化能力。",
             ["模型后门", "数据投毒", "异常检测"]),
            (("adversarial", "robustness", "对抗", "鲁棒"),
             "大模型对抗鲁棒性与安全防御失效边界研究",
             "在不同攻击预算和任务类型下测量性能下降，定位防御方法的有效区间与失效条件。",
             ["对抗攻击", "鲁棒性", "失效边界"]),
            (("watermark", "copyright", "版权", "水印"),
             "大模型水印在微调与压缩后的可靠性及版权验证",
             "测试微调、量化、剪枝和模型提取后水印的可检测性，并评估误判与移除成本。",
             ["模型水印", "版权验证", "模型修改"]),
            (("agent", "tool use", "tool-use", "智能体", "工具调用"),
             "大模型智能体工具调用越权攻击与最小权限控制",
             "构造恶意工具返回和跨步骤提示注入，评估最小权限、参数校验与执行隔离的防护效果。",
             ["智能体", "工具调用", "权限隔离"]),
        )
        for markers, topic, innovation, objects in profiles:
            if any(marker in text for marker in markers):
                return topic, innovation, objects
        # 规划阶段给出的 name 本身已经是依据兴趣产生的细分科研问题；未知类型
        # 直接保留该名称，避免再附加一个跨领域通用标题。
        return (
            name,
            f"围绕“{name}”复用所列论文的公开任务和指标设置对照实验，通过消融实验定位有效条件与失效边界。",
            [name, "论文公开任务", "关键评价指标"],
        )

    def _real_recommend(
        self,
        input_obj: TopicRecommendInput,
        registry: LLMRegistry,
        domain: str = "",
    ) -> list[dict]:
        """真实调用 LLM 生成推荐（两步推理）。

        第一步：从趋势数据锁定热门方向并量化热门度（popularity_score 0-100）。
        第二步：基于热门方向逐条产出推荐，评估难度/创新性/关联性。
        """
        # 构造趋势数据摘要
        analysis = input_obj.analysis
        emerging = analysis.get("emerging", [])[:10]
        stable = analysis.get("stable", [])[:10]
        saturated = analysis.get("saturated", [])[:5]

        def fmt_trend_list(items: list[dict]) -> str:
            lines = []
            for item in items:
                kw = item.get("keyword", "")
                gr = item.get("growth_rate", 0)
                tc = item.get("total_count", 0)
                trend = item.get("trend", [])
                trend_str = ", ".join(f"{t['year']}:{t['count']}" for t in trend)
                lines.append(f"  {kw} (增长率:{gr:.0%}, 总数:{tc}, 趋势: {trend_str})")
            return "\n".join(lines)

        trend_block = (
            f"【新兴方向（增长率>50%）】\n{fmt_trend_list(emerging)}\n\n"
            f"【稳定方向（增长率10%-50%）】\n{fmt_trend_list(stable)}\n\n"
            f"【饱和方向（增长率<10%，避免投入）】\n{fmt_trend_list(saturated)}"
        )

        # 样本论文
        sample_papers = input_obj.trend_data.get("sample_papers", [])[:10]
        paper_block = "\n".join(
            f"  - {p.get('title', 'N/A')} ({p.get('year', '?')})"
            for p in sample_papers
        )

        # 研究领域（领域自适应：决定「具体」体现在哪些名词与指标上）
        from core.knowledge.domain_profile import (
            build_core_topics_hint,
            build_domain_hint,
            build_topic_recommend_hint,
            domain_label as _domain_label_fn,
        )

        _domain = domain or ""
        _domain_label = _domain_label_fn(_domain)
        _domain_hint = build_domain_hint(_domain) or "未指定研究领域，按论文内容自行判断该领域。"
        _topic_hint = build_topic_recommend_hint(_domain)
        # 核心研究主题锚点：把 LLM 拉回所选领域，防止被训练记忆里的高频学科带偏
        _core_topics = build_core_topics_hint(_domain)

        # ===== 第一步：锁定热门方向并量化热门度 =====
        step1_system = (
            "你是科研趋势分析专家。你的任务是【先锁定热门研究方向】：\n"
            "1. 结合用户研究兴趣与趋势数据，找出最值得投入的 5-8 个热门方向；\n"
            f"2. 【领域锚定（最高优先级）】{_core_topics}\n"
            "   - 趋势数据来自所选领域的论文样本，锁定方向时以数据为唯一依据；\n"
            "   - 严禁把方向引向其他学科——除非趋势数据中明确出现该学科的关键词；\n"
            "   - 【反训练偏置】不要因为你的训练记忆中某类学科（如材料科学）论文数量多、"
            "例子顺手，就把方向往那个学科上靠；你给出的每个方向都必须能在上方趋势数据与"
            "论文样本中找到直接证据；\n"
            "3. 描述方向时不要停留在泛泛的标签词（如“机器学习”“纳米”“优化”），\n"
            f"   要落到**具体的研究对象、科学问题或关键技术手段**上（{_topic_hint}），\n"
            "   让每个方向一听就知道它在解决什么具体问题；\n"
            "4. 为每个方向量化热门度 popularity_score（0-100）：\n"
            "   - 增长率高且论文总量大 → 高分（80-100，热门且活跃）\n"
            "   - 增长率高但总量小 → 中高分（60-79，新兴但需注意风险）\n"
            "   - 增长率平稳 → 中分（40-59）\n"
            "   - 饱和/衰退 → 低分（<40，尽量避免）\n"
            "5. 按热门度从高到低排序。\n"
            "只输出方向清单，不要展开推荐主题。"
        )
        step1_prompt = (
            f"用户研究兴趣：{input_obj.interest}\n\n"
            f"用户研究领域：{_domain_label}\n\n"
            f"趋势分析结果：\n{trend_block}\n\n"
            f"近期论文样本：\n{paper_block}"
        )
        hot = registry.structured_output(
            task_type=self.task_type,
            output_schema=HotDirectionSchema,
            system=step1_system,
            prompt=step1_prompt,
            temperature_override=0.7,
        )

        # ===== 第二步：基于热门方向逐条产出完整推荐 =====
        # 防御性去重：step1 偶发返回重复关键词会导致后续推荐全部锚到同一条
        seen_kw: set[str] = set()
        unique_hot = []
        for d in hot.directions:
            k = (d.keyword or "").strip()
            if not k or k.lower() in seen_kw:
                continue
            seen_kw.add(k.lower())
            unique_hot.append(d)
        if len(unique_hot) < len(hot.directions):
            logger.warning(
                "热门方向去重：%d -> %d（LLM 返回了重复关键词）",
                len(hot.directions), len(unique_hot),
            )
            hot.directions = unique_hot
        hot_block = "\n".join(
            f"  - {d.keyword}（popularity={d.popularity_score}，{d.trend_summary}）"
            for d in hot.directions
        )
        # 供 LLM 参考的多样化命名单模式（体现措辞的多样性，而非固定模板）
        # 学科中性：示例必须覆盖多领域措辞，禁止全部来自单一学科（防止 few-shot 把推荐带偏）
        _name_patterns = (
            "主题命名的多样化参考（学科中性，不要照抄其中任何一个，仅体会其措辞差异）：\n"
            "  · “XX 调控 YY 的分子机制：来自单细胞分辨率的证据”——对象 + 机制 + 数据模态\n"
            "  · “基于 XX 框架的 YY 优化：从算法设计到系统落地”——方法 + 目标 + 落点\n"
            "  · “XX 与 YY 的交互效应：一项随机对照研究”——变量 + 变量 + 研究设计\n"
            "  · “面向 ZZ 场景的 XX 架构设计与消融分析”——应用 + 结构 + 验证方式\n"
            "  · “XX 过程中的 YY 动力学：原位观测与建模”——过程 + 对象 + 手段\n"
            "  · “小样本条件下 XX 的主动学习筛选框架”——约束 + 方法 + 对象"
        )
        step2_system = (
            f"你是{_domain_label}领域的研究顾问（{_domain_hint}）。"
            "在上一步锁定的热门方向基础上，产出 3-5 个推荐研究主题。\n\n"
            "【领域锚定（最高优先级）】\n"
            f"{_core_topics}\n"
            "- 所有推荐主题必须属于该领域；若你的第一反应想到了其他学科的例子，丢弃它；\n"
            "- 【反训练偏置】不要因为训练记忆里某学科论文多、示例顺手就往那个学科靠；\n"
            "  每条推荐都必须能从下方趋势数据与论文样本中找到直接证据支撑；\n"
            "- 用户的研究兴趣是第一锚点，领域核心主题是第二锚点，两者共同决定推荐范围。\n\n"
            "【主题命名要求（最重要）】\n"
            f"1. 每条主题的名称必须**整体语感像一位{_domain_label}领域研究者手写的论文题目**，"
            f"用该领域的具体术语（{_topic_hint}）与动作性短语来组织，\n"
            "   不要用“XX的核心动机与痛点”“未来的探索方向”“XX的机遇与挑战”这类评论性套话；\n"
            "2. 5 条推荐之间**句式结构必须明显不同**——禁止共用同一个动宾模板；\n"
            "   合格的差异化 = 不同研究对象 + 不同研究手段 + 不同指标维度的组合；\n"
            "   失败的雷同 = “XX的性能突破研究” / “XX的优化与筛选” / “XX的机制研究”；\n"
            "3. 每条主题从**不同的切入视角**出发，可参考但不限于：\n"
            f"   研究对象创新（{_topic_hint}）、方法与手段、"
            "关键指标维度（各选一项核心指标）、应用场景、多尺度/多角度理解、\n"
            "   数据驱动（主动学习/生成模型/符号回归）；\n"
            "4. 若两个主题落在同一研究对象上，必须用不同的科学问题或手段加以区分，\n"
            "   宁可刀状细分也不要选题近似；\n"
            "5. 主题名控制在 15-30 字，信息密度高，去掉“基于”“关于”等冗余介词；\n"
            "6. 【唯一性硬约束】输出前自查：所有 topic 字符串必须互不相同——\n"
            "   出现两条一模一样（或仅一词之差的近似重复）主题即为不合格，\n"
            "   每条 topic 必须体现该条推荐独有的研究对象/手段差异；\n"
            "   innovation_point 同理，禁止多条推荐共用同一句切入点。\n\n"
            "【其余字段要求】\n"
            "1. rationale 引用趋势数据（增长率、论文数等），并说明该主题**为什么是一个值得做的空缺**；\n"
            "2. innovation_point 给出具体可操作的切入点（具体对象/具体方法/具体指标），\n"
            "   且必须按下方「创新点套路锚定」归类并填写 innovation_pattern 字段；\n"
            f"3. recommended_objects 字段填推荐的具体研究对象（{_topic_hint}），尽量各不相同；\n"
            "4. 评估实现难度 difficulty（easy/medium/hard）、创新度 novelty（low/medium/high）、\n"
            "   与用户研究兴趣的关联度 relevance（low/medium/high）；\n"
            "5. popularity_score 与上一步热门度保持一致（0-100）；\n"
            "6. growth_rate 填该方向对应关键词的年度增长率（浮点，如 0.35）。\n"
            "避免推荐饱和方向（增长率<10%）。\n\n"
            f"{INNOVATION_PATTERNS_PROMPT}\n\n"
            f"{_name_patterns}"
        )
        step2_prompt = (
            f"用户研究兴趣：{input_obj.interest}\n\n"
            f"用户研究领域：{_domain_label}\n\n"
            f"上一步锁定的热门方向（按热门度降序）：\n{hot_block}\n\n"
            f"趋势分析结果：\n{trend_block}\n\n"
            f"近期论文样本：\n{paper_block}"
        )
        result = registry.structured_output(
            task_type=self.task_type,
            output_schema=TopicRecommendationSchema,
            system=step2_system,
            prompt=step2_prompt,
            temperature_override=0.8,
        )

        recs = [item.model_dump() for item in result.recommendations]

        # ===== 主题唯一性守卫：LLM 偶发把多条推荐写成同一主题名 =====
        # 现象：rationale 各不相同（方向其实不同），但 topic/innovation_point 被复制。
        # 处理：按 topic 去重 → 对缺失的槽位做一次补生成调用（附带禁用主题清单）。
        def _topic_key(r: dict) -> str:
            return (r.get("topic") or "").strip().lower()

        unique_recs: list[dict] = []
        seen_topics: set[str] = set()
        for r in recs:
            k = _topic_key(r)
            if not k or k in seen_topics:
                continue
            seen_topics.add(k)
            unique_recs.append(r)
        dup_count = len(recs) - len(unique_recs)
        if dup_count > 0:
            logger.warning(
                "推荐主题去重：%d -> %d（LLM 返回了 %d 条重复主题），触发补生成",
                len(recs), len(unique_recs), dup_count,
            )
            forbidden = "\n".join(f"- {r.get('topic', '')}" for r in unique_recs)
            need = len(recs) - len(unique_recs)
            repair_system = (
                step2_system
                + "\n\n【补生成约束（最高优先级，覆盖前文任何数量要求）】\n"
                "以下主题已被占用，禁止再输出它们或其近似改写：\n"
                f"{forbidden}\n"
                f"只输出 {need} 条全新的推荐，主题名互不相同且与上方已占用主题都不同，"
                "各自来自不同的热门方向；其余字段要求不变。"
            )
            try:
                repair = registry.structured_output(
                    task_type=self.task_type,
                    output_schema=TopicRecommendationSchema,
                    system=repair_system,
                    prompt=step2_prompt,
                    temperature_override=0.9,
                )
                for item in repair.recommendations:
                    d = item.model_dump()
                    k = _topic_key(d)
                    if not k or k in seen_topics:
                        continue
                    seen_topics.add(k)
                    unique_recs.append(d)
                    if len(unique_recs) >= len(recs):
                        break
            except Exception as e:
                logger.warning("重复主题补生成失败（保留去重后的推荐）: %s", e)
        recs = unique_recs

        # 服务端按热门度降序排序（保证 ctx 顺序 = 展示顺序 = 前端选中索引一致）
        recs.sort(key=lambda r: r.get("popularity_score", 0), reverse=True)
        return recs

    @staticmethod
    def _placeholder(input_obj: TopicRecommendInput, domain: str = "") -> list[dict]:
        """占位推荐数据（dry_run 模式用）。领域自适应，不硬编码任何学科名词。"""
        from core.knowledge.domain_profile import domain_label as _dl, build_topic_recommend_hint

        label = _dl(domain)
        obj_hint = build_topic_recommend_hint(domain)
        return [
            {
                "topic": f"{input_obj.interest}的核心机制解析与关键影响因素识别",
                "rationale": "该方向关键词增长率 120%，目前仅 3 篇相关论文，竞争度低",
                "innovation_point": f"围绕{obj_hint}设计系统性的机制验证方案",
                "recommended_objects": [f"{label}·对象A", f"{label}·对象B", f"{label}·对象C"],
                "trend_summary": "核心关键词增长 120%，相关方法词增长 80%",
                "difficulty": "medium",
                "novelty": "high",
                "relevance": "high",
                "popularity_score": 88,
                "growth_rate": 1.2,
            },
            {
                "topic": f"数据驱动的{input_obj.interest}智能筛选与优化",
                "rationale": "machine learning 关键词增长率 150%，是当前最热门交叉方向",
                "innovation_point": "结合主动学习与贝叶斯优化加速筛选迭代",
                "recommended_objects": [f"{label}·候选对象1", f"{label}·候选对象2"],
                "trend_summary": "machine learning 增长 150%，screening 增长 90%",
                "difficulty": "medium",
                "novelty": "high",
                "relevance": "high",
                "popularity_score": 75,
                "growth_rate": 1.5,
            },
            {
                "topic": f"{input_obj.interest}的多尺度建模与实验验证闭环",
                "rationale": "多尺度方法增长稳定 30%，是持续活跃的稳定方向",
                "innovation_point": "构建跨尺度模拟与实验对照的验证闭环",
                "recommended_objects": [f"{label}·体系X", f"{label}·体系Y"],
                "trend_summary": "multiscale 增长 30%，benchmark 增长 25%",
                "difficulty": "hard",
                "novelty": "medium",
                "relevance": "medium",
                "popularity_score": 55,
                "growth_rate": 0.3,
            },
        ]


# ===== TopicSelectHuman =====

class TopicSelectHuman(HumanNode):
    """用户选择推荐主题。

    展示 LLM 推荐的研究主题列表，用户选择一个主题后
    写入 RESEARCH_TOPIC，接入原有 research 流程。

    用户也可以选择自定义主题（输入自由文本）。
    """

    node_type = "topic_select"
    input_schema = NodeInput
    output_schema = TopicSelectOutput
    output_keys = {
        "selected_topic": TOPIC_DISCOVERY_SELECTED_TOPIC,
        "confirmed": RESEARCH_TOPIC,  # 同时写入 RESEARCH_TOPIC
    }

    def _build_input(self, ctx: ExecutionContext) -> NodeInput:
        return NodeInput()

    def _render_prompt(self, ctx: ExecutionContext) -> str:
        recommendations = ctx.get(TOPIC_DISCOVERY_RECOMMENDATIONS, [])
        if not recommendations:
            return (
                "暂无推荐主题。请直接输入你想研究的主题，"
                "系统将进入文献调研流程。"
            )

        lines = ["系统根据领域趋势分析，推荐以下研究主题：\n"]
        for i, rec in enumerate(recommendations):
            lines.append(
                f"  [{i+1}] {rec.get('topic', 'N/A')}\n"
                f"      理由：{rec.get('rationale', 'N/A')}\n"
                f"      创新点：{rec.get('innovation_point', 'N/A')}\n"
                f"      推荐研究对象：{', '.join(rec.get('recommended_objects', rec.get('recommended_materials', [])))}\n"
                f"      难度：{rec.get('difficulty', 'N/A')} | "
                f"创新度：{rec.get('novelty', 'N/A')}\n"
            )
        lines.append(
            "\n请选择：\n"
            "  - 输入数字（如 1）选择对应推荐主题\n"
            "  - 或输入自定义主题（直接输入文本）\n"
            "  - 输入 'ok' 选择第 1 个推荐"
        )
        return "\n".join(lines)

    def _build_output_from_response(
        self, response: HumanResponse, ctx: ExecutionContext
    ) -> Optional[NodeOutput]:
        recommendations = ctx.get(TOPIC_DISCOVERY_RECOMMENDATIONS, [])
        text = (response.text or "").strip()

        if not text or text.lower() in ("ok", "确认", "y", "yes"):
            # 默认选择第 1 个推荐
            if recommendations:
                selected = recommendations[0].get("topic", "")
            else:
                selected = ""
            return TopicSelectOutput(selected_topic=selected, confirmed=True)

        # 数字选择
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(recommendations):
                selected = recommendations[idx].get("topic", "")
                return TopicSelectOutput(selected_topic=selected, confirmed=True)

        # 自定义主题
        return TopicSelectOutput(selected_topic=text, confirmed=True)
