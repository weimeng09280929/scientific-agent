"""增量改造契约：原页面、灵感笔记和已有研究材料继续使用。"""
from pathlib import Path
from types import SimpleNamespace

from core.config import ProjectPaths
from core.knowledge.schema import Claim, Experiment, Idea, ResearchGap
from core.workspace.guide import build_guide_context
from core.workspace.service import WorkspaceService


def test_guide_uses_existing_system_materials_without_treating_them_as_verified(tmp_path):
    service = WorkspaceService("p1", tmp_path / "knowledge.db")
    service.store.save_idea(Idea(idea_id="idea1", text="已有研究设想"))
    service.store.save_claim(Claim(claim_id="claim1", statement="已有待验证论断"))
    service.store.save_experiment(Experiment(experiment_id="exp1", name="原系统试跑"))
    service.store.save_research_gap(ResearchGap(gap_id="gap1", statement="原系统发现的待核实问题"))
    context = build_guide_context(service, "联邦学习", "question", None, [], legacy_notes=[{"note_id":"n1", "text":"我的原始灵感"}])
    old = context["existing_system"]
    assert old["notes"][0]["text"] == "我的原始灵感"
    assert old["ideas"][0]["idea_id"] == "idea1"
    assert old["claims"][0]["status"] == "draft"
    assert old["experiments"][0]["experiment_id"] == "exp1"
    assert old["gaps"][0]["gap_id"] == "gap1"
    assert "非独立核验" in old["scope"]


def test_original_notes_survive_state_recreation(monkeypatch, tmp_path):
    from web import api
    project = SimpleNamespace(notes=[{"note_id":"old", "text":"已有灵感", "created_at":"2026-10-16"}])
    monkeypatch.setattr(api, "_require_project", lambda pid: project)
    monkeypatch.setattr(api, "_CONFIG", SimpleNamespace(paths=ProjectPaths.from_root(tmp_path)))
    api.add_note("p1", api.NoteRequest(text="新增指导笔记"))
    project.notes = []
    notes = api.list_notes("p1")["notes"]
    assert [n["text"] for n in notes] == ["已有灵感", "新增指导笔记"]
    assert api.list_notes("p1")["notes"] == notes


def test_primary_interface_keeps_original_navigation_and_only_adds_guide():
    from web import api
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/static/index.html").read_text(encoding="utf-8")
    for name in ('id="nav"', 'id="flow-nav-host"', 'data-page="notes"', 'id="sidebar-notes"', 'data-page="papers"', 'data-page="human"'):
        assert name in html
    assert 'href="/workspace"' not in html
    assert 'id="research-guide"' in html
    assert Path(api.research_workspace().path).name == "index.html"
