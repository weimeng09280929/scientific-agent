"""ideation 阶段 Agent / Human 节点实现。

节点拓扑（借鉴 LangGraph 人在回路）：
    BrainstormAgent（基于调研产出 + 交叉验证报告，针对 gaps/conflicts 生成 3-5 个候选思路）
    → IdeaDiscussHuman（用户交互式探讨：可否决/修正/补充思路）
    → StageCheckpoint
    → IdeaValidateAgent（三维度评估：可行性/新颖性/贡献度）
    → ClaimDraftAgent（从验证通过思路派生 draft Claim，status=DRAFT）

执行模式：
- dry_run=True  ：用占位数据返回，不调用 LLM（默认，验证架构用）
- dry_run=False ：真实调用配置的 LLM（glm-5.3-flash），真实入库 Idea/Claim 实体
"""
from __future__ import annotations

import logging
import json
from typing import Optional

from pydantic import BaseModel, Field

from core.knowledge import Claim, ClaimStatus, Idea, KnowledgeStore, Paper
from core.knowledge.innovation_playbook import INNOVATION_PATTERNS_PROMPT
from core.knowledge.writing_methodology import build_stage_writing_hint
from core.llm import LLMRegistry
from core.orchestration.context import ContextKey, ExecutionContext
from core.orchestration.node import (
    AgentNode,
    HumanNode,
    HumanResponse,
    NodeInput,
    NodeOutput,
    NodeResult,
    NodeStatus,
)

from stages.common import (
    DRY_RUN,
    IDEATION_DISCUSSION_NOTES,
    IDEATION_DRAFT_CLAIM_IDS,
    IDEATION_IDEA_IDS,
    IDEATION_VALIDATED_IDEA_IDS,
    KNOWLEDGE_STORE,
    LLM_REGISTRY,
    RESEARCH_CROSS_VALIDATION_REPORT,
    RESEARCH_GAP_REPORT,
    RESEARCH_PAPER_IDS,
    RESEARCH_TOPIC,
)
from stages.ideation.io_schema import (
    BrainstormInput,
    BrainstormOutput,
    ClaimDraftInput,
    ClaimDraftOutput,
    IdeaDiscussOutput,
    IdeaValidateInput,
    IdeaValidateOutput,
)

logger = logging.getLogger(__name__)


# ===== 结构化输出 Schema =====

class IdeaDraftItem(BaseModel):
    """单条思路草稿。"""

    text: str = Field(description="思路描述（一段话）")
    constraints: list[str] = Field(default_factory=list, description="思路约束")
    source_paper_ids: list[str] = Field(default_factory=list, description="来源 Paper ID")
    novelty_score: float = Field(
        default=0.5, ge=0.0, le=1.0, description="新颖性评分 0~1（与已有文献/共识的差异程度）"
    )
    feasibility_score: float = Field(
        default=0.5, ge=0.0, le=1.0, description="可行性评分 0~1（可落地、证据充分、约束可满足程度）"
    )
    gap_relevance_score: float = Field(
        default=0.5, ge=0.0, le=1.0, description="缺口关联度评分 0~1（与 Research Gap / 未解决冲突的匹配程度）"
    )
    innovation_pattern: str = Field(
        default="",
        description="创新套路标签：application / modification / fusion / comparison / theory",
    )
    testability_score: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="贡献可检验性 0~1（读者能否据此判断这条思路做到没有；贡献越具体可验证越高）",
    )


class BrainstormSchema(BaseModel):
    """思路生成输出 schema。"""

    ideas: list[IdeaDraftItem] = Field(description="3-5 个候选思路")


class IdeaValidationSchema(BaseModel):
    """思路三维度评估 schema。"""

    feasibility: float = Field(ge=0, le=1, description="可行性 0~1")
    novelty: float = Field(ge=0, le=1, description="新颖性 0~1")
    contribution: float = Field(ge=0, le=1, description="贡献度 0~1")
    reason: str = Field(description="评估理由")


class ClaimDraftItem(BaseModel):
    """单条 Claim 草稿。"""

    statement: str = Field(description="一句话可验证陈述")


class ClaimDraftListSchema(BaseModel):
    """Claim 草稿列表 schema。"""

    claims: list[ClaimDraftItem] = Field(description="1-2 个可验证 Claim")


# ===== BrainstormAgent =====

class BrainstormAgent(AgentNode):
    """思路生成 Agent。

    借鉴 LangGraph 人在回路：Agent 先基于调研可信证据提出候选方案，再交由
    用户审核/修正。思路生成聚焦于：
    - 针对 cross_validation_report.gaps（证据缺口）提出 hypothesis
    - 针对 cross_validation_report.conflicts（未解决冲突）提出调和假设
    - 基于 consensus（共识）提出扩展方向
    """

    node_type = "ideation_brainstorm"
    task_type = "ideation_brainstorm"
    input_schema = BrainstormInput
    output_schema = BrainstormOutput
    output_keys = {
        "idea_ids": IDEATION_IDEA_IDS,
    }

    def _build_input(self, ctx: ExecutionContext) -> BrainstormInput:
        return BrainstormInput(
            paper_ids=ctx.get(RESEARCH_PAPER_IDS, []),
            cross_validation_report=ctx.get(RESEARCH_CROSS_VALIDATION_REPORT, {}),
            gap_report=ctx.get(RESEARCH_GAP_REPORT, []),
        )

    def _execute(self, input_obj: BrainstormInput, ctx: ExecutionContext) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        dry_run: bool = ctx.get(DRY_RUN, True)

        topic = ctx.get(RESEARCH_TOPIC, "") or ""
        report = input_obj.cross_validation_report or {}
        conflicts = report.get("conflicts", []) or []
        consensus = report.get("consensus", []) or []
        paper_ids = input_obj.paper_ids or []

        # 研究缺口（Task 3 结构化优先，回退旧字符串 gaps）：
        # gap_report: [{statement, gap_type, priority, evidence, ...}]
        gap_report = input_obj.gap_report or []
        gaps: list = []
        if gap_report:
            # 结构化 Gap：按优先级升序取 statement（带类型标记，提升 prompt 质量）
            sorted_gaps = sorted(gap_report, key=lambda g: g.get("priority", 5))
            gaps = [
                f"[{g.get('gap_type', 'unexplored')}|P{g.get('priority', 3)}] "
                f"{g.get('statement', '')}"
                for g in sorted_gaps[:8]
            ]
        else:
            # 回退：cross_validate 的字符串 gaps
            gaps = report.get("gaps", []) or []

        # 加载 paper 摘要作为 prompt 素材
        paper_summaries: list[str] = []
        if store is not None:
            for pid in paper_ids[:8]:  # 带 ID 的摘要，避免模型无法对应引用
                try:
                    p = store.get_paper(pid)
                    paper_summaries.append(
                        f"- paper_id={pid}; {p.title} ({p.year}): {(p.abstract or '')[:700]}"
                    )
                except Exception:
                    pass

        # 思路草稿统一 8 元组格式（与真实调用分支的 IdeaDraftItem 字段对应）：
        # (text, constraints, src_pids, novelty, feasibility, gap_relevance,
        #  innovation_pattern, testability)
        idea_drafts: list[tuple] = []

        if not dry_run and registry is not None:
            try:
                result = registry.structured_output(
                    task_type=self.task_type,
                    output_schema=BrainstormSchema,
                    system=(
                        "你是科研思路生成助手。基于调研的交叉验证报告与文献证据，"
                        "针对证据缺口（gaps）与未解决冲突（conflicts）提出 3-5 个可验证的研究假设。"
                        "每个假设给出：思路描述、约束条件、来源 Paper ID（从给定列表选取）。"
                        "思路应当：可落地、相对已有工作有差异、有潜在学术贡献。\n"
                        "同时为每个假设输出四维可验证性评分（各 0~1，保留两位小数）：\n"
                        "  - novelty_score 新颖性：与已有文献结论/共识的差异程度\n"
                        "  - feasibility_score 可行性：可落地、证据充分、约束可满足的程度\n"
                        "  - gap_relevance_score 缺口关联度：与 Research Gap / 未解决冲突的匹配程度\n"
                        "  - testability_score 贡献可检验性：贡献是否具体到「读者能判断你做到没有」\n"
                        "    （思路描述里应包含可检验的贡献点：用什么数据/指标/对比能证明它成立）\n"
                        "评分须与思路内容一致，不要全部给高分。"
                        "所有思路必须紧扣给定的研究主题，不得偏离。\n\n"
                        f"{INNOVATION_PATTERNS_PROMPT}\n\n"
                        "注意：套路分类仅用于内部约束你的思考路径（innovation_pattern 字段照常填写），"
                        "思路描述文本本身不要出现套路名称或分类标签，直接呈现具体研究内容。\n\n"
                        f"{build_stage_writing_hint('ideation')}"
                    ),
                    prompt=(
                        f"研究主题：{topic}\n\n"
                        f"文献证据：\n" + "\n".join(paper_summaries) + "\n\n"
                        f"gaps: {[str(g)[:650] for g in gaps[:8]]}\n"
                        f"conflicts: {[json.dumps(c, ensure_ascii=False)[:900] for c in conflicts[:5]]}\n"
                        f"consensus: {[json.dumps(c, ensure_ascii=False)[:650] for c in consensus[:6]]}\n"
                        f"可用 paper_ids: {paper_ids}"
                    ),
                )
                for d in result.ideas:
                    idea_drafts.append((
                        d.text, d.constraints, d.source_paper_ids,
                        d.novelty_score, d.feasibility_score, d.gap_relevance_score,
                        getattr(d, "innovation_pattern", "") or "",
                        float(getattr(d, "testability_score", 0.5) or 0.5),
                    ))
            except Exception as e:
                from core.llm.base import LLMTruncatedError
                reason = "模型输出预算耗尽，JSON 被截断" if isinstance(e, LLMTruncatedError) else f"模型未返回符合要求的研究思路（{type(e).__name__}）"
                return NodeResult(status=NodeStatus.FAILED, error=reason, summary=f"{reason}；已有 {len(paper_ids)} 篇文献已保留，可从思路生成重试，无需重新检索")
        else:
            if not dry_run:
                return NodeResult(status=NodeStatus.FAILED, error="模型未连接", summary="未生成研究思路，请先配置可用模型")
            idea_drafts = self._placeholder_drafts(topic, gaps, conflicts, consensus, paper_ids)

        # 兜底：至少 1 个思路（统一 8 元组格式，位置 3/4/5/7 必须是数值）
        if not idea_drafts:
            if not dry_run:
                return NodeResult(status=NodeStatus.FAILED, error="模型未返回研究思路", summary="没有可采纳的思路，请补充文献或缩小研究问题后重试")
            idea_drafts.append((
                f"针对主题「{topic[:60]}」基于 {len(paper_ids)} 篇调研论文的扩展研究方向。",
                ["需进一步文献确认新颖性"],
                paper_ids[:2],
                0.5, 0.5, 0.5, "", 0.5,
            ))

        def _safe_score(v, default: float = 0.5) -> float:
            """防御性评分转换：格式漂移（如拿到 list/None）时回退默认值，不让节点崩溃。"""
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        # 持久化 Idea 实体
        idea_ids: list[str] = []
        ideas_meta: list[dict] = []
        for draft in idea_drafts:
            text, constraints, src_pids = draft[0], draft[1], draft[2]
            n_score = _safe_score(draft[3]) if len(draft) > 3 else 0.5
            f_score = _safe_score(draft[4]) if len(draft) > 4 else 0.5
            g_score = _safe_score(draft[5]) if len(draft) > 5 else 0.5
            pattern = (draft[6] or "") if len(draft) > 6 else ""
            t_score = _safe_score(draft[7]) if len(draft) > 7 else 0.5
            idea_id = KnowledgeStore.new_id()
            idea = Idea(
                idea_id=idea_id,
                text=text,
                constraints=constraints,
                source_paper_ids=src_pids,
                status="draft",
                created_by="agent",
                source_stage="ideation",
                validation_notes={
                    "novelty_score": round(min(1.0, max(0.0, n_score)), 2),
                    "feasibility_score": round(min(1.0, max(0.0, f_score)), 2),
                    "gap_relevance_score": round(min(1.0, max(0.0, g_score)), 2),
                    "testability_score": round(min(1.0, max(0.0, t_score)), 2),
                    "overall_score": round(
                        0.4 * min(1.0, max(0.0, n_score))
                        + 0.3 * min(1.0, max(0.0, f_score))
                        + 0.3 * min(1.0, max(0.0, g_score)),
                        2,
                    ),
                    "innovation_pattern": pattern,
                },
            )
            if store is not None:
                try:
                    store.save_idea(idea)
                except Exception as e:
                    logger.warning("Idea 入库失败: %s", e)
            idea_ids.append(idea_id)
            ideas_meta.append({
                "idea_id": idea_id,
                "text": text,
                "constraints": constraints,
                "source_paper_ids": src_pids,
                "validation_notes": idea.validation_notes,
                "innovation_pattern": pattern,
            })

        output = BrainstormOutput(idea_ids=idea_ids, ideas_meta=ideas_meta)
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=(
                f"生成 {len(idea_ids)} 个候选思路"
                f"（gaps={len(gaps)}, conflicts={len(conflicts)}, consensus={len(consensus)}）"
            ),
        )

    @staticmethod
    def _placeholder_drafts(
        topic: str, gaps: list, conflicts: list, consensus: list, paper_ids: list
    ) -> list[tuple]:
        """占位思路（真实调用失败时兜底）。

        统一 8 元组格式：(text, constraints, src_pids, novelty, feasibility,
        gap_relevance, innovation_pattern, testability)，与真实调用分支的
        IdeaDraftItem 字段一一对应；任何位置错位都会让持久化循环拿错类型。
        """
        drafts: list[tuple] = []
        if gaps:
            gap0 = gaps[0] if isinstance(gaps[0], str) else str(gaps[0])
            drafts.append((
                f"针对主题「{topic[:50]}」的证据缺口「{gap0}」提出假设："
                f"设计新方法填补该缺口，并设计对照实验验证其有效性。",
                ["需在现有公开数据集上可复现", "方法改动应可消融分析"],
                paper_ids[:2],
                0.6, 0.5, 0.9, "", 0.5,
            ))
        if conflicts:
            c0 = conflicts[0]
            cclaim = c0.get("claim", "某冲突") if isinstance(c0, dict) else str(c0)
            drafts.append((
                f"针对未解决冲突「{cclaim}」提出调和假设：设计统一实验框架，"
                "在相同评测协议下重新检验冲突双方的结论。",
                ["需严格控制变量", "评测协议须公开可复现"],
                paper_ids[:2],
                0.6, 0.5, 0.8, "", 0.5,
            ))
        if consensus:
            cons0 = consensus[0] if isinstance(consensus[0], str) else str(consensus[0])
            drafts.append((
                f"基于共识「{cons0}」的扩展假设：在已有共识基础上引入新模块，"
                "验证是否能进一步提升性能。",
                ["新模块须有理论依据", "不得破坏原共识成立条件"],
                paper_ids[:3],
                0.5, 0.7, 0.6, "", 0.5,
            ))
        while len(drafts) < 3:
            idx = len(drafts)
            drafts.append((
                f"针对主题「{topic[:50]}」的候选思路 {idx + 1}："
                f"基于 {len(paper_ids)} 篇调研论文的扩展研究方向。",
                ["需进一步文献确认新颖性"],
                paper_ids[:2],
                0.4 + 0.1 * idx, 0.5, 0.5, "", 0.5,
            ))
        return drafts


# ===== IdeaDiscussHuman =====

class IdeaDiscussHuman(HumanNode):
    """与用户交互式探讨思路（借鉴 LangGraph 人在回路）。

    呈现每个候选思路的 text 与 constraints，用户可：
    - 'ok' 确认全部思路
    - 'reject: <序号>' 否决某个思路（1-based）
    - 'add: <思路描述>' 补充新思路
    - 自由文本作为讨论笔记
    """

    node_type = "ideation_discuss"
    input_schema = NodeInput
    output_schema = IdeaDiscussOutput
    output_keys = {
        "discussion_notes": IDEATION_DISCUSSION_NOTES,
        "idea_ids": IDEATION_IDEA_IDS,
    }

    def _build_input(self, ctx: ExecutionContext) -> NodeInput:
        return NodeInput()

    def _fetch_ideas(self, ctx: ExecutionContext) -> list[Idea]:
        """从 KnowledgeStore 按 id 读取思路正文。"""
        idea_ids = ctx.get(IDEATION_IDEA_IDS, [])
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        if not idea_ids or store is None:
            return []
        ideas: list[Idea] = []
        for iid in idea_ids:
            try:
                ideas.append(store.get_idea(iid))
            except Exception:
                pass
        return ideas

    def _render_prompt(self, ctx: ExecutionContext) -> str:
        topic = ctx.get(RESEARCH_TOPIC, "") or ""
        idea_ids = ctx.get(IDEATION_IDEA_IDS, [])
        ideas = self._fetch_ideas(ctx)
        idea_map = {idea.idea_id: idea for idea in ideas}
        lines: list[str] = []
        for i, iid in enumerate(idea_ids):
            idea = idea_map.get(iid)
            if idea is not None:
                cons = idea.constraints or []
                cons_str = ("；约束：" + " / ".join(cons)) if cons else ""
                lines.append(f"  {i + 1}. {idea.text}{cons_str}")
            else:
                lines.append(f"  {i + 1}. [idea_id={iid}]（正文读取失败）")
        ideas_block = "\n".join(lines) if lines else "  （无候选思路）"
        topic_line = f"研究主题：{topic}\n" if topic else ""
        return (
            f"{topic_line}已生成 {len(idea_ids)} 个候选思路：\n{ideas_block}\n\n"
            "请与系统交互式探讨（可多行输入）：\n"
            "  - 输入 'ok' 确认全部思路\n"
            "  - 输入 'reject: <序号>' 否决某个思路（如 'reject: 2'）\n"
            "  - 输入 'add: <思路描述>' 补充新思路（如 'add: 探索轻量化变体'）\n"
            "  - 其他文本将作为讨论笔记保留"
        )

    def _build_output_from_response(
        self, response: HumanResponse, ctx: ExecutionContext
    ) -> Optional[NodeOutput]:
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        paper_ids = ctx.get(RESEARCH_PAPER_IDS, [])
        idea_ids = list(ctx.get(IDEATION_IDEA_IDS, []))

        text = (response.text or "").strip()
        notes_parts: list[str] = []
        reject_indices: set[int] = set()

        if not text or text.lower() in ("ok", "确认", "y", "yes"):
            notes_parts.append("用户确认全部思路。")
        else:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                low = line.lower()
                if low.startswith("reject:"):
                    body = line[len("reject:"):].strip()
                    try:
                        idx = int(body)
                        if 1 <= idx <= len(idea_ids):
                            reject_indices.add(idx)
                            notes_parts.append(f"用户否决了思路 #{idx}。")
                        else:
                            notes_parts.append(f"（reject 序号越界：{body}）")
                    except ValueError:
                        notes_parts.append(f"（reject 格式错误：{body}）")
                elif low.startswith("add:"):
                    desc = line[len("add:"):].strip()
                    if desc:
                        new_id = KnowledgeStore.new_id()
                        new_idea = Idea(
                            idea_id=new_id,
                            text=desc,
                            constraints=[],
                            source_paper_ids=paper_ids,
                            status="draft",
                            created_by="user",
                            source_stage="ideation",
                        )
                        if store is not None:
                            try:
                                store.save_idea(new_idea)
                            except Exception:
                                pass
                        idea_ids.append(new_id)
                        notes_parts.append(f"用户补充新思路：{desc}")
                else:
                    notes_parts.append(line)

        for idx in sorted(reject_indices, reverse=True):
            try:
                removed = idea_ids.pop(idx - 1)
                if store is not None:
                    try:
                        idea = store.get_idea(removed)
                        idea.status = "rejected"
                        store.save_idea(idea)
                    except Exception:
                        pass
            except IndexError:
                pass

        discussion_notes = "\n".join(notes_parts)
        return IdeaDiscussOutput(
            discussion_notes=discussion_notes,
            idea_ids=idea_ids,
        )


# ===== IdeaValidateAgent =====

class IdeaValidateAgent(AgentNode):
    """思路验证 Agent。

    对用户讨论后保留的思路做三维度量化评估：
    - feasibility（可行性）：是否有可落地的方法路径与资源
    - novelty（新颖性）：相对已有工作的差异度
    - contribution（贡献度）：潜在学术/工程价值

    三维度均 >= DEFAULT_THRESHOLD 视为通过。
    """

    node_type = "ideation_validate"
    task_type = "ideation_validate"
    input_schema = IdeaValidateInput
    output_schema = IdeaValidateOutput
    output_keys = {
        "validated_idea_ids": IDEATION_VALIDATED_IDEA_IDS,
    }

    DEFAULT_THRESHOLD = 0.5

    def _build_input(self, ctx: ExecutionContext) -> IdeaValidateInput:
        return IdeaValidateInput(
            idea_ids=ctx.get(IDEATION_IDEA_IDS, []),
            discussion_notes=ctx.get(IDEATION_DISCUSSION_NOTES, ""),
        )

    def _execute(
        self, input_obj: IdeaValidateInput, ctx: ExecutionContext
    ) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        dry_run: bool = ctx.get(DRY_RUN, True)
        topic = ctx.get(RESEARCH_TOPIC, "") or ""

        validation_reports: list[dict] = []
        validated_idea_ids: list[str] = []
        reproduction = any(marker in topic for marker in ("真实验收", "流程验收", "系统验收", "复现实验", "复现研究"))

        for idea_id in input_obj.idea_ids:
            # 读取思路正文
            idea_text = f"思路 {idea_id}"
            idea_constraints: list[str] = []
            if store is not None:
                try:
                    idea = store.get_idea(idea_id)
                    idea_text = idea.text
                    idea_constraints = idea.constraints
                except Exception:
                    pass

            if not dry_run and registry is not None:
                try:
                    resp = registry.structured_output(
                        task_type=self.task_type,
                        output_schema=IdeaValidationSchema,
                        system=(
                            "你是科研思路评审助手。从可行性、新颖性、贡献度三个维度"
                            "评估研究思路，各维度给出 0~1 的分数与理由。"
                            "评分依据：可行性看方法路径与资源是否就绪；"
                            "新颖性看相对已有工作的差异度；贡献度看潜在学术/工程价值。"
                            "评估须紧扣研究主题。"
                            + ("本项目明确是复现/系统验收：新颖性必须如实打分，不要为流程通过而打高分；贡献度按可复算性与工程验证价值评价，不要求提出新算法。" if reproduction else "")
                        ),
                        prompt=(
                            f"研究主题：{topic}\n"
                            f"思路：{idea_text}\n"
                            f"约束：{idea_constraints}\n"
                            f"讨论笔记：{input_obj.discussion_notes}"
                        ),
                    )
                    feasibility = float(resp.feasibility)
                    novelty = float(resp.novelty)
                    contribution = float(resp.contribution)
                    reason = resp.reason
                except Exception as e:
                    return NodeResult(status=NodeStatus.FAILED, error=f"思路评估失败：{type(e).__name__}", summary="思路尚未评估，未默认通过；已有想法保留，可重试")
            else:
                if not dry_run:
                    return NodeResult(status=NodeStatus.FAILED, error="模型未连接", summary="思路尚未评估，未默认通过")
                feasibility, novelty, contribution, reason = 0.7, 0.6, 0.5, "占位评估：方法路径清晰、与已有工作有差异、具一定贡献度。"

            passed = (
                feasibility >= self.DEFAULT_THRESHOLD
                and (reproduction or novelty >= self.DEFAULT_THRESHOLD)
                and (reproduction or contribution >= self.DEFAULT_THRESHOLD)
            )
            report = {
                "idea_id": idea_id,
                "feasibility": feasibility,
                "novelty": novelty,
                "contribution": contribution,
                "passed": passed,
                "reason": reason,
                "validation_mode": "reproduction" if reproduction else "research",
                "novelty_threshold_applied": not reproduction,
            }
            validation_reports.append(report)
            if passed:
                validated_idea_ids.append(idea_id)
            if store is not None:
                idea = store.get_idea(idea_id)
                if idea is not None:
                    idea.status = "validated" if passed else "draft"
                    idea.validation_notes = {**(idea.validation_notes or {}), **report}
                    store.save_idea(idea)

        ctx.set(IDEATION_VALIDATED_IDEA_IDS, validated_idea_ids)
        ctx.set(ContextKey[list]('ideation.validation_reports'), validation_reports)
        if store is not None:
            store.save_kv("ideation.validation_reports", validation_reports)

        output = IdeaValidateOutput(
            validated_idea_ids=validated_idea_ids,
            validation_reports=validation_reports,
        )
        summary = (
            f"思路验证完成：通过 {len(validated_idea_ids)}/{len(input_obj.idea_ids)}"
            f"（阈值 {self.DEFAULT_THRESHOLD}）"
            + ("；复现/验收模式检查执行可行性，学术评分仅记录、不视为已证明创新" if reproduction else "")
        )
        return NodeResult(
            status=NodeStatus.SUCCESS if validated_idea_ids else NodeStatus.FAILED,
            output=output,
            error=None if validated_idea_ids else "没有通过评审的研究思路",
            summary=summary if validated_idea_ids else summary + "；阶段未完成，请修改思路后重试。" + "；".join(r["reason"] for r in validation_reports)[:600],
        )


# ===== ClaimDraftAgent =====

class ClaimDraftAgent(AgentNode):
    """Claim 草稿生成 Agent。

    从验证通过的 Idea 派生 draft Claim（每个 Idea 派生 1-2 个）。
    - statement 为一句话可验证陈述
    - status=ClaimStatus.DRAFT，evidence_refs=[]（草稿阶段无证据）
    - 派生关系记录到 KnowledgeStore（source_idea_id）
    """

    node_type = "ideation_claim_draft"
    task_type = "design_claim_extract"
    input_schema = ClaimDraftInput
    output_schema = ClaimDraftOutput
    output_keys = {
        "draft_claim_ids": IDEATION_DRAFT_CLAIM_IDS,
    }

    def _build_input(self, ctx: ExecutionContext) -> ClaimDraftInput:
        return ClaimDraftInput(
            validated_idea_ids=ctx.get(IDEATION_VALIDATED_IDEA_IDS, []),
        )

    def _execute(self, input_obj: ClaimDraftInput, ctx: ExecutionContext) -> NodeResult:
        registry: Optional[LLMRegistry] = ctx.get(LLM_REGISTRY)
        store: Optional[KnowledgeStore] = ctx.get(KNOWLEDGE_STORE)
        dry_run: bool = ctx.get(DRY_RUN, True)
        topic = ctx.get(RESEARCH_TOPIC, "") or ""

        draft_claim_ids: list[str] = []
        claims_meta: list[dict] = []

        for i, idea_id in enumerate(input_obj.validated_idea_ids):
            # 读取思路正文
            idea_text = f"思路 {idea_id}"
            idea_constraints: list[str] = []
            if store is not None:
                try:
                    idea = store.get_idea(idea_id)
                    idea_text = idea.text
                    idea_constraints = idea.constraints
                except Exception:
                    pass

            # 生成 1-2 个 Claim
            if not dry_run and registry is not None:
                try:
                    resp = registry.structured_output(
                        task_type=self.task_type,
                        output_schema=ClaimDraftListSchema,
                        system=(
                            "你是科研论点提炼助手。从研究思路中派生 1-2 个可验证的 Claim，"
                            "每个 Claim 用一句话陈述，须可被实验或证据验证/反驳。"
                            "Claim 应当具体、可量化、可证伪，且紧扣研究主题。"
                        ),
                        prompt=(
                            f"研究主题：{topic}\n"
                            f"思路：{idea_text}\n"
                            f"约束：{idea_constraints}"
                        ),
                    )
                    claim_statements = [c.statement for c in resp.claims]
                except Exception as e:
                    return NodeResult(status=NodeStatus.FAILED, error=f"论断草稿整理失败：{type(e).__name__}", summary="尚未整理出论断，未编造优于基线的结论；请重试")
            else:
                if not dry_run:
                    return NodeResult(status=NodeStatus.FAILED, error="模型未连接", summary="未生成论断草稿")
                # 占位：第一个思路派生 2 个 Claim，其余 1 个（差异化模板避免测试误判为重复）
                claim_count = 2 if i == 0 else 1
                _claim_templates = [
                    "所提方法在标准评测协议下优于现有 baseline。",
                    "在公开数据集上的关键指标提升至少 10%，统计显著（p<0.05）。",
                    "相比最强基线，推理时延降低 30% 以上。",
                ]
                claim_statements = [
                    f"针对主题「{topic[:40]}」，基于「{idea_text[:40]}」的可验证论断 {j + 1}：{_claim_templates[j % len(_claim_templates)]}"
                    for j in range(claim_count)
                ]

            for statement in claim_statements:
                claim_id = KnowledgeStore.new_id()
                claim = Claim(
                    claim_id=claim_id,
                    statement=statement,
                    source_idea_id=idea_id,
                    evidence_refs=[],  # 草稿阶段无证据
                    status=ClaimStatus.DRAFT,
                )
                if store is not None:
                    try:
                        store.save_claim(claim)
                    except Exception as e:
                        logger.warning("Claim 入库失败: %s", e)
                draft_claim_ids.append(claim_id)
                claims_meta.append({
                    "claim_id": claim_id,
                    "statement": statement,
                    "source_idea_id": idea_id,
                })

        output = ClaimDraftOutput(
            draft_claim_ids=draft_claim_ids,
            claims_meta=claims_meta,
        )
        return NodeResult(
            status=NodeStatus.SUCCESS,
            output=output,
            summary=(
                f"从 {len(input_obj.validated_idea_ids)} 个验证思路派生 "
                f"{len(draft_claim_ids)} 个 draft Claim"
            ),
        )
