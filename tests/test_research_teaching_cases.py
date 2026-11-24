"""Real routers + SQLite + released packages; every external call is forbidden."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json

import pytest
from tests.test_conversation_home import home, tree
from core.workspace.conversations import ConversationService
from core.workspace.teaching import TeachingService, CASE_KEY
from core.workspace.teaching_packages import catalog


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import httpx
    import requests
    from core.llm import LLMRegistry
    import web.workspace_api as workspace_api

    def forbidden(*a, **k):
        pytest.fail("Teaching path attempted model/network/search")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(LLMRegistry, "from_config", forbidden)
    monkeypatch.setattr(workspace_api, "generate_guidance", forbidden)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def begin(h, case="fl-heterogeneity", rid="start"):
    result = ok(
        h.client.post(
            "/api/teaching-cases/start",
            json={"request_id": rid, "case_id": case, "version": "1.0.0"},
        )
    )
    return (
        f"/api/projects/{result['project_id']}/conversations/{result['conversation_id']}",
        result,
    )


def progress(h, base):
    return ok(h.client.get(base + "/teaching"))


def choose(h, base, option, rid=None):
    state = progress(h, base)
    return ok(
        h.client.post(
            base + "/teaching/choices",
            json={
                "request_id": rid or f"choice-{state['state_version']}",
                "expected_version": state["state_version"],
                "option_id": option,
            },
        )
    )


def workspace(h, result):
    return ok(
        h.client.get(
            f"/api/projects/{result['project_id']}/workspace?include_chat=false"
        )
    )["workspace"]


@pytest.mark.parametrize(
    "case,q,scope",
    [
        ("fl-heterogeneity", q, s)
        for q in ("compare", "shift")
        for s in ("small", "repeat")
    ]
    + [
        ("sulfide-design", q, s)
        for q in ("bulk", "interface")
        for s in ("narrow", "broader")
    ],
)
def test_all_real_branches_offline_explicit_adoption_and_export(home, case, q, scope):
    base, result = begin(home, case)
    assert workspace(home, result)["items"] == []
    for option in ("begin", "continue", q):
        choose(home, base, option)
    assert workspace(home, result)["items"] == []
    choose(home, base, "adopt_question")
    assert [i["kind"] for i in workspace(home, result)["items"]] == ["question"]
    choose(home, base, scope)
    choose(home, base, "show_plan")
    assert not any(i["kind"] == "plan" for i in workspace(home, result)["items"])
    state = progress(home, base)
    suggestions = ok(home.client.get(base + "/suggestions"))["suggestions"]
    plan = next(s for s in suggestions if s["kind"] == "plan")
    assert plan["plan"]["method"] == (
        "computational" if case.startswith("fl") else "experimental"
    )
    assert set(plan["paper_ids"]) == set(state["paper_map"].values())
    assert not plan["basis_changed"]
    choose(home, base, "adopt_plan")
    assert progress(home, base)["completed"]
    w = workspace(home, result)
    assert not any(i["kind"] in ("result", "finding", "outline") for i in w["items"])
    formal = next(i for i in w["items"] if i["kind"] == "plan")
    assert formal["plan_version"] == 1
    exported = home.client.get(base + "/teaching/export")
    assert exported.status_code == 200
    for text in (
        "教学案例",
        "1.0.0",
        "未验证",
        "来源",
        "教学设定",
        scope == "small" and "固定任务" or "待确认",
    ):
        assert text in exported.text
    assert (
        not home.config.paths.project_dir(result["project_id"])
        .joinpath("experiments")
        .exists()
    )


def test_directory_readonly_and_start_durable_idempotency(home):
    before = tree(home.root)
    assert len(ok(home.client.get("/api/teaching-cases"))["cases"]) == 2
    assert tree(home.root) == before
    base, a = begin(home)
    assert begin(home)[1]["project_id"] == a["project_id"]
    assert len(ok(home.client.get(base + "/messages"))["messages"]) == 1
    r = home.client.post(
        "/api/teaching-cases/start",
        json={"request_id": "start", "case_id": "sulfide-design", "version": "1.0.0"},
    )
    assert r.status_code == 409


def test_explanation_no_research_mutation_and_choice_replays(home):
    base, result = begin(home)
    before = workspace(home, result)
    body = {"request_id": "explain", "expected_version": 0, "option_id": "explain"}
    a = ok(home.client.post(base + "/teaching/choices", json=body))
    assert a["step"] == 0 and workspace(home, result) == before
    assert ok(home.client.post(base + "/teaching/choices", json=body))["replayed"]
    assert (
        home.client.post(
            base + "/teaching/choices", json={**body, "option_id": "begin"}
        ).status_code
        == 409
    )
    assert (
        home.client.post(
            base + "/teaching/choices", json={**body, "request_id": "stale"}
        ).status_code
        == 409
    )
    assert (
        home.client.post(
            base + "/teaching/choices",
            json={
                "request_id": "skip",
                "expected_version": 1,
                "option_id": "adopt_plan",
            },
        ).status_code
        == 422
    )


def test_concurrent_choice_one_advance(home):
    base, _ = begin(home)
    body = {"request_id": "double", "expected_version": 0, "option_id": "begin"}
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                lambda _: home.client.post(base + "/teaching/choices", json=body),
                range(2),
            )
        )
    assert [r.status_code for r in responses] == [200, 200]
    assert progress(home, base)["state_version"] == 1
    assert len(ok(home.client.get(base + "/messages"))["messages"]) == 3


def test_isolation_restart_free_question_and_fork(home):
    base, a = begin(home)
    other, b = begin(home, rid="second")
    choose(home, base, "begin")
    assert progress(home, other)["step"] == 0
    chat = ConversationService(
        a["project_id"], home.config.paths.project_db(a["project_id"])
    )
    assert TeachingService(chat).view(a["conversation_id"])["step"] == 1
    data = {"request_id": "free", "content": "帮我实时检索论文并开始实验"}
    r = ok(home.client.post(base + "/messages", json=data))
    assert (
        r["status"] == "completed"
        and "未调用模型或检索" in r["messages"][-1]["content"]
    )
    assert (
        ok(home.client.post(base + "/messages", json=data))["messages"] == r["messages"]
    )
    assert (
        home.client.post(
            base + "/messages", json={**data, "content": "其他"}
        ).status_code
        == 409
    )
    assert (
        home.client.get(
            f"/api/projects/{a['project_id']}/conversations/{b['conversation_id']}/teaching"
        ).status_code
        == 404
    )
    request = {"request_id": "fork", "title": "我的新主题", "resources": "只有 CPU"}
    fresh = ok(home.client.post(base + "/teaching/fork", json=request))
    assert (
        ok(home.client.post(base + "/teaching/fork", json=request))["project_id"]
        == fresh["project_id"]
    )
    w = workspace(home, fresh)
    assert w["brief"]["resources"] == "只有 CPU" and not w["brief"]["time_budget"]
    assert not any(i["kind"] in ("question", "plan") for i in w["items"])
    assert not w["focus"]["question"]
    assert progress(home, base)["step"] == 1


def test_package_failure_no_project_and_immutable_binding(home, tmp_path, monkeypatch):
    import core.workspace.teaching_packages as packages

    base, a = begin(home)
    p = deepcopy(packages.load_package("fl-heterogeneity", "1.0.0"))
    root = tmp_path / "packages"
    (root / "fl-heterogeneity").mkdir(parents=True)
    # Test-only generated fixture, never publication data.
    (root / "fl-heterogeneity" / "1.0.0.json").write_text(
        json.dumps(p), encoding="utf-8"
    )
    manifest = {"fl-heterogeneity/1.0.0": packages.fingerprint(p)}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(packages, "ROOT", root)
    assert progress(home, base)["version"] == "1.0.0"
    p["title"] = "Changed released version"
    (root / "fl-heterogeneity" / "1.0.0.json").write_text(
        json.dumps(p), encoding="utf-8"
    )
    before = tree(home.root)
    assert home.client.get(base + "/teaching").status_code == 422
    assert (
        home.client.post(
            "/api/teaching-cases/start",
            json={
                "case_id": "fl-heterogeneity",
                "version": "1.0.0",
                "request_id": "broken",
            },
        ).status_code
        == 422
    )
    assert tree(home.root) == before


def test_revise_scope_keeps_plan_versions_and_review_state(home):
    base, result = begin(home)
    for o in (
        "begin",
        "continue",
        "compare",
        "adopt_question",
        "small",
        "show_plan",
        "adopt_plan",
    ):
        choose(home, base, o)
    first = next(i for i in workspace(home, result)["items"] if i["kind"] == "plan")
    for o in ("scope_back", "repeat"):
        choose(home, base, o)
    assert next(
        i for i in workspace(home, result)["items"] if i["item_id"] == first["item_id"]
    )["needs_review"]
    for o in ("show_plan", "adopt_plan"):
        choose(home, base, o)
    plans = [i for i in workspace(home, result)["items"] if i["kind"] == "plan"]
    assert len(plans) == 2 and plans[0]["status"] == "superseded"
    assert (
        plans[1]["plan_version"] == 2 and plans[1]["supersedes_id"] == first["item_id"]
    )


def test_external_material_change_blocks_plan_adoption(home):
    from core.knowledge.schema import PaperChunk

    base, result = begin(home)
    for o in ("begin", "continue", "compare", "adopt_question", "small", "show_plan"):
        choose(home, base, o)
    chat = ConversationService(
        result["project_id"], home.config.paths.project_db(result["project_id"])
    )
    paper = chat.shared.store.list_papers()[0]
    # Explicit test fixture write: a newly supplied excerpt changes the evidence basis.
    from core.knowledge import KnowledgeStore

    KnowledgeStore(chat.db_path).save_paper_chunks(
        [
            PaperChunk(
                chunk_id="changed",
                paper_id=paper.paper_id,
                chunk_index=0,
                text="corrected evidence",
            )
        ]
    )
    s = progress(home, base)
    assert not next(o for o in s["options"] if o["id"] == "adopt_plan")["enabled"]
    before = tree(home.root)
    rejected = home.client.post(
        base + "/teaching/choices",
        json={
            "request_id": "stale-plan",
            "expected_version": s["state_version"],
            "option_id": "adopt_plan",
        },
    )
    assert rejected.status_code == 409 and tree(home.root) == before
    assert not any(i["kind"] == "plan" for i in workspace(home, result)["items"])


def test_atomic_failure_after_adoption_rolls_back_everything(home, monkeypatch):
    from core.workspace.service import WorkspaceError

    base, result = begin(home)
    for o in ("begin", "continue", "compare"):
        choose(home, base, o)
    before = tree(home.root)
    original = TeachingService._message

    def fail(*args, **kwargs):
        raise WorkspaceError("injected transaction failure")

    monkeypatch.setattr(TeachingService, "_message", fail)
    s = progress(home, base)
    r = home.client.post(
        base + "/teaching/choices",
        json={
            "request_id": "atomic",
            "expected_version": s["state_version"],
            "option_id": "adopt_question",
        },
    )
    assert r.status_code == 422
    assert tree(home.root) == before
    assert not workspace(home, result)["items"]
    monkeypatch.setattr(TeachingService, "_message", original)
    choose(home, base, "adopt_question", "atomic")
    assert len(workspace(home, result)["items"]) == 1


def test_concurrent_external_and_step_adoption_is_one_record(home):
    base, result = begin(home)
    for o in ("begin", "continue", "compare"):
        choose(home, base, o)
    state = progress(home, base)
    with ThreadPoolExecutor(2) as pool:
        calls = [
            pool.submit(
                home.client.post,
                base + "/teaching/choices",
                json={
                    "request_id": "adopt-step",
                    "expected_version": state["state_version"],
                    "option_id": "adopt_question",
                },
            ),
            pool.submit(
                home.client.post,
                base + "/suggestions/" + state["question_suggestion"] + "/apply",
                json={},
            ),
        ]
        assert all(c.result().status_code == 200 for c in calls)
    assert len(workspace(home, result)["items"]) == 1


def test_new_package_version_does_not_replace_bound_version(
    home, tmp_path, monkeypatch
):
    import core.workspace.teaching_packages as packages

    base, result = begin(home)
    original = packages.load_package("fl-heterogeneity", "1.0.0")
    updated = deepcopy(original)
    updated["version"] = "1.1.0"
    updated["title"] = "Future version"
    root = tmp_path / "releases"
    (root / "fl-heterogeneity").mkdir(parents=True)
    manifest = {}
    for p in (original, updated):
        (root / "fl-heterogeneity" / (p["version"] + ".json")).write_text(
            json.dumps(p), encoding="utf-8"
        )
        manifest["fl-heterogeneity/" + p["version"]] = packages.fingerprint(p)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(packages, "ROOT", root)
    before = tree(home.root)
    assert progress(home, base)["version"] == "1.0.0"
    releases = ok(home.client.get("/api/teaching-cases"))["cases"]
    assert (
        len(releases) == 1
    )  # Directory offers one latest release, not duplicate lessons.
    assert releases[0]["version"] == "1.1.0"
    assert packages.load_package("fl-heterogeneity", "1.0.0") == original
    assert tree(home.root) == before


def test_missing_db_forged_ids_and_uncovered_settings_cannot_mutate(home):
    base, result = begin(home)
    before = tree(home.root)
    for extra in ({"selected_paper_ids": ["foreign"]}, {"selected_item_id": "foreign"}):
        assert (
            home.client.post(
                base + "/messages",
                json={"request_id": "forged", "content": "read", **extra},
            ).status_code
            == 422
        )
    assert (
        home.client.post(
            base + "/teaching/choices",
            json={
                "request_id": "fake",
                "expected_version": 0,
                "option_id": "adopt_question",
                "next": 6,
            },
        ).status_code
        == 422
    )
    assert tree(home.root) == before
    db = home.config.paths.project_db(result["project_id"])
    # Move ONLY a temporary test DB; GET must not replace it.
    db.rename(db.with_suffix(".backup"))
    before = tree(home.root)
    assert home.client.get(base + "/teaching").status_code == 404
    assert not db.exists() and tree(home.root) == before


def test_question_change_preserves_records_and_invalidates_plan(home):
    base, result = begin(home)
    for o in (
        "begin",
        "continue",
        "compare",
        "adopt_question",
        "small",
        "show_plan",
        "adopt_plan",
    ):
        choose(home, base, o)
    old = workspace(home, result)
    for o in ("back", "shift", "adopt_question"):
        choose(home, base, o)
    w = workspace(home, result)
    assert len([i for i in w["items"] if i["kind"] == "question"]) == 2
    assert next(i for i in w["items"] if i["kind"] == "plan")["needs_review"]
    assert w["focus"]["question"] != old["focus"]["question"]


def test_search_service_does_not_even_register_a_teaching_task(home):
    from core.research_tasks.service import ResearchTaskService, SearchTaskRequest
    from core.workspace.service import WorkspaceError

    base, result = begin(home)
    chat = ConversationService(
        result["project_id"], home.config.paths.project_db(result["project_id"])
    )
    before = tree(home.root)
    with pytest.raises(WorkspaceError, match="教学项目"):
        ResearchTaskService(chat.project_id, chat.db_path, chat).start(
            result["conversation_id"],
            SearchTaskRequest(
                topic="federated learning",
                content="搜索联邦学习",
                request_id="no-search",
            ),
        )
    assert tree(home.root) == before


def test_main_app_old_execution_endpoints_reject_before_generation(home):
    from fastapi.testclient import TestClient

    base, result = begin(home)
    before = tree(home.root)
    with TestClient(home.api.app, raise_server_exceptions=False) as main:
        for tail in (
            "run",
            "resume",
            "run-topic-discovery",
            "regenerate-method-doc",
            "human-response",
            "papers/import",
            "workspace/guide",
            "conversations/" + result["conversation_id"] + "/research-tasks",
        ):
            response = main.post(
                f"/api/projects/{result['project_id']}/" + tail, json={}
            )
            assert response.status_code == 422, (tail, response.text)
            assert "教学" in response.text
    assert tree(home.root) == before


def test_recovery_after_registry_restart_and_gets_never_write(home):
    base, result = begin(home)
    choose(home, base, "begin")
    original = tree(home.root)
    for path in (base + "/teaching", base + "/messages", base + "/suggestions"):
        assert home.client.get(path).status_code == 200
    assert tree(home.root) == original
    home.api._PROJECTS.clear()
    restored_base, restored = begin(home)
    assert restored_base == base and restored["teaching"]["step"] == 1
    assert len(ok(home.client.get(base + "/messages"))["messages"]) == 3


def test_missing_snapshot_and_forged_case_suggestion_rejected(home):
    base, result = begin(home)
    for o in ("begin", "continue", "compare"):
        choose(home, base, o)
    other, other_result = begin(home, rid="other")
    for o in ("begin", "continue", "compare"):
        choose(home, other, o)
    sid = progress(home, other)["question_suggestion"]
    before = tree(home.root)
    assert (
        home.client.post(base + "/suggestions/" + sid + "/apply", json={}).status_code
        == 404
    )
    assert tree(home.root) == before
    state = progress(home, base)
    chat = ConversationService(
        result["project_id"], home.config.paths.project_db(result["project_id"])
    )
    with chat._connection(True) as conn:
        conn.execute(
            "UPDATE sra_chat_suggestions SET material_fingerprint=NULL WHERE suggestion_id=?",
            (state["question_suggestion"],),
        )
    assert not next(
        o for o in progress(home, base)["options"] if o["id"] == "adopt_question"
    )["enabled"]
    before = tree(home.root)
    r = home.client.post(
        base + "/teaching/choices",
        json={
            "request_id": "missing-basis",
            "expected_version": state["state_version"],
            "option_id": "adopt_question",
        },
    )
    assert r.status_code == 409 and tree(home.root) == before


def test_case_exports_use_existing_catalog_and_keep_provenance(home):
    from web.project_exports_api import build_exports_router

    api = home.api
    home.client.app.include_router(
        build_exports_router(
            lambda: api._CONFIG,
            api._require_project,
            lambda p: api._workspace_workflow(p, include_materials=True),
            api.download_artifact,
            api._make_download,
            api._md_to_docx_response,
            api._md_to_pdf_response,
        )
    )
    base, result = begin(home)
    for o in ("begin", "continue", "compare", "adopt_question", "small", "show_plan"):
        choose(home, base, o)
    pid = result["project_id"]
    draft_id = progress(home, base)["plan_suggestion"]
    inventory = ok(
        home.client.get(
            f"/api/projects/{pid}/exports?conversation_id={result['conversation_id']}"
        )
    )
    assert not any(e.get("label") == "当前正式方案" for e in inventory["entries"])
    assert not any(i["kind"] == "plan" for i in workspace(home, result)["items"])
    raw = home.client.get(base + "/suggestions/" + draft_id + "/export").text
    assert "未采纳讨论稿" in raw and "教学设定" in raw and "1.0.0" in raw
    choose(home, base, "adopt_plan")
    item = next(i for i in workspace(home, result)["items"] if i["kind"] == "plan")
    raw = home.client.get(f"/api/projects/{pid}/exports/items/{item['item_id']}").text
    assert all(t in raw for t in ("教学案例", "1.0.0", "版本：1", "来源", "未验证"))
    assert home.client.get(f"/api/projects/{pid}/workspace/export").text.startswith(
        "教学案例"
    )


def test_request_ids_cannot_cross_choice_and_message_namespaces(home):
    base, result = begin(home)
    choose(home, base, "begin", "shared")
    assert (
        home.client.post(
            base + "/messages", json={"request_id": "shared", "content": "other"}
        ).status_code
        == 409
    )
    ok(
        home.client.post(
            base + "/messages", json={"request_id": "free", "content": "what"}
        )
    )
    state = progress(home, base)
    assert (
        home.client.post(
            base + "/teaching/choices",
            json={
                "request_id": "free",
                "expected_version": state["state_version"],
                "option_id": "continue",
            },
        ).status_code
        == 409
    )


def test_fork_carries_draft_only_as_reference_with_new_source_ids(home):
    base, result = begin(home)
    for option in (
        "begin",
        "continue",
        "compare",
        "adopt_question",
        "small",
        "show_plan",
    ):
        choose(home, base, option)
    source_ids = set(progress(home, base)["paper_map"].values())
    preview = progress(home, base)["reference_preview"]
    assert preview["question"]["title"] and "FedAvg" in preview["plan"]["content"]
    request = {
        "request_id": "fork-draft",
        "title": "自己的问题",
        "resources": "尚未确定",
    }
    fresh = ok(home.client.post(base + "/teaching/fork", json=request))
    again = ok(home.client.post(base + "/teaching/fork", json=request))
    assert fresh["project_id"] == again["project_id"] != result["project_id"]
    view = ok(
        home.client.get(
            f"/api/projects/{fresh['project_id']}/workspace?include_chat=false"
        )
    )
    w = view["workspace"]
    assert not view.get("teaching")
    assert all(i["kind"] == "note" for i in w["items"])
    assert len(w["items"]) == 2
    assert not w["focus"]["question"] and not w["focus"]["plan"]
    assert w["brief"]["resources"] == "尚未确定" and not w["brief"]["time_budget"]
    new_ids = {p["paper_id"] for p in view["papers"]}
    assert len(new_ids) == 3 and source_ids.isdisjoint(new_ids)
    for item in w["items"]:
        assert set(item["paper_ids"]) == new_ids
        assert not any(old in item["content"] for old in source_ids)
    assert "未采纳" in w["items"][-1]["content"]


@pytest.mark.parametrize(
    "mutation",
    ["skip_confirmation", "unknown_action", "missing_confirm", "unreachable_question"],
)
def test_package_semantic_transitions_are_validated(mutation):
    from core.workspace.teaching_packages import load_package, validate_package

    p = deepcopy(load_package("fl-heterogeneity", "1.0.0"))
    if mutation == "skip_confirmation":
        p["steps"][2]["options"][0]["next"] = 4
    elif mutation == "unknown_action":
        p["steps"][1]["options"].append(
            {"id": "execute", "label": "执行实验", "next": 6}
        )
    elif mutation == "missing_confirm":
        p["steps"][3]["options"] = p["steps"][3]["options"][1:]
    else:
        p["steps"][2]["options"] = p["steps"][2]["options"][1:]
    with pytest.raises(ValueError):
        validate_package(p)
