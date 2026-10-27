"""Method-aware plans. Uses existing chat requests, suggestions, items and versions."""

from __future__ import annotations
import json
import re

from core.research_scope import ensure_plan_scope
from core.workspace.plan_schema import PlanRequest, ResearchPlan
from core.workspace.schema import GuideDraft, SuggestedItem, ActionProposal
from core.workspace.service import WorkspaceError

METHODS = {
    "computational": "计算研究",
    "theoretical": "理论研究",
    "experimental": "实验研究设计",
}
SECTIONS = {
    "objective": "研究目标与明确问题",
    "related_work": "相关工作与实际引用依据",
    "hypothesis": "拟研究的差异或待验证设想",
    "conditions": "研究对象、数据或材料条件",
    "method": "方法与技术路线",
    "validation": "比较、验证或论证设计",
    "feasibility": "时间、资源与可行性",
    "limitations": "局限、未确认假设和待讨论事项",
    "references": "参考来源",
}
DETAILS = {
    "computational": {
        "data_task": "数据与任务",
        "baselines": "基线",
        "comparisons": "公平比较条件",
        "budget": "预算与计算条件",
        "evaluation": "评估方法",
        "reproducibility": "重复性与复现条件",
    },
    "theoretical": {
        "definitions": "定义",
        "assumptions": "假设",
        "propositions": "待论证命题",
        "derivation_route": "推导路线",
        "proof_obligations": "证明义务",
        "counterexamples": "反例",
        "boundaries": "边界条件",
        "related_theory": "与已有理论的关系",
    },
    "experimental": {
        "research_object": "研究对象",
        "variables": "变量",
        "controls": "对照",
        "measurements": "测量方法",
        "repetition_error": "重复与误差",
        "resource_needs": "资源需求",
        "professional_review": "专业审核事项",
    },
}
BASES = {
    "literature": "文献依据（仅提供范围）",
    "user": "用户提供",
    "proposal": "系统建议（未验证）",
    "unverified": "待核实／待确定",
}


def requested(text, request=None):
    if re.search(
        r"(?:不要|别|不需要|不必|暂不|不(?=生成|制定|修订|修改|设计|写)).{0,8}(?:生成|制定|修订|修改|设计|写).{0,8}方案",
        text,
    ):
        return None
    from core.workspace.guidance_policy import is_paused

    if is_paused(text):
        return None
    if request is not None:
        return request
    if re.search(
        r"(?:是否|要不要|怎么|如何|为什么).{0,12}(?:生成|制定|修订|修改|设计).{0,8}方案",
        text,
    ):
        return None
    if re.search(
        r"(?:生成|制定|起草|编写|修订|修改|完善|设计|写一份).{0,20}(?:研究|方法|理论|计算|实验)?方案",
        text,
    ):
        return PlanRequest()
    return None


def method_hint(text):
    # No domain-to-method mapping: physics could use any of the three.
    explicit = [
        (kind, word)
        for kind, word in [
            ("computational", "计算研究"),
            ("theoretical", "理论研究"),
            ("experimental", "实验研究设计"),
        ]
        if word in text
    ]
    if len(explicit) == 1:
        return explicit[0][0], "用户明确指定" + explicit[0][1]
    guesses = [
        kind
        for kind, pattern in [
            ("theoretical", r"证明|命题|定理|推导"),
            ("computational", r"训练|算法|仿真|数据集|基线"),
            ("experimental", r"测量|变量|对照|传感器"),
        ]
        if re.search(pattern, text)
    ]
    return (
        (guesses[0], "研究问题中包含相应方法线索，仅作建议，须用户确认")
        if len(guesses) == 1
        else (None, "方法尚不明确，不能仅按学科推断")
    )


def build_plan_context(service, workspace, context, text, req):
    ensure_plan_scope(text + "\n" + context.get("topic", ""))
    flow = service.workflow(workspace)["flow"]
    records = {i.item_id: i for i in workspace.items}
    qid = req.question_id or (flow.get("question") or {}).get("item_id")
    question = records.get(qid)
    if req.question_id and (
        not question or question.kind != "question" or question.status != "active"
    ):
        raise WorkspaceError("研究问题不存在、已修订或不属于当前项目")
    target = records.get(req.supersedes_id) if req.supersedes_id else None
    if req.supersedes_id and (
        not target or target.kind != "plan" or target.status != "active"
    ):
        raise WorkspaceError("待修订方案不存在、已被替换或不属于当前项目")
    hint, reason = method_hint(text + "\n" + (question.content if question else ""))
    method = req.method
    if method:
        reason = "用户明确选择：" + METHODS[method]
    if method is None:
        explicit, why = method_hint(text)
        if why.startswith("用户明确"):
            method = explicit
        elif target and target.plan:
            method = target.plan.method
    result = {
        "question": question.model_dump(mode="json") if question else None,
        "method": method,
        "suggested_method": hint,
        "method_reason": reason,
        "supersedes_id": req.supersedes_id,
        "revision_target": target.model_dump(mode="json") if target else None,
        "allowed": False,
    }
    if not question:
        result["clarification"] = (
            "请先明确选择并确认一个正式研究问题；目前不会自动创建问题并继续生成方案。"
            if not flow["questions"]
            else "当前有多个研究问题，请先选择本次方案对应的问题。"
        )
    elif question.needs_review:
        result["clarification"] = (
            "当前研究问题或来源待复核，请先复核问题，再请求生成方案。"
        )
    elif target and qid not in target.related_ids:
        raise WorkspaceError("待修订方案与所选问题不属于同一主线")
    elif not method:
        result["clarification"] = (
            "请选择计算研究、理论研究或实验研究设计。"
            + reason
            + ("；建议可考虑" + METHODS[hint] if hint else "")
            + "。选择类型本身不会调用模型。"
        )
    else:
        result["allowed"] = True
    if question:
        ensure_plan_scope(question.content)
        # Include actual sources associated with the selected question, not just
        # whichever six abstracts happened to rank highest for the project title.
        present = {p["paper_id"] for p in context["papers"]}
        available = {p.paper_id: p for p in service.store.list_papers()}
        for pid in question.paper_ids[:12]:
            if pid in present:
                continue
            p = available.get(pid)
            if p is None:
                result.update(
                    allowed=False,
                    clarification="问题关联的论文不可用，请先核对来源，不自动补造依据。",
                )
                continue
            context["papers"].append(
                {
                    "paper_id": pid,
                    "title": p.title,
                    "abstract": (p.abstract or "")[:1200],
                    "scope": p.metadata.get("reading_scope")
                    or "已存摘要和片段，不保证全文",
                    "excerpts": [
                        {"text": c.text[:1000], "page": c.page}
                        for c in service.store.get_paper_chunks(pid)[:2]
                    ],
                }
            )
    return result


PLAN_PROMPT = """【理工科方案生成 stem-plan-v1】
只有 plan_generation.allowed=true 且用户明确要求制定/修订时生成。必须返回一条 kind=plan 的 suggestions，填写 plan 结构（不是只有 content）。
plan.method 与指定方法相同，question_id 使用本次明确的问题；related_ids 只关联该问题，supersedes_id 原样使用指定修订目标。不要新建问题，不自动采纳。
九个章节 sections 各一次：objective,related_work,hypothesis,conditions,method,validation,feasibility,limitations,references。每个 statements 填 text,basis(literature/user/proposal/unverified),paper_ids,quote。
literature 必须有本次实际提供的摘要或片段逐字 quote 和 paper_id，元信息不能支撑方法、结果或研究空白；不能根据论文目录编造结论。user 的 quote 必须是 brief、正式问题或本会话用户原文，不能虚构设备/期限/预算/权限。proposal 是待讨论建议；unverified 明示待核实。
conditions 的 resources/time_budget/budget/data_access 均填写 value,basis(user/unverified),quote；未知用 value=待确认,basis=unverified,quote=空，不填精确假数字。即便理论研究不涉及数据，也不强制填训练集；可待用户确认不涉及。
已知条件的 quote 必须直接复制用户原文，value 直接摘取 quote 中的连续原文，不自行改写、推算或翻译（例如用户写四周，就不要写一个月）。不确定如何对应时标待确认。
method_details 按类型填写全部必需字段：计算研究(data_task,baselines,comparisons,budget,evaluation,reproducibility)；理论研究(definitions,assumptions,propositions,derivation_route,proof_obligations,counterexamples,boundaries,related_theory)，新命题明确待论证，不强塞准确率、训练数据或代码；实验研究设计(research_object,variables,controls,measurements,repetition_error,resource_needs,professional_review)，只规划，具体操作参数不能臆造，须专业审核。
unconfirmed 保留实际待讨论事项，references 只使用实际提供的项目论文 ID。允许复现、比较和小改进，不强称全新方法。不要求所有研究公式—代码映射，公式或伪代码仅在确实有用时给出。
不生成实验结果，不写预期结果为已取得结果，不保证创新性、可发表性或成功概率；完整性不等于科学有效。本轮止于方案。
"""


def user_basis(context, text):
    return "\n".join(
        [
            text,
            *[str(v) for v in context.get("brief", {}).values()],
            *[m["content"] for m in context.get("history", []) if m["role"] == "user"],
            (context.get("plan_generation", {}).get("question") or {}).get(
                "content", ""
            ),
        ]
    )


class ConditionEvidenceError(WorkspaceError):
    """Actionable condition error; technical location is kept in audit details."""

    def __init__(self, field, reason):
        self.field, self.reason = field, reason
        label = {
            "resources": "资源／设备",
            "time_budget": "期限",
            "budget": "预算",
            "data_access": "资料权限",
        }[field]
        super().__init__(
            f"方案中的{label}未能对应你提供的原文，草案尚未保存或采纳。请补充或核对这项条件后重试；原消息和已有材料仍保留。"
        )


def _literal(text):
    # Typography only. Do not infer equivalence of CPU/GPU, weeks/months,
    # permissions, negation, quantities or paraphrases. Preserve original fields.
    # Full-width ASCII only: do not fold superscripts, circled numerals or units.
    width = str.maketrans({i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)})
    return re.sub(r"\s+", "", text.translate(width))


def validate_plan(plan: ResearchPlan, context, text):
    pg = context.get("plan_generation", {})
    if pg and (
        not pg.get("allowed")
        or plan.question_id != pg["question"]["item_id"]
        or plan.method != pg["method"]
    ):
        raise WorkspaceError("方案方法或关联问题与本次明确选择不一致")
    papers = {p["paper_id"]: p for p in context.get("papers", [])}
    if not set(plan.references) <= papers.keys():
        raise WorkspaceError("方案引用不属于本次实际提供的项目材料")
    known = user_basis(context, text)
    evidence = []
    for section in plan.sections:
        for s in section.statements:
            if not s.text.strip() or not set(s.paper_ids) <= set(plan.references):
                raise WorkspaceError("方案陈述为空或引用未列入参考来源")
            if s.basis == "literature":
                if not s.paper_ids or not s.quote.strip():
                    raise WorkspaceError("文献事实必须附实际依据片段")
                for pid in s.paper_ids:
                    p = papers[pid]
                    available = [
                        p.get("abstract", ""),
                        *[c["text"] for c in p.get("excerpts", [])],
                    ]
                    if not any(s.quote in a for a in available if a):
                        raise WorkspaceError(
                            "文献依据不在提供的摘要／正文片段中；元信息不能支持内容结论"
                        )
                evidence.append(s.quote)
            elif s.basis == "user" and (not s.quote.strip() or s.quote not in known):
                raise WorkspaceError("用户条件没有对应的已提供原文")
            elif s.basis != "literature" and re.search(
                r"实验结果(?:表明|显示)|我们(?:已取得|已经证明|已验证)|准确率达到了|已经实现了",
                s.text,
            ):
                raise WorkspaceError("不能将拟研究或预期结果写成已取得结果")
    for field, c in plan.conditions.model_dump().items():
        if c["basis"] == "user":
            if not c["quote"].strip() or _literal(c["quote"]) not in _literal(known):
                raise ConditionEvidenceError(field, "quote_not_in_user_context")
            if not c["value"].strip() or _literal(c["value"]) not in _literal(
                c["quote"]
            ):
                raise ConditionEvidenceError(field, "value_not_literal_excerpt")
            # Substring matching must not turn '没有GPU' into an available 'GPU'.
            # Keep a qualified condition whole rather than infer its meaning.
            if re.search(
                r"没有|不可|不能|不允许|未经|尚未|待确认|未知|无", c["quote"]
            ) and _literal(c["value"]) != _literal(c["quote"]):
                raise ConditionEvidenceError(field, "condition_qualifier_omitted")
        elif c["value"] != "待确认" or c["quote"]:
            raise ConditionEvidenceError(field, "unknown_condition_presented_as_fact")
    details = plan.method_details.model_dump(exclude={"type"})
    if re.search(r"(?:无|没有|不能使用)\s*GPU|(?:仅|只有)\s*CPU", known, re.I):
        design_text = "\n".join(str(v) for v in details.values())
        design_text = re.sub(
            r"(?:不需要|无需|不使用)\s*GPU", "", design_text, flags=re.I
        )
        if re.search(r"(?:需要|使用|依赖)\s*GPU|GPU\s*训练", design_text, re.I):
            raise WorkspaceError(
                "方案安排与用户明确的 CPU／无 GPU 条件冲突，请调整方案"
            )
    if any(not str(v).strip() for v in details.values()):
        raise WorkspaceError("方案方法设计存在空字段")
    if plan.method == "theoretical" and not re.search(
        r"待论证|待证明|拟证明|尚未证明|拟论证", details["propositions"]
    ):
        raise WorkspaceError("理论研究中的新命题必须标记待论证")
    all_text = json.dumps(plan.model_dump(), ensure_ascii=False)
    for token in re.findall(
        r"\d+\s*(?:万元|元|张GPU|块GPU|天|周|小时|℃|°C|MPa|mL|mol|个样本|次重复|轮训练)",
        all_text,
    ):
        if token not in known and not any(token in q for q in evidence):
            raise WorkspaceError("出现未经用户条件或文献支持的精确参数：" + token)
    if plan.method == "experimental":
        ensure_plan_scope(all_text)
        if re.search(r"(?:加入|配制|服用|注射|加热至|升温至).{0,20}\d", all_text):
            raise WorkspaceError("当前仅实验研究设计，不提供具体设备或危险操作步骤")


def render_plan(plan: ResearchPlan, reference_labels: dict[str, str] | None = None):
    def reference_label(pid):
        return reference_labels.get(pid, "来源待核对") if reference_labels is not None else pid

    out = [
        f"**方案类型：{METHODS[plan.method]}" + (f" · 模板 {plan.template_version}" if reference_labels is None else "") + "**",
        "",
        "> 研究方案建议，尚未执行。结构完整不等于科学有效；采纳状态见记录标记。",
    ]
    for key, title in SECTIONS.items():
        section = next(s for s in plan.sections if s.key == key)
        out += ["", "## " + title]
        for s in section.statements:
            out += [
                f"- 【{BASES[s.basis]}】{s.text}"
                + (" [" + ", ".join(reference_label(pid) for pid in s.paper_ids) + "]" if s.paper_ids else "")
            ]
            if s.quote:
                out += ["> 依据原文：" + s.quote.replace("\n", " ")]
    out += ["", "## 方法专属设计（" + METHODS[plan.method] + "）"]
    for field, label in DETAILS[plan.method].items():
        out += [f"### {label}", getattr(plan.method_details, field)]
    out += [
        "",
        "## 已知条件与待确认",
        *[
            f"- {label}：{getattr(plan.conditions, field).value}（{BASES[getattr(plan.conditions, field).basis]}）"
            for field, label in [
                ("resources", "资源／设备"),
                ("time_budget", "期限"),
                ("budget", "预算"),
                ("data_access", "数据或材料访问"),
            ]
        ],
        "",
        "## 待讨论事项",
        *["- " + s for s in plan.unconfirmed],
        "",
        ("参考资料：" if reference_labels is not None else "参考论文 ID：")
        + (", ".join(reference_label(pid) for pid in plan.references) or "尚无可核验内容依据，需补充资料"),
    ]
    return "\n\n".join(out)


def export_plan_content(item, reference_labels):
    """Relabel generated annotations only; keep custom text and saved records intact."""
    if item.plan is None:
        return item.content
    return item.content.replace(render_plan(item.plan), render_plan(item.plan, reference_labels), 1)


def prepare_plan(draft, context, text):
    pg = context.get("plan_generation")
    if not pg:
        return draft
    plans = [s for s in draft.suggestions if s.kind == "plan"]
    if (
        not pg.get("allowed")
        or len(plans) != 1
        or len(draft.suggestions) != 1
        or plans[0].plan is None
    ):
        raise WorkspaceError(
            "方案请求须返回一份完整的结构化方案草案，未自动保存替代内容"
        )
    s = plans[0]
    if (
        s.related_ids != [pg["question"]["item_id"]]
        or s.supersedes_id != pg["supersedes_id"]
    ):
        raise WorkspaceError("方案关联或修订目标与请求不一致")
    validate_plan(s.plan, context, text)
    if not set(draft.cited_paper_ids) <= set(s.plan.references):
        raise WorkspaceError("回复引用须与方案的真实参考来源一致")
    s.paper_ids = list(s.plan.references)
    draft.cited_paper_ids = list(s.plan.references)
    s.content = render_plan(s.plan)
    # Human-readable content is derived from the validated structured plan.
    if len(s.content) > 12000:
        raise WorkspaceError("方案正文过长，请缩短结构化内容后重试；不发布截断方案")
    draft.actions = (
        [ActionProposal(type="view_references")] if s.plan.references else []
    ) + [ActionProposal(type="adopt_suggestion", suggestion_index=0)]
    return draft


def rule_plan(context):
    pg = context["plan_generation"]
    if not pg["allowed"]:
        return GuideDraft(
            reply=pg["clarification"],
            questions=["请明确本次的问题和研究方法；这不会修改正式主线。"],
        )
    unknown = {"value": "待确认", "basis": "unverified", "quote": ""}
    conditions = {
        k: dict(unknown) for k in ("resources", "time_budget", "budget", "data_access")
    }
    for key in ("resources", "time_budget"):
        value = context.get("brief", {}).get(key, "")
        if value:
            conditions[key] = {
                "value": value[:1000],
                "basis": "user",
                "quote": value[:1000],
            }
    statements = {
        k: [
            {
                "text": "待讨论：" + title + "；当前材料不足以作出验证结论。",
                "basis": "unverified",
            }
        ]
        for k, title in SECTIONS.items()
    }
    statements["objective"] = [
        {
            "text": pg["question"]["content"][:1000],
            "basis": "user",
            "quote": pg["question"]["content"][:1000],
        }
    ]
    method = pg["method"]
    details = {k: "待讨论：" + label for k, label in DETAILS[method].items()}
    if method == "theoretical":
        details["propositions"] = "待论证：在明确假设后提出待证明命题，不宣称已有证明。"
    if method == "experimental":
        details["professional_review"] = (
            "仅规划；测量条件与安全性须专业人员审核，不给具体操作参数。"
        )
    plan = ResearchPlan(
        method=method,
        question_id=pg["question"]["item_id"],
        sections=[{"key": k, "statements": v} for k, v in statements.items()],
        conditions=conditions,
        method_details={"type": method, **details},
        unconfirmed=["补充相关文献依据并与导师讨论可行性；当前为规则讨论稿。"],
        references=[],
    )
    return GuideDraft(
        reply="当前为规则模式，未调用模型；以下为待完善讨论稿，尚无文献内容验证。",
        suggestions=[
            SuggestedItem(
                kind="plan",
                title=METHODS[method] + "方案讨论稿",
                content="待渲染",
                related_ids=[pg["question"]["item_id"]],
                supersedes_id=pg["supersedes_id"],
                plan=plan,
            )
        ],
    )


def completeness(plan):
    if plan is None:
        return {
            "structured": False,
            "notice": "历史方案，无本版结构字段；可阅读、导出，不自动重新生成",
        }
    return {
        "structured": True,
        "template_version": plan.template_version,
        "method": plan.method,
        "section_count": len(plan.sections),
        "unconfirmed": plan.unconfirmed,
        "has_content_evidence": any(
            s.basis == "literature"
            for section in plan.sections
            for s in section.statements
        ),
        "notice": "仅检查结构与已提供依据，不证明科学正确性、创新性或可发表性",
    }
