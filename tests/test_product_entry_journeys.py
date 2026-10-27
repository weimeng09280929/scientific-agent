"""Entry choices reuse real project routes; offline HTTP and temporary data only."""

import pytest
import json
from types import SimpleNamespace

from tests.test_conversation_home import home, tree
from tests.test_local_product_acceptance import no_network, ok, product


ENTRY_SETTINGS = (
    ("beginner", "orientation", "conditions"),
    ("direct", "literature", "exploration"),
    ("direct", "question", "question"),
    ("direct", "literature", "reading"),
)


def begin(h, entry, request_id, project_id=None):
    mode, stage, step = ENTRY_SETTINGS[entry]
    body = {
        "request_id": request_id,
        "title": "人工智能",
        "domain": "ai",
        "guidance_mode": mode,
        "entry_stage": stage,
        "discussion_step": step,
    }
    if project_id:
        body["project_id"] = project_id
    return ok(h.client.post("/api/conversations/start", json=body))


def workspace(h, pid):
    return ok(h.client.get(f"/api/projects/{pid}/workspace?include_chat=false"))[
        "workspace"
    ]


def formal_state(h, pid):
    current = workspace(h, pid)
    return {key: current[key] for key in ("revision", "items", "focus")}


def upload_readable(h, pid):
    return ok(
        h.client.post(
            f"/api/projects/{pid}/upload-paper",
            files={
                "file": (
                    "reading-fixture.md",
                    "软件流程测试材料，并非真实论文：在相同任务下比较两种方法，结果尚未验证。".encode(),
                    "text/markdown",
                )
            },
        )
    )["paper_id"]


def test_four_entry_settings_share_project_but_do_not_advance_research(product):
    h = product
    # Preparing the landing page never creates a project or an execution request.
    before = tree(h.root)
    for path in ("/", "/workspace", "/api/conversation-capabilities"):
        assert h.client.get(path).status_code == 200
    assert tree(h.root) == before

    pid = None
    conversations = []
    for index, settings in enumerate(ENTRY_SETTINGS):
        identity = begin(h, index, f"entry-{index}", project_id=pid)
        pid = pid or identity["project_id"]
        assert identity["project_id"] == pid
        cid = identity["conversation_id"]
        conversations.append(cid)
        base = f"/api/projects/{pid}/conversations/{cid}"

        files = tree(h.root)
        meta = ok(h.client.get(base))["conversation"]
        assert (
            meta["guidance_mode"], meta["entry_stage"], meta["discussion_step"]
        ) == settings
        route = ok(h.client.get(base + "/research-route"))
        assert route["discussion_step"] == settings[2]
        # An entry label cannot fabricate evidence, a confirmed question or a plan.
        assert route["step"] == "narrow_interest"
        assert route["question_id"] is None
        assert route["provided_readable_papers"] == 0
        assert route["planReady"] is False
        assert next(s for s in route["steps"] if s["id"] == "design")[
            "blocked_reason"
        ]
        assert ok(h.client.get(base + "/messages"))["messages"] == []
        current = formal_state(h, pid)
        assert current["revision"] == 0 and current["items"] == []
        assert not any(current["focus"].values())
        assert tree(h.root) == files

    assert len(set(conversations)) == 4
    assert len(ok(h.client.get("/api/projects"))["projects"]) == 1

    # Changing discussion focus is a settings write, not formal research adoption.
    base = f"/api/projects/{pid}/conversations/{conversations[0]}"
    baseline = formal_state(h, pid)
    changed = ok(
        h.client.patch(
            base,
            json={
                "guidance_mode": "direct",
                "entry_stage": "literature",
                "discussion_step": "reading",
            },
        )
    )
    assert changed["conversation"]["discussion_step"] == "reading"
    assert formal_state(h, pid) == baseline
    assert ok(h.client.get(base + "/research-route"))["discussion_step"] == "reading"


def test_paper_entry_reads_existing_material_without_search_or_adoption(product):
    h = product
    identity = begin(h, 3, "paper-entry")
    pid, cid = identity["project_id"], identity["conversation_id"]
    base = f"/api/projects/{pid}/conversations/{cid}"
    paper_id = upload_readable(h, pid)
    baseline = formal_state(h, pid)

    files = tree(h.root)
    route = ok(h.client.get(base + "/research-route"))
    assert route["discussion_step"] == "reading"
    assert route["step"] == "read_and_gap"
    assert route["provided_readable_papers"] == 1
    assert route["readable_paper_ids"] == [paper_id]
    assert not route["offer_search"] and not route["planReady"]
    assert tree(h.root) == files

    response = ok(
        h.client.post(
            base + "/messages",
            json={
                "request_id": "read-uploaded-only",
                "content": "只阅读我已上传的资料，说明阅读范围与待核查点；不要检索，不生成方案。",
                "selected_paper_ids": [paper_id],
            },
        )
    )
    assert response["status"] == "completed"
    assert response["messages"][-1]["mode"] == "rules"
    assert set(response["messages"][-1]["cited_paper_ids"]) <= {paper_id}
    assert formal_state(h, pid) == baseline
    assert not any(s["kind"] == "plan" for s in response["suggestions"])

    # Reopening evidence and available downloads must not redo a search or generation.
    files = tree(h.root)
    for path in (
        base + "/research-route",
        base + "/messages",
        f"/api/projects/{pid}/workspace/materials?kind=plans",
        f"/api/projects/{pid}/exports?conversation_id={cid}",
    ):
        assert h.client.get(path).status_code == 200
    assert tree(h.root) == files
    assert formal_state(h, pid) == baseline


def test_returning_question_entry_reuses_confirmed_basis_and_can_take_records(product):
    h = product
    identity = begin(h, 2, "question-entry")
    pid = identity["project_id"]
    paper_id = upload_readable(h, pid)
    baseline = formal_state(h, pid)
    assert baseline["items"] == []

    # This explicit record write represents the user's question confirmation.
    confirmed = ok(
        h.client.post(
            f"/api/projects/{pid}/workspace/items",
            json={
                "expected_revision": baseline["revision"],
                "item": {
                    "kind": "question",
                    "title": "相同任务下如何公平比较两种方法？",
                    "content": "仅形成待讨论的问题；没有实验数据或已验证结果。",
                    "paper_ids": [paper_id],
                },
            },
        )
    )
    question = next(i for i in confirmed["workspace"]["items"] if i["kind"] == "question")
    baseline = formal_state(h, pid)

    # Both returning users and a new beginner discussion retain the formal basis.
    for entry in (2, 0):
        returned = begin(h, entry, f"return-{entry}", project_id=pid)
        cid = returned["conversation_id"]
        base = f"/api/projects/{pid}/conversations/{cid}"
        files = tree(h.root)
        route = ok(h.client.get(base + "/research-route"))
        assert route["discussion_step"] == ENTRY_SETTINGS[entry][2]
        assert route["step"] == "shape_method"
        assert route["question_id"] == question["item_id"]
        assert not next(s for s in route["steps"] if s["id"] == "design")[
            "blocked_reason"
        ]
        assert route["planReady"] is False
        assert tree(h.root) == files
        assert formal_state(h, pid) == baseline

    # A useful, explicitly confirmed intermediate result is downloadable before a plan.
    files = tree(h.root)
    inventory = ok(h.client.get(f"/api/projects/{pid}/exports"))
    records = next(e for e in inventory["entries"] if e["key"] == "records")
    exported = h.client.get(records["url"])
    assert exported.status_code == 200, exported.text
    assert question["title"] in exported.text
    assert "没有实验数据或已验证结果" in exported.text
    assert not any(e["key"].startswith("plan:") for e in inventory["entries"])
    assert formal_state(h, pid) == baseline
    assert tree(h.root) == files


@pytest.mark.parametrize(
    "old_draft", ["请生成实验研究方案", "请检索联邦学习的代表性论文"],
    ids=["old-plan", "old-search"],
)
@pytest.mark.parametrize(
    "entry,prompt",
    [
        (2, "我目前的研究问题是：\n请先帮我核对问题是否具体、可以怎样验证，并整理成待我确认的问题；确认后再讨论方案。"),
        (3, "我手上有一篇论文，希望先读懂研究问题、方法与局限，再整理阅读重点。\n我最想弄清楚的是："),
    ],
    ids=["question-entry", "paper-entry"],
)
def test_switching_entry_keeps_old_draft_without_executing_old_intent(
    product, monkeypatch, old_draft, entry, prompt
):
    h = product
    identity = begin(h, entry, "entry-switch")
    pid, cid = identity["project_id"], identity["conversation_id"]
    base = f"/api/projects/{pid}/conversations/{cid}"
    paper_id = upload_readable(h, pid)
    baseline = formal_state(h, pid)

    from web import workspace_api

    captured = []
    original = workspace_api.rule_guidance

    def observe(area, text, context):
        captured.append((text, dict(context)))
        return original(area, text, context)

    monkeypatch.setattr(workspace_api, "rule_guidance", observe)
    # Exact text composed by chooseEntry + discussOnly preparePlanInput after
    # the user confirms appending the new entry to an existing nonempty draft.
    content = (
        old_draft + "\n\n" + prompt
        + "\n\n本次先按新起点讨论，暂不检索或搜索。"
        + "\n\n本次不生成或修订方案。"
    )
    response = ok(
        h.client.post(
            base + "/messages",
            json={
                "request_id": "discuss-new-entry",
                "content": content,
                "selected_paper_ids": [paper_id],
            },
        )
    )
    assert response["status"] == "completed"
    assert captured and all("plan_generation" not in context for _, context in captured)
    assert captured[0][0] == content
    assert not any(s["kind"] == "plan" for s in response["suggestions"])
    assert formal_state(h, pid) == baseline
    assert ok(h.client.get(base + "/research-tasks"))["tasks"] == []
    assert not any(
        s["kind"] == "plan"
        for s in ok(h.client.get(base + "/suggestions"))["suggestions"]
    )
    saved_users = [
        message["content"]
        for message in ok(h.client.get(base + "/messages"))["messages"]
        if message["role"] == "user"
    ]
    assert saved_users == [content] and saved_users[0].startswith(old_draft)


@pytest.fixture
def offline_journey(product, monkeypatch):
    """Real HTTP/state/validation; only source and model output edges are fixtures."""
    from core.llm import LLMRegistry
    from core.research_tasks import sources
    from core.workspace.guide import rule_guidance
    from tests.test_research_plan_templates import model
    from tests.test_research_tasks import SourceControl, make_paper

    h = product
    h.config.dry_run = False
    source = SourceControl()
    source.default = lambda name, query, seq: [make_paper(
        seq, query, doi=f"10.0000/journey-{seq}",
        title=f"离线流程资料 {seq}：联邦学习比较",
        abstract="JOURNEY_SOURCE_TEXT：软件测试摘要，并非真实论文或科研证据；比较条件仍待核对。",
    )]
    monkeypatch.setattr(sources, "SOURCE_FUNCS", {
        "openalex": source("openalex"), "arxiv": source("arxiv"),
    })
    model_calls, guidance_calls = [], []

    class OfflineRegistry:
        def complete(self, task_type, *, prompt):
            model_calls.append(task_type)
            if task_type == "research_plan":
                output = {"queries": [{"query": "federated learning comparison", "rationale": "离线软件验收"}]}
            elif task_type == "research_rank":
                candidates = json.loads(prompt.split("\n候选：", 1)[1])
                output = {"rankings": [{"index": c["index"], "reason": "本轮比较主题的离线资料", "relevant": True} for c in candidates]}
            elif task_type == "research_synthesize":
                candidates = json.loads(prompt.split("候选文献：", 1)[1].split("\n来源执行情况", 1)[0])
                output = {"representative": [{"paper_id": c["paper_id"], "point": "仅依据离线测试摘要，尚未核对全文。"} for c in candidates],
                          "methods_relations": "测试摘要只用于软件验收，具体比较条件仍待核实。", "limitations": []}
            else:
                raise AssertionError("Unexpected model task in offline journey: " + task_type)
            return SimpleNamespace(text=json.dumps(output, ensure_ascii=False))

    registry = OfflineRegistry()
    monkeypatch.setattr(LLMRegistry, "from_config", classmethod(lambda cls, cfg: registry))

    def generate(registry, area, domain, text, context, purpose):
        guidance_calls.append((area, text))
        if context.get("plan_generation", {}).get("allowed"):
            return model(context, literature=any(p.get("abstract") for p in context["papers"]))
        draft = rule_guidance(area, text, context)
        for suggestion in draft.suggestions:
            if suggestion.kind == "question":
                suggestion.paper_ids = [p["paper_id"] for p in context["papers"][:1]]
        return draft

    monkeypatch.setattr("web.workspace_api.generate_guidance", generate)
    return SimpleNamespace(h=h, source=source, model_calls=model_calls, guidance_calls=guidance_calls)


def journey_message(journey, identity, step, text, request_id, paper_ids=()):
    h = journey.h
    pid, cid = identity["project_id"], identity["conversation_id"]
    base = f"/api/projects/{pid}/conversations/{cid}"
    ok(h.client.patch(base, json={"discussion_step": step}))
    return ok(h.client.post(base + "/messages", json={
        "request_id": request_id, "content": text, "selected_paper_ids": list(paper_ids),
    }))


@pytest.mark.parametrize("entry", [0, 3, 2], ids=["newbie-search", "bring-paper", "existing-question"])
def test_complete_entry_journey_requires_confirmation_and_exports_actual_basis(
    offline_journey, entry
):
    from tests.test_research_tasks import wait_terminal

    journey, h = offline_journey, offline_journey.h
    identity = begin(h, entry, "complete-journey")
    pid, cid = identity["project_id"], identity["conversation_id"]
    root, base = f"/api/projects/{pid}", f"/api/projects/{pid}/conversations/{cid}"
    paper_ids, task_id = [], None

    if entry == 0:
        first = journey_message(journey, identity, "conditions",
            "我没有做过科研，对联邦学习的数据差异感兴趣；设备与时间还不确定。", "first-interest")
        assert first["status"] == "completed" and not workspace(h, pid)["items"]
        assert not journey.source.calls and not journey.model_calls
        # The user confirms the bounded search once; a repeated request reuses it.
        search = {"request_id": "bounded-search", "content": "确认检索联邦学习数据差异的代表性资料",
                  "topic": "联邦学习数据差异", "year_from": 2023, "year_to": 2025}
        started = ok(h.client.post(base + "/research-tasks", json=search))
        task_id = started["task_id"]
        report = wait_terminal(h, pid, cid, task_id)
        assert report["status"] == "completed", report
        assert report["year_from"] == 2023 and report["year_to"] == 2025
        assert len(report["queries"]) <= report["limits"]["max_queries"]
        assert len(report["source_calls"]) <= report["limits"]["max_queries"] * report["limits"]["sources_per_query"]
        paper_ids = report["saved_paper_ids"]
        assert paper_ids and report["report"]["representative"]
        assert {p["paper_id"] for p in report["report"]["representative"]} <= set(paper_ids)
        assert "摘要" in report["report_markdown"]
        calls = (len(journey.source.calls), len(journey.model_calls))
        assert ok(h.client.post(base + "/research-tasks", json=search))["task_id"] == task_id
        assert (len(journey.source.calls), len(journey.model_calls)) == calls
        assert not workspace(h, pid)["items"]
    elif entry == 3:
        paper_ids = [upload_readable(h, pid)]
        assert not journey.source.calls and not workspace(h, pid)["items"]

    if paper_ids:
        reading = journey_message(journey, identity, "reading",
            "请只阅读已有资料，说明阅读范围和待核查点；不要检索，不生成方案。", "read-basis", paper_ids[:1])
        saved_papers = ok(h.client.get(root + "/workspace?include_chat=false"))["papers"]
        source_paper = next(p for p in saved_papers if p["paper_id"] == paper_ids[0])
        assert source_paper["title"] in reading["messages"][-1]["content"]
        assert "阅读范围" in reading["messages"][-1]["content"]
        assert source_paper["abstract"][:40] in reading["messages"][-1]["content"]
        assert paper_ids[0] in reading["messages"][-1]["cited_paper_ids"]
        assert not workspace(h, pid)["items"]
    else:
        source_paper = None

    retrieval_calls = (len(journey.source.calls), len(journey.model_calls))
    candidate = journey_message(journey, identity, "question",
        "我的研究问题是：在相同公开任务与数据划分下如何公平比较 FedAvg 与 FedProx？\n请帮我整理为待确认的问题，先不生成方案。",
        "question-candidate", paper_ids[:1])
    proposed_question = next(s for s in candidate["suggestions"] if s["kind"] == "question")
    assert not proposed_question["applied_item_id"] and not workspace(h, pid)["items"]
    adopted_question = ok(h.client.post(base + f"/suggestions/{proposed_question['suggestion_id']}/apply",
        json={"expected_revision": workspace(h, pid)["revision"]}))["item"]
    assert adopted_question["kind"] == "question"
    assert [i["kind"] for i in workspace(h, pid)["items"]] == ["question"]

    ok(h.client.patch(base, json={"discussion_step": "design"}))
    before_plan = formal_state(h, pid)
    planned = ok(h.client.post(base + "/messages", json={
        "request_id": "explicit-plan", "content": "请为已确认问题生成计算研究方案；设备、时间、预算和数据权限仍待确认。",
        "plan_request": {"method": "computational", "question_id": adopted_question["item_id"]},
    }))
    draft = next(s for s in planned["suggestions"] if s["kind"] == "plan")
    assert not draft["applied_item_id"] and draft["plan"]["question_id"] == adopted_question["item_id"]
    assert formal_state(h, pid) == before_plan
    assert all(c["basis"] == "unverified" and c["value"] == "待确认" for c in draft["plan"]["conditions"].values())

    # Reading the review and exporting an unadopted draft are both read-only.
    before_review = tree(h.root)
    calls = len(journey.guidance_calls)
    quality = ok(h.client.get(root + f"/workspace/plan-quality?conversation_id={cid}&suggestion_id={draft['suggestion_id']}"))
    assert quality["status"] == "needs_attention" and quality["pending_conditions"]
    draft_export = h.client.get(base + f"/suggestions/{draft['suggestion_id']}/export")
    assert draft_export.status_code == 200 and "未采纳" in draft_export.text
    assert "待确认" in draft_export.text and adopted_question["title"] in draft_export.text
    if source_paper:
        assert draft["plan"]["references"] == [source_paper["paper_id"]]
        assert quality["evidence"] and all(e["matched"] for e in quality["evidence"])
        assert source_paper["title"] in draft_export.text
        assert source_paper["abstract"][:40] in draft_export.text
    else:
        assert draft["plan"]["references"] == [] and quality["evidence"] == []
        assert "文献" in draft_export.text and "待" in draft_export.text
    assert tree(h.root) == before_review and len(journey.guidance_calls) == calls
    assert formal_state(h, pid) == before_plan

    applied = ok(h.client.post(base + f"/suggestions/{draft['suggestion_id']}/apply",
        json={"expected_revision": workspace(h, pid)["revision"]}))["item"]
    final = ok(h.client.get(root + "/workspace?include_chat=false"))
    assert final["workflow"]["flow"]["planReady"] is True
    assert final["workflow"]["flow"]["plan"]["item_id"] == applied["item_id"]
    assert {i["kind"] for i in final["workspace"]["items"]} == {"question", "plan"}
    inventory = ok(h.client.get(root + "/exports"))
    output = next(e for e in inventory["entries"] if e["key"] == "plan:" + applied["item_id"])
    before_export = tree(h.root)
    exported = h.client.get(output["url"])
    assert exported.status_code == 200 and "当前正式方案" in exported.text
    assert adopted_question["title"] in exported.text and "待确认" in exported.text
    if source_paper:
        assert source_paper["title"] in exported.text and source_paper["abstract"][:40] in exported.text
    assert tree(h.root) == before_export
    assert (len(journey.source.calls), len(journey.model_calls)) == retrieval_calls
    tasks = ok(h.client.get(base + "/research-tasks"))["tasks"]
    assert [t["task_id"] for t in tasks] == ([task_id] if task_id else [])
