"""理工科范围契约：隔离项目数据，不调用模型，不启动真实后台任务。"""

from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from core.config import GlobalConfig, ProjectPaths
from core.knowledge import KnowledgeStore
from core.knowledge.domain_profile import get_domain_profile, build_domain_hint
from core.research_scope import PUBLIC_DOMAINS, normalize_public_domain
from core.workspace.guide import build_guide_system

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scoped_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD", "1")
    from web import api

    monkeypatch.setattr(
        api,
        "_CONFIG",
        GlobalConfig(paths=ProjectPaths.from_root(tmp_path), dry_run=True),
    )
    monkeypatch.setattr(api, "_PROJECTS", {})
    monkeypatch.setattr(api, "_METHOD_REGEN_JOBS", {})
    # The production workspace router captures its config at import time. Rebuild
    # it explicitly rather than accidentally using the user's project directory.
    from web.workspace_api import build_workspace_router

    app = FastAPI()
    for path, endpoint, methods in [
        ("/api/projects", api.create_project, ["POST"]),
        ("/api/projects/{project_id}/run", api.run_project, ["POST"]),
        (
            "/api/projects/{project_id}/run-topic-discovery",
            api.run_topic_discovery,
            ["POST"],
        ),
        (
            "/api/projects/{project_id}/regenerate-method-doc",
            api.regenerate_method_doc,
            ["POST"],
        ),
        ("/api/projects/{project_id}/notes", api.list_notes, ["GET"]),
        ("/api/projects/{project_id}/status", api.get_status, ["GET"]),
    ]:
        app.add_api_route(path, endpoint, methods=methods)
    app.include_router(build_workspace_router(api._CONFIG, api._require_project))
    return api, TestClient(app)


def test_public_catalog_matches_both_creation_forms():
    assert set(PUBLIC_DOMAINS) == {
        "computer_science",
        "ai",
        "security",
        "engineering",
        "materials",
        "physics",
        "chemistry",
        "energy",
        "mathematics",
    }
    js = (ROOT / "web/static/app.js").read_text(encoding="utf-8")
    catalog = js.split("const RESEARCH_DOMAINS = [", 1)[1].split("];", 1)[0]
    assert (
        dict(re.findall(r'value: "([^"]+)", label: "([^"]+)"', catalog))
        == PUBLIC_DOMAINS
    )
    html = (ROOT / "web/static/workspace.html").read_text(encoding="utf-8")
    select = html.split('<select name="domain">', 1)[1].split("</select>", 1)[0]
    assert (
        dict(re.findall(r'<option value="([^"]+)">([^<]+)</option>', select))
        == PUBLIC_DOMAINS
    )
    assert 'id: "domain-custom-input"' not in js
    assert "也可换任意领域" not in js


@pytest.mark.parametrize(
    "domain",
    [
        "ai",
        "computer_science",
        "security",
        "engineering",
        "materials",
        "physics",
        "chemistry",
        "energy",
        "mathematics",
        "",
        "cs",
        "人工智能",
        "MATH",
        "材料科学",
        "stem:LLZO 固态电解质",
    ],
)
def test_create_canonical_stem_domain_and_persist(scoped_api, domain):
    api, client = scoped_api
    response = client.post(
        "/api/projects", json={"topic": "范围测试，不是真实科研", "domain": domain}
    )
    assert response.status_code == 200
    project = response.json()
    assert project["domain"] == normalize_public_domain(domain)
    store = KnowledgeStore(api._CONFIG.paths.project_db(project["project_id"]))
    assert store.get_kv("project.domain") == project["domain"]


@pytest.mark.parametrize(
    "domain",
    [
        "medicine",
        "biology",
        "custom",
        "医学",
        "finance",
        "stem:临床物理治疗",
        "stem:药物材料",
        "stem:人体实验",
        "stem:炸药化学",
        "stem:无法识别的方向",
        "stem:",
    ],
)
def test_unsupported_create_is_rejected_before_project_write(scoped_api, domain):
    api, client = scoped_api
    response = client.post(
        "/api/projects", json={"topic": "不开放范围测试", "domain": domain}
    )
    assert response.status_code == 422
    assert any(word in response.json()["detail"] for word in ["历史", "澄清"])
    assert api._PROJECTS == {}
    assert not list(api._CONFIG.paths.root.rglob("*.db"))


def test_legacy_project_stays_readable_but_does_not_start_generation(scoped_api):
    api, client = scoped_api
    pid = "legacy-scope-fixture"
    state = api.ProjectState(
        project_id=pid,
        topic="历史材料（软件测试）",
        domain="medicine",
        created_at="2026-10-17",
    )
    api._PROJECTS[pid] = state
    store = KnowledgeStore(api._CONFIG.paths.project_db(pid))
    store.save_kv("project.domain", "medicine")
    store.save_kv("research.topic", state.topic)
    store.save_kv("user.notes", [{"note_id": "old", "text": "历史笔记（软件测试）"}])
    assert (
        client.put(
            f"/api/projects/{pid}/workspace/brief",
            json={"expected_revision": 0, "brief": {"goal": "保留历史记录"}},
        ).status_code
        == 200
    )
    before = client.get(f"/api/projects/{pid}/workspace").json()
    assert before["research_scope"]["supported"] is False
    for suffix, payload in [
        ("run", {}),
        ("run-topic-discovery", {"interest": "历史主题", "domain": "medicine"}),
        ("run-topic-discovery", {"interest": "尝试静默改领域", "domain": "ai"}),
        ("regenerate-method-doc", {}),
        (
            "workspace/guide",
            {"expected_revision": 0, "area": "overview", "message": "生成新方案"},
        ),
    ]:
        response = client.post(f"/api/projects/{pid}/{suffix}", json=payload)
        assert response.status_code == 422, (suffix, response.text)
    assert (
        state.thread is None
        and state.status == "created"
        and state.domain == "medicine"
    )
    assert api._METHOD_REGEN_JOBS == {}
    assert store.get_kv("project.domain") == "medicine"
    assert (
        client.get(f"/api/projects/{pid}/workspace").json()["workspace"]
        == before["workspace"]
    )
    assert (
        client.get(f"/api/projects/{pid}/notes").json()["notes"][0]["text"]
        == "历史笔记（软件测试）"
    )
    assert client.get(f"/api/projects/{pid}/workspace/export").status_code == 200
    assert client.get(f"/api/projects/{pid}/status").status_code == 200


def test_supported_project_cannot_switch_to_unsupported_discovery(scoped_api):
    api, client = scoped_api
    pid = client.post(
        "/api/projects", json={"topic": "RAG 引用评测", "domain": "ai"}
    ).json()["project_id"]
    response = client.post(
        f"/api/projects/{pid}/run-topic-discovery",
        json={"interest": "越界测试", "domain": "medicine"},
    )
    assert response.status_code == 422
    assert api._PROJECTS[pid].domain == "ai" and api._PROJECTS[pid].thread is None


def test_computing_profiles_use_distinct_analysis_contexts_and_keep_legacy():
    software = get_domain_profile("computer_science")
    security = get_domain_profile("security")
    assert "负载" in software["metric"] and "威胁" in security["entity"]
    assert software["topic_hint"] != security["topic_hint"]
    assert get_domain_profile("medicine")["label"] == "医学与药学"
    assert "能力边界" in build_domain_hint("security")


@pytest.mark.parametrize(
    "area", ["overview", "literature", "question", "study", "writing"]
)
def test_guidance_states_capability_boundary(area):
    system = build_guide_system(area, "ai")
    assert "不启动实验或训练" in system and "暂不支持医学" in system
    assert "明确授权" in system and "不生成操作方案" in system


def test_aliases_and_unrecognized_custom_do_not_infer_wrong_discipline():
    assert normalize_public_domain(" Materials Science ") == "materials"
    assert normalize_public_domain("stem:LLZO 固态电解质") == "stem:LLZO 固态电解质"
    assert normalize_public_domain("物理学") == "physics"
    with pytest.raises(ValueError, match="澄清"):
        normalize_public_domain("stem:我还不知道")
