"""Real saved report snapshots and retrieved abstracts remain usable after search."""
from copy import deepcopy

import pytest

from core.knowledge.schema import Paper, PaperChunk
from core.research_tasks.coverage import coverage_facts
from core.research_tasks.service import ResearchTaskService, SearchTaskRequest, encode
from core.workspace.conversations import ConversationService
from core.workspace.guide import build_guide_context
from core.workspace.research_coach import build_research_route
from tests.test_project_conversations import tree
from tests.test_research_tasks import rt, create, make_paper, start_via_message, wait_terminal


def seeded_versions(rt, *, with_coverage=True, current_status="completed"):
    cid = create(rt)
    chat = ConversationService("p1", rt.config.paths.project_db("p1"))
    service = ResearchTaskService("p1", chat.db_path, chat, runner=lambda fn: None)
    task = service.start(cid, SearchTaskRequest(content="offline fixture search", topic="solid electrolyte", request_id="snapshot"))
    old_queries = [{"query": "old query", "rationale": "old scope"}]
    new_queries = [{"query": "future query", "rationale": "future scope"}]
    old_calls = [{"source": "old-source", "query": "old query", "status": "error", "count": 0, "error": "offline fixture failure"}]
    new_calls = [{"source": "future-source", "query": "future query", "status": "ok", "count": 1}]
    first = {"summary": "VERSION_ONE_BODY", "representative": [{"paper_id": "old-paper", "point": "OLD_PAPER_POINT"}], "limitations": [], "gaps": [], "next_steps": [], "mode": "rules"}
    second = {"summary": "VERSION_TWO_BODY", "representative": [{"paper_id": "new-paper", "point": "NEW_PAPER_POINT"}], "limitations": [], "gaps": [], "next_steps": [], "mode": "rules"}
    if with_coverage:
        first["coverage"] = coverage_facts(old_queries, old_calls, [], limits=service.limits.as_dict())
        second["coverage"] = coverage_facts(new_queries, new_calls, [], limits=service.limits.as_dict())
    versions = [{"version": 1, "status": "partial", "report": first, "delivered_at": "2026-10-27T01:00:00+00:00"},
                {"version": 2, "status": "completed", "report": second, "delivered_at": "2026-10-27T02:00:00+00:00"}]
    service._update(task["task_id"], status=current_status, stage="done", report=encode(second),
                    source_calls=encode(new_calls), planned_queries=encode(new_queries), report_versions=encode(versions))
    return service, task["task_id"], versions


@pytest.mark.parametrize("current_status", ["completed", "running", "cancelled", "failed"])
def test_historical_export_uses_saved_body_scope_and_state_without_current_substitution(rt, current_status):
    service, tid, versions = seeded_versions(rt, current_status=current_status)
    before = tree(rt.root)
    old = rt.client.get(f"/api/projects/p1/research-tasks/{tid}/export?version=1")
    assert old.status_code == 200, old.text
    assert "VERSION_ONE_BODY" in old.text and "OLD_PAPER_POINT" in old.text
    assert "VERSION_TWO_BODY" not in old.text and "NEW_PAPER_POINT" not in old.text
    assert "状态：部分完成" in old.text and "状态：已完成" not in old.text
    assert "old query" in old.text and "old-source" in old.text
    assert "future query" not in old.text and "future-source" not in old.text
    assert "报告版本 v1" in old.text
    assert service.report_version(tid, 1)["report"] == versions[0]["report"]
    assert all("report" not in version for version in service.get(tid)["report_versions"])
    assert rt.client.get(f"/api/projects/p1/research-tasks/{tid}/export?version=99").status_code == 404
    assert rt.client.get(f"/api/projects/p2/research-tasks/{tid}/export?version=1").status_code == 404
    assert tree(rt.root) == before and not rt.control.calls


def test_legacy_snapshot_without_scope_does_not_borrow_future_source_history(rt):
    _, tid, _ = seeded_versions(rt, with_coverage=False)
    old = rt.client.get(f"/api/projects/p1/research-tasks/{tid}/export?version=1")
    assert old.status_code == 200 and "VERSION_ONE_BODY" in old.text
    assert "未保存查询及来源调用快照" in old.text
    assert "future query" not in old.text and "future-source" not in old.text


def test_unreadable_snapshot_fails_explicitly_instead_of_exporting_current_report(rt):
    service, tid, versions = seeded_versions(rt)
    versions[0]["report"] = None
    service._update(tid, report_versions=encode(versions))
    result = rt.client.get(f"/api/projects/p1/research-tasks/{tid}/export?version=1")
    assert result.status_code == 503 and "未用当前报告替代" in result.text
    assert "VERSION_TWO_BODY" not in result.text


def test_retrieved_abstract_supplements_metadata_only_paper_and_unblocks_reading(rt):
    service = rt.services["p1"]
    service.store.save_paper(Paper(paper_id="library1", title="User supplied title", doi="10.1/existing",
                                   authors=["User supplied author"], metadata={"user_note": "Keep this note"}))
    original_basis = ConversationService("p1", service.db_path).material_state()["fingerprint"]
    abstract = "The source reports impedance measurements under a stated temperature."
    rt.control.default = lambda name, query, seq: [make_paper(seq, query, doi="10.1/existing", abstract=abstract)]
    cid = create(rt)
    task = start_via_message(rt, cid)
    result = wait_terminal(rt, "p1", cid, task["task_id"])
    assert result["status"] == "completed" and result["saved_paper_ids"] == ["library1"]
    paper = service.store.get_paper("library1")
    assert paper.abstract == abstract and paper.title == "User supplied title" and paper.authors == ["User supplied author"]
    assert paper.metadata["user_note"] == "Keep this note"
    assert "摘要" in paper.metadata["reading_scope"] and "未核对全文" in paper.metadata["reading_scope"]
    context = build_guide_context(service, "solid electrolyte", "literature", None, ["library1"], history=[])
    assert context["papers"][0]["abstract"] == abstract and "未核对全文" in context["papers"][0]["scope"]
    assert build_research_route(context, "Read this paper")["step"] == "read_and_gap"
    assert ConversationService("p1", service.db_path).material_state()["fingerprint"] != original_basis


@pytest.mark.parametrize("identity", ["doi", "arxiv_id", "paper_id"])
def test_supplement_only_fills_missing_fields_preserving_user_text_scope_and_chunks(rt, identity):
    workspace = rt.services["p1"]
    existing = Paper(paper_id="original", title="User title", authors=["User author"], year=2020,
                     abstract="", doi="10.1/original" if identity == "doi" else None,
                     arxiv_id="2601.00001" if identity == "arxiv_id" else None,
                     metadata={"reading_scope": "用户声明的阅读范围", "user_note": "Do not replace"}, source_stage="upload")
    workspace.store.save_paper(existing)
    chunk = PaperChunk(chunk_id="user-chunk", paper_id="original", chunk_index=0, text="User original passage", page=8)
    workspace.store.save_paper_chunks([chunk])
    service = ResearchTaskService("p1", workspace.db_path, ConversationService("p1", workspace.db_path))
    candidate = {"paper_id": "original" if identity == "paper_id" else "candidate", "title": "Retrieved title",
                 "authors": ["Retrieved author"], "year": 2024, "abstract": "Retrieved abstract only", "has_abstract": True,
                 "doi": existing.doi, "arxiv_id": existing.arxiv_id, "venue": "Retrieved venue", "url": "https://example.org/paper"}
    saved, linked, skipped = service._save_papers("offline-task", [candidate])
    updated = workspace.store.get_paper("original")
    assert saved == [] and linked == ["original"] and skipped == []
    assert candidate["paper_id"] == "original" and updated.abstract == "Retrieved abstract only"
    assert updated.title == existing.title and updated.authors == existing.authors and updated.year == 2020
    assert updated.metadata == existing.metadata and updated.source_stage == "upload" and updated.created_at == existing.created_at
    assert updated.venue == "Retrieved venue" and updated.url == "https://example.org/paper"
    assert workspace.store.get_paper_chunks("original") == [chunk]
    before = deepcopy(updated.model_dump())
    candidate["abstract"] = "Different incoming abstract"
    service._save_papers("offline-task", [candidate])
    assert workspace.store.get_paper("original").model_dump() == before
