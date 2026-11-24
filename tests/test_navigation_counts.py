"""菜单计数必须与相应页面使用同一项目、同一数据口径。"""
from pathlib import Path
import pytest

from core.config import GlobalConfig, ProjectPaths
from core.knowledge import KnowledgeStore
from core.knowledge.schema import ResearchGap


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD", "1")
    from web import api
    config = GlobalConfig(paths=ProjectPaths.from_root(tmp_path))
    state = api.ProjectState(project_id="navigation-fixture", topic="界面计数软件测试", created_at="2026-10-17")
    monkeypatch.setattr(api, "_CONFIG", config)
    monkeypatch.setattr(api, "_require_project", lambda pid: state)
    store = KnowledgeStore(config.paths.project_db(state.project_id))
    return api, store, state


def test_report_badge_uses_report_items_not_all_research_gaps(project):
    api, store, state = project
    for index in range(7):
        store.save_research_gap(ResearchGap(gap_id=f"g{index}", statement="软件测试缺口"))
    store.save_kv("cross_validation_report", {"gaps": ["报告问题一", "报告问题二"]})
    status = api.get_status(state.project_id)
    assert status["counts"]["gaps"] == 7
    assert status["counts"]["report_gaps"] == len(api.get_research_report(state.project_id)["report"]["gaps"]) == 2
    assert status['material_counts']['research_reports']==1


def test_status_notes_count_matches_deduplicated_list_without_writing(project):
    api, store, state = project
    saved = [{"note_id": "one", "text": "已保存"}]
    store.save_kv("user.notes", saved)
    state.notes = [saved[0], {"note_id": "two", "text": "内存中的未重复笔记"}]
    assert api.get_status(state.project_id)["notes_count"] == 2
    assert store.get_kv("user.notes") == saved
    assert len(api.list_notes(state.project_id)["notes"]) == 2
    assert api.get_status(state.project_id)["notes_count"] == 2


def test_zero_counts_are_explicit_and_new_note_updates_status(project):
    api, _, state = project
    status = api.get_status(state.project_id)
    assert status["counts"]["report_gaps"] == 0
    assert status["notes_count"] == 0
    api.add_note(state.project_id, api.NoteRequest(text="软件测试笔记"))
    assert api.get_status(state.project_id)["notes_count"] == 1
    assert len(api.list_notes(state.project_id)["notes"]) == 1


def test_page_renderers_do_not_directly_overwrite_navigation_badges():
    js = (Path(__file__).resolve().parents[1] / "web/static/app.js").read_text(encoding="utf-8")
    # 所有菜单都通过唯一 updateBadges 入口展示，页面加载只更新数据。
    assert js.count('setBadge("badge-report-gaps",') == 1
    assert js.count('setBadge("badge-notes",') == 1
    assert 'state.notesCount ?? data.notes_count ?? null' in js
    # Legacy report_gaps remains in HTTP counts; the actual navigation now
    # counts saved reports, not candidate questions or versions.
    assert 'materialCounts?.research_reports ?? null' in js
    assert 'else refreshReportBadgeCount(pid)' in js
