"""Step 6A. Real HTTP, temporary SQLite, offline structured models only."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from fastapi.responses import Response
from core.workspace.plans import rule_plan, SECTIONS, METHODS, DETAILS
from core.workspace.schema import Brief, ItemInput, ResearchFocus, GuideDraft
from core.workspace.service import KEY
from core.workspace.conversations import ConversationService
from core.knowledge.schema import PaperChunk
from core.llm.base import LLMTruncatedError
from tests.test_project_conversations import chat, create, send, messages, apply, tree
from web.project_exports_api import build_exports_router


def answer(r):
    assert r.status_code == 200, r.text
    return r.json()


def generate(chat, cid, method="computational", rid="plan", **extra):
    return send(
        chat,
        cid,
        "请为当前问题生成研究方案",
        rid,
        plan_request={"method": method, **extra},
    )


def model(context, *, literature=False):
    d = rule_plan(context)
    d.reply = "离线结构化软件验收；不是科研结论。"
    if literature:
        p = next(p for p in context["papers"] if p.get("abstract"))
        q = next(s for s in d.suggestions[0].plan.sections if s.key == "related_work")
        q.statements[0].text = "所提供摘要明确写明：" + p["abstract"]
        q.statements[0].basis = "literature"
        q.statements[0].paper_ids = [p["paper_id"]]
        q.statements[0].quote = p["abstract"]
        d.suggestions[0].plan.references = [p["paper_id"]]
    return d


def core_payload(draft):
    """Offline generator shape. Never use this to repair a saved raw response."""
    suggestion = draft.suggestions[0]
    plan = suggestion.plan.model_dump(mode="json")
    return {
        "title": suggestion.title,
        **{name: plan[name] for name in ("sections", "conditions", "unconfirmed")},
        "method_details": {k: v for k, v in plan["method_details"].items() if k != "type"},
    }


@pytest.mark.parametrize("method", list(METHODS))
def test_three_method_structures_have_distinct_real_fields_and_do_not_execute(
    chat, method
):
    cid = create(chat)
    before = chat.services["p1"].load(include_chat=False).model_dump()
    chat.control["generate"] = lambda area, text, ctx: model(ctx, literature=True)
    data = answer(generate(chat, cid, method))
    s = data["suggestions"][0]
    assert {k for k in s["plan"]["method_details"] if k != "type"} == set(
        DETAILS[method]
    )
    assert {v["key"] for v in s["plan"]["sections"]} == set(SECTIONS)
    assert s["plan"]["method"] == method
    assert s["paper_ids"] == ["p1-paper"]
    assert all(v["value"] == "待确认" for v in s["plan"]["conditions"].values())
    assert chat.services["p1"].load(include_chat=False).model_dump() == before
    assert not chat.services["p1"].store.list_experiments()
    assert not chat.services["p1"].store.get_kv("pipeline.context.v1")
    if method == "theoretical":
        assert "待论证" in s["content"]
        assert not any(
            word in s["content"] for word in ["训练集", "准确率", "代码骨架"]
        )
    if method == "experimental":
        assert "专业审核" in s["content"] and "仅规划" in s["content"]
    assert chat.calls[-1]["context"]["plan_generation"]["method"] == method


def test_physics_does_not_choose_method_automatically_and_selection_is_readonly(chat):
    chat.projects["p1"].domain = "physics"
    cid = create(chat)
    before = tree(chat.root)
    chat.client.get(f"/api/projects/p1/conversations/{cid}")
    chat.client.get("/api/projects/p1/workspace?include_chat=false")
    assert tree(chat.root) == before and chat.calls == []
    response = answer(generate(chat, cid, None))
    assert response["suggestions"] == [] and chat.calls == []
    assert "请选择计算研究" in response["messages"][-1]["content"]


def test_no_formal_question_and_multiple_questions_do_not_autocreate_or_pick(chat):
    cid = create(chat)
    s = chat.services["p1"]
    w = s.load(include_chat=False)
    w.items = []
    w.focus = ResearchFocus()
    s.store.save_kv(KEY, w.model_dump(mode="json"))
    result = answer(generate(chat, cid))
    assert "不会自动创建" in result["messages"][-1]["content"]
    assert s.load().items == [] and chat.calls == []
    for title in ("问题A", "问题B"):
        s.add_item(
            ItemInput(
                kind="question", title=title, content="待核查", paper_ids=["p1-paper"]
            ),
            s.load().revision,
        )
    s.save_focus(ResearchFocus(), s.load().revision)
    result = answer(generate(chat, cid, rid="many"))
    assert "多个研究问题" in result["messages"][-1]["content"]
    assert chat.calls == []


def test_selected_question_and_target_ownership_before_write(chat):
    cid = create(chat)
    foreign = chat.services["p2"].load().items
    original = tree(chat.root)
    assert generate(chat, cid, question_id=foreign[0].item_id).status_code == 422
    assert generate(chat, cid, supersedes_id=foreign[1].item_id).status_code == 422
    assert tree(chat.root) == original and chat.calls == []


@pytest.mark.parametrize(
    "bad",
    [
        "forged_ref",
        "metadata_claim",
        "made_up_budget",
        "wrong_quote",
        "achieved_result",
        "missing_section",
        "wrong_method",
        "unproven",
        "invalid_field",
        "truncated",
    ],
)
def test_bad_outputs_are_not_published_as_usable_plans(chat, bad):
    cid = create(chat)

    def output(area, text, ctx):
        if bad == "truncated":
            raise LLMTruncatedError("offline truncation")
        d = model(ctx)
        plan = d.suggestions[0].plan
        if bad == "forged_ref":
            plan.references = ["p2-paper"]
        if bad in ("metadata_claim", "wrong_quote"):
            sec = plan.sections[1].statements[0]
            sec.basis = "literature"
            sec.paper_ids = ["p1-paper"]
            sec.quote = "不存在的论文原文"
            plan.references = ["p1-paper"]
        if bad == "metadata_claim":
            for p in ctx["papers"]:
                p["abstract"] = ""
                p["excerpts"] = []
        if bad == "made_up_budget":
            plan.conditions.budget.value = "20万元"
        if bad == "achieved_result":
            plan.sections[2].statements[
                0
            ].text = "我们已验证该方法并且实验结果显示显著提升"
        if bad == "wrong_method":
            plan.method = "experimental"
        if bad == "unproven":
            plan.method_details.propositions = "已经证明新命题"
        data = d.model_dump()
        if bad == "missing_section":
            data["suggestions"][0]["plan"]["sections"].pop()
        if bad == "invalid_field":
            data["suggestions"][0]["plan"]["method_details"][
                "execute_shell"
            ] = "untrusted"
        return data

    chat.control["generate"] = output
    before = chat.services["p1"].load(include_chat=False).model_dump()
    result = generate(
        chat, cid, "theoretical" if bad == "unproven" else "computational"
    )
    assert result.status_code in (422, 502), result.text
    assert result.json()["status"] == "failed"
    assert len(messages(chat, cid)) == 1
    assert chat.services["p1"].load(include_chat=False).model_dump() == before


def test_revision_versions_and_concurrent_atomic_adoption(chat):
    cid = create(chat)
    s = chat.services["p1"]
    old = s.load().items[-1]
    chat.control["generate"] = lambda area, text, ctx: model(ctx)
    proposal = answer(generate(chat, cid, "theoretical", supersedes_id=old.item_id))[
        "suggestions"
    ][0]
    assert s.load().items[-1].status == "active"
    with ThreadPoolExecutor(2) as ex:
        responses = list(
            ex.map(lambda _: apply(chat, cid, proposal["suggestion_id"]), range(2))
        )
    assert all(r.status_code == 200 for r in responses)
    assert (
        responses[0].json()["item"]["item_id"] == responses[1].json()["item"]["item_id"]
    )
    new = s.load().items[-1]
    assert new.supersedes_id == old.item_id and new.plan_version == 2
    previous = next(i for i in s.load().items if i.item_id == old.item_id)
    assert previous.content == old.content and previous.status == "superseded"
    assert s.workflow()["flow"]["plan"]["item_id"] == new.item_id
    assert (
        s.workflow()["flow"]["planReady"] is True
    )  # dependency readiness, not scientific proof


@pytest.mark.parametrize("change", ["excerpt", "brief", "question"])
def test_stale_plan_uses_existing_fingerprint_and_keeps_draft(chat, change):
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: model(ctx)
    s = chat.services["p1"]
    proposal = answer(generate(chat, cid))["suggestions"][0]
    if change == "excerpt":
        s.store.save_paper_chunks(
            [
                PaperChunk(
                    chunk_id="chunk",
                    paper_id="p1-paper",
                    chunk_index=0,
                    text="Changed",
                    page=1,
                )
            ]
        )
    elif change == "brief":
        s.save_brief(Brief(time_budget="两周"), s.load().revision)
    else:
        old = s.load().items[0]
        s.add_item(
            ItemInput(
                kind="question",
                title="修订的问题",
                content="问题已变",
                supersedes_id=old.item_id,
                paper_ids=["p1-paper"],
            ),
            s.load().revision,
        )
    assert apply(chat, cid, proposal["suggestion_id"]).status_code == 409
    data = chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions").json()[
        "suggestions"
    ]
    assert data[0]["basis_changed"] and data[0]["content"] == proposal["content"]


def test_condition_changes_during_generation_do_not_get_overwritten(chat):
    cid = create(chat)
    s = chat.services["p1"]

    def output(area, text, ctx):
        d = model(ctx)
        s.save_brief(Brief(resources="用户刚补充的资源"), s.load().revision)
        return d

    chat.control["generate"] = output
    data = answer(generate(chat, cid))
    assert data["basis_changed"] and data["suggestions"][0]["basis_changed"]
    assert s.load().brief.resources == "用户刚补充的资源"
    assert apply(chat, cid, data["suggestions"][0]["suggestion_id"]).status_code == 409


def test_rule_plan_is_honest_and_does_not_call_model(chat):
    chat.config.dry_run = True
    cid = create(chat)
    data = answer(generate(chat, cid, "experimental"))
    assert data["messages"][-1]["mode"] == "rules" and chat.calls == []
    assert data["suggestions"][0]["plan"]["references"] == []
    assert "尚无文献内容验证" in data["messages"][-1]["content"]


def attach_exports(chat):
    def download(text, name, mime):
        return Response(text, media_type=mime)

    chat.client.app.include_router(
        build_exports_router(
            chat.config,
            lambda pid: chat.projects[pid],
            lambda p: chat.services[p.project_id].workflow(),
            lambda *a: None,
            download,
            lambda text, name: download(text, name, "text/plain"),
            lambda text, name: download(text, name, "text/plain"),
        )
    )


def test_export_draft_formal_and_history_are_distinct_and_include_versions(chat):
    attach_exports(chat)
    cid = create(chat)
    s = chat.services["p1"]
    old = s.load().items[-1]
    chat.control["generate"] = lambda area, text, ctx: model(ctx, literature=True)
    proposal = answer(generate(chat, cid, supersedes_id=old.item_id))["suggestions"][0]
    path = f"/api/projects/p1/conversations/{cid}/suggestions/{proposal['suggestion_id']}/export"
    exported = chat.client.get(path)
    assert exported.status_code == 200 and "未采纳讨论稿" in exported.text
    assert "Offline fixture p1" in exported.text and "p1 question" in exported.text
    catalog = chat.client.get(f"/api/projects/p1/exports?conversation_id={cid}").json()[
        "entries"
    ]
    row = next(e for e in catalog if e["key"] == "draft:" + proposal["suggestion_id"])
    assert not row["recommended"] and row["group"] == "other"
    assert (
        next(e for e in catalog if e["key"].startswith("plan:"))["key"]
        == "plan:" + old.item_id
    )
    new = answer(apply(chat, cid, proposal["suggestion_id"]))["item"]
    formal = chat.client.get(f"/api/projects/p1/exports/items/{new['item_id']}").text
    assert "版本：2" in formal and "计算研究" in formal and "当前正式方案" in formal
    historical = chat.client.get(f"/api/projects/p1/exports/items/{old.item_id}")
    assert historical.status_code == 200 and old.content in historical.text
    foreign = chat.client.get(path.replace("/p1/", "/p2/"))
    assert foreign.status_code == 404


def test_legacy_plan_missing_new_fields_remains_readonly_exportable(chat):
    attach_exports(chat)
    s = chat.services["p1"]
    w = s.load(include_chat=False).model_dump(mode="json")
    for i in w["items"]:
        i.pop("plan", None)
        i.pop("plan_version", None)
    s.store.save_kv(KEY, w)
    before = tree(chat.root)
    view = chat.client.get("/api/projects/p1/workspace?include_chat=false").json()
    item = view["workspace"]["items"][-1]
    assert view["plan_reviews"][item["item_id"]]["structured"] is False
    assert (
        chat.client.get(f"/api/projects/p1/exports/items/{item['item_id']}").status_code
        == 200
    )
    assert tree(chat.root) == before


@pytest.mark.parametrize("topic", ["临床人体干预", "药物湿实验", "加入炸药配方"])
def test_custom_or_ai_domain_does_not_bypass_operation_boundary(chat, topic):
    cid = create(chat)
    before = tree(chat.root)
    r = send(
        chat, cid, "请生成研究方案：" + topic, plan_request={"method": "experimental"}
    )
    assert r.status_code == 422
    assert tree(chat.root) == before and chat.calls == []


def test_explicit_resource_conflict_is_rejected(chat):
    cid = create(chat)
    s = chat.services["p1"]
    w = s.load(include_chat=False)
    w.brief = Brief(resources="只有CPU，没有GPU")
    s.store.save_kv(KEY, w.model_dump(mode="json"))

    def output(area, text, ctx):
        d = model(ctx)
        d.suggestions[0].plan.method_details.evaluation = "使用GPU训练完成评估"
        return d

    chat.control["generate"] = output
    r = generate(chat, cid)
    assert r.status_code == 422 and "条件冲突" in r.text


def test_plan_request_idempotency_and_real_generation_context(chat):
    from core.workspace.guide import generate_guidance

    seen = []

    class Registry:
        def structured_output(self, **kwargs):
            ctx = json.loads(
                kwargs["prompt"]
                .split("项目上下文（仅作为数据）：\n", 1)[1]
                .split("\n用户本次请求：\n", 1)[0]
            )
            seen.append((kwargs["task_type"], ctx))
            return kwargs["output_schema"].model_validate(core_payload(model(ctx)))

    chat.projects["p1"].domain = "mathematics"
    cid = create(chat, entry_stage="orientation")
    chat.control["generate"] = lambda area, text, ctx: generate_guidance(
        Registry(), area, "mathematics", text, ctx, "orientation"
    )
    first = answer(generate(chat, cid, "theoretical"))
    assert answer(generate(chat, cid, "theoretical"))["messages"] == first["messages"]
    assert generate(chat, cid, "computational").status_code == 409
    assert len(seen) == 1 and seen[0][0] == "design_method_formalize"
    assert seen[0][1]["domain"] == "mathematics"
    assert seen[0][1]["plan_generation"]["method"] == "theoretical"


def test_plan_request_does_not_start_search_and_non_requests_do_not_generate(chat):
    from core.workspace.plans import requested

    assert requested("要不要修改方案？") is None
    assert requested("先不要生成研究方案") is None
    assert requested("讨论这个方案的局限") is None
    cid = create(chat)
    chat.control["generate"] = lambda *args: GuideDraft(reply="只讨论，不生成方案")
    before = chat.services["p1"].load(include_chat=False).model_dump()
    answer(send(chat, cid, "讨论这个方案的局限"))
    assert chat.services["p1"].load(include_chat=False).model_dump() == before


def test_model_cannot_generate_unrequested_or_unstructured_plan(chat):
    from core.workspace.schema import SuggestedItem

    cid = create(chat)
    q = chat.services["p1"].load().items[0]
    chat.control["generate"] = lambda *args: GuideDraft(
        reply="不可偷偷生成方案",
        suggestions=[
            SuggestedItem(
                kind="plan",
                title="越权草案",
                content="没有明确请求的方案",
                related_ids=[q.item_id],
            )
        ],
    )
    before = chat.services["p1"].load(include_chat=False).model_dump()
    r = send(chat, cid, "只解释研究现状")
    assert r.status_code == 422 and r.json()["status"] == "failed"
    assert len(messages(chat, cid)) == 1
    assert chat.services["p1"].load(include_chat=False).model_dump() == before
    r = generate(chat, cid, rid="unstructured")
    assert r.status_code == 422
    assert chat.services["p1"].load(include_chat=False).model_dump() == before


@pytest.mark.parametrize(
    "method,domain,question,resources,details",
    [
        (
            "computational",
            "ai",
            "公开数据上的联邦学习方法在异质数据条件下如何公平比较？",
            "仅使用CPU和公开数据，数据许可待确认",
            {
                "data_task": "比较公开分类任务上的异质数据划分，具体数据集待核对许可。",
                "baselines": "拟比较常用聚合基线，具体实现版本待文献核实。",
                "comparisons": "各方法共享数据划分、参与条件和计算预算。",
                "budget": "遵守用户仅使用CPU的条件，先评估可行规模，不安排GPU。",
                "evaluation": "拟比较任务误差、通信代价与跨划分波动，不填写实验结果。",
                "reproducibility": "记录环境版本、划分方法与随机种子，重复次数待讨论。",
            },
        ),
        (
            "theoretical",
            "mathematics",
            "带约束图上的可行最短路性质在什么假设下成立？",
            "已有图论基础，证明范围需要导师确认",
            {
                "definitions": "先定义图、可行路径和路径代价，区分约束与优化目标。",
                "assumptions": "边权条件与可行集合非空性须明确，不能默认满足。",
                "propositions": "待论证：在明确的边权和可行性假设下讨论最优路径存在性。",
                "derivation_route": "拟先讨论有限图，再检查放宽假设时的失效情况。",
                "proof_obligations": "分别证明可行性、极小值存在性和必要条件，缺失步骤明确标记。",
                "counterexamples": "构造不满足边权或可行性假设的反例检验边界。",
                "boundaries": "暂不扩展至无限图，是否扩展由导师讨论后决定。",
                "related_theory": "与既有最短路理论逐项对照，所需定理原文尚待补充。",
            },
        ),
        (
            "experimental",
            "physics",
            "光照变化是否影响光电传感器输出的稳定性？",
            "已有光电传感器，测量能力和实验室条件待确认",
            {
                "research_object": "拟研究光电传感器输出的稳定性，不假定已掌握设备性能。",
                "variables": "拟将光照条件作为自变量，将输出波动作为观察量。",
                "controls": "讨论如何保持环境与传感器状态可比，不给出操作参数。",
                "measurements": "测量量定义、仪器适用性与校准可行性须由专业人员确认。",
                "repetition_error": "区分仪器误差和重复测量波动，重复安排待确认。",
                "resource_needs": "核对现有传感器及测量资源，不默认具备未提供的设备。",
                "professional_review": "具体设备条件和安全事项需导师及实验室人员审核；本方案仅规划。",
            },
        ),
    ],
)
def test_end_to_end_method_scenarios_context_adoption_and_export(
    chat, method, domain, question, resources, details
):
    """Substantive offline scenarios, not just prompt-string or title checks."""
    attach_exports(chat)
    s = chat.services["p1"]
    old_question = s.load().items[0]
    # Conditions are established before confirming the new question. Changing
    # brief afterwards correctly makes that question require review instead.
    s.save_brief(Brief(resources=resources, time_budget="两周"), s.load().revision)
    w = s.add_item(
        ItemInput(
            kind="question",
            title=question,
            content=question,
            supersedes_id=old_question.item_id,
        ),
        s.load().revision,
    )
    q = w.items[-1]
    chat.projects["p1"].domain = domain
    cid = create(chat, entry_stage="orientation")

    def output(area, text, ctx):
        assert ctx["plan_generation"]["question"]["item_id"] == q.item_id
        assert ctx["brief"]["resources"] == resources
        d = model(ctx)
        p = d.suggestions[0].plan
        p.method_details = type(p.method_details)(type=method, **details)
        p.sections[4].statements[0].text = next(iter(details.values()))
        p.sections[4].statements[0].basis = "proposal"
        p.sections[5].statements[0].text = details.get(
            "proof_obligations",
            details.get("evaluation", details.get("repetition_error")),
        )
        p.sections[5].statements[0].basis = "proposal"
        p.unconfirmed = ["文献依据与所需资源待核对；方案须与导师讨论。"]
        return d

    chat.control["generate"] = output
    initial_count = len(s.load().items)
    data = answer(generate(chat, cid, method))
    proposal = data["suggestions"][0]
    assert len(s.load().items) == initial_count
    assert proposal["plan"]["conditions"]["resources"]["value"] == resources
    assert proposal["plan"]["conditions"]["budget"]["value"] == "待确认"
    assert proposal["plan"]["conditions"]["data_access"]["value"] == "待确认"
    item = answer(apply(chat, cid, proposal["suggestion_id"]))["item"]
    assert item["plan"]["method_details"] == {"type": method, **details}
    exported = chat.client.get(f"/api/projects/p1/exports/items/{item['item_id']}")
    assert exported.status_code == 200
    assert question in exported.text and resources in exported.text
    assert all(value in exported.text for value in details.values())
    assert not s.store.list_experiments()
    if method == "theoretical":
        assert "训练集" not in exported.text and "准确率" not in exported.text


@pytest.mark.parametrize(
    "formula,code,mappings", [("x > y", "", 0), ("", "", 0), ("f(x)", "f(x)", 1)]
)
def test_legacy_method_node_does_not_force_formula_code_pairs(
    tmp_path, formula, code, mappings
):
    from core.knowledge import KnowledgeStore, Idea
    from core.orchestration.context import ExecutionContext
    from core.orchestration.node import NodeStatus
    from stages.common import (
        DRY_RUN,
        KNOWLEDGE_STORE,
        LLM_REGISTRY,
        IDEATION_VALIDATED_IDEA_IDS,
        RESEARCH_TOPIC,
    )
    from stages.design.agents import AtomDecomposeAgent

    store = KnowledgeStore(tmp_path / "node.db")
    idea = Idea(
        idea_id="method-fixture", text="软件验收：讨论证明义务或测量设计，不执行实验"
    )
    store.save_idea(idea)

    class Model:
        def structured_output(self, **kwargs):
            return kwargs["output_schema"].model_validate(
                {
                    "atom_concepts": [
                        {
                            "concept_name": "reasoning",
                            "description": "待讨论的方法组成",
                            "formula_latex": formula,
                            "code_stub": code,
                        }
                    ],
                    "formula_code_map": [],
                }
            )

    ctx = ExecutionContext(project_id="temporary-node")
    for key, value in [
        (DRY_RUN, False),
        (KNOWLEDGE_STORE, store),
        (LLM_REGISTRY, Model()),
        (IDEATION_VALIDATED_IDEA_IDS, [idea.idea_id]),
        (RESEARCH_TOPIC, "方法组成的软件测试"),
    ]:
        ctx.set(key, value)
    result = AtomDecomposeAgent("atom").run(ctx)
    assert result.status == NodeStatus.SUCCESS, result.error
    assert len(result.output.formula_code_map) == mappings
    assert result.output.atom_concepts[0]["formula_latex"] == formula
    assert result.output.atom_concepts[0]["code_stub"] == code
    assert not store.list_experiments()
