"""Delivery checks use actual project records; never call a model or rewrite them."""
import pytest

from core.knowledge.schema import Paper, PaperChunk
from core.workspace.plan_quality import assess_plan
from tests.test_project_conversations import chat, create, apply, tree
from tests.test_research_plan_templates import answer, generate, model, attach_exports


def proposal(chat):
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: model(ctx, literature=True)
    draft = answer(generate(chat, cid))["suggestions"][0]
    return cid, draft


def quality(chat, *, pid="p1", **params):
    return chat.client.get(f"/api/projects/{pid}/workspace/plan-quality", params=params)


def test_legacy_quality_is_readonly_and_does_not_claim_scientific_validation(chat, monkeypatch):
    from core.llm import LLMRegistry

    monkeypatch.setattr(LLMRegistry, "from_config", lambda *a: pytest.fail("read called a model"))
    item = chat.services["p1"].load(include_chat=False).items[-1]
    before = tree(chat.root)
    report = answer(quality(chat, item_id=item.item_id))
    assert report["version"] == 1 and report["status"] == "legacy"
    assert report["target_id"] == item.item_id
    assert "不证明" in report["notice"] and report["review_questions"]
    assert tree(chat.root) == before and chat.calls == []


@pytest.mark.parametrize("params", [{}, {"item_id": "x", "suggestion_id": "y", "conversation_id": "z"},
                                     {"suggestion_id": "x"}, {"conversation_id": "x"}, {"item_id": ""}])
def test_quality_requires_one_explicit_target(chat, params):
    before = tree(chat.root)
    assert quality(chat, **params).status_code == 422
    assert tree(chat.root) == before


def test_abstract_draft_exposes_reading_scope_conditions_and_project_ownership(chat):
    cid, draft = proposal(chat)
    before = tree(chat.root)
    report = answer(quality(chat, conversation_id=cid, suggestion_id=draft["suggestion_id"]))
    assert report["status"] == "needs_attention"
    assert report["pending_conditions"] == ["资源／设备", "期限", "预算", "资料权限"]
    assert report["evidence"][0]["matched"] is True
    assert report["evidence"][0]["location"] == "摘要字段（未核对全文）"
    assert report["evidence"][0]["page"] is None
    assert any(c["code"] == "reading_scope" for c in report["checks"])
    assert quality(chat, pid="p2", conversation_id=cid, suggestion_id=draft["suggestion_id"]).status_code == 404
    assert tree(chat.root) == before
    other = create(chat)
    assert quality(chat, conversation_id=other, suggestion_id=draft["suggestion_id"]).status_code == 404
    # Creation of another conversation is intentional; checking a draft is not a write.
    after_create = tree(chat.root)
    quality(chat, conversation_id=cid, suggestion_id=draft["suggestion_id"])
    assert tree(chat.root) == after_create
    assert before != after_create


def test_formal_quality_tracks_corrected_sources_and_exports_same_checks(chat):
    attach_exports(chat)
    cid, draft = proposal(chat)
    item = answer(apply(chat, cid, draft["suggestion_id"]))["item"]
    service = chat.services["p1"]
    service.store.save_paper_chunks([PaperChunk(chunk_id="e1", paper_id="p1-paper", chunk_index=0,
                                              text="Not scientific evidence", page=4)])
    before = tree(chat.root)
    report = answer(quality(chat, item_id=item["item_id"]))
    assert report["evidence"][0]["location"] == "已存片段（第 4 页；未核对全文）；阅读范围未声明"
    assert report["evidence"][0]["page"] == 4
    exported = chat.client.get(f"/api/projects/p1/exports/items/{item['item_id']}")
    assert exported.status_code == 200
    assert "交付检查（依据当前资料）" in exported.text and "已存片段（第 4 页；未核对全文）；阅读范围未声明" in exported.text
    assert tree(chat.root) == before
    paper = service.store.list_papers()[0]
    paper.abstract = "Corrected abstract"
    service.store.save_paper(paper)
    service.store.save_paper_chunks([PaperChunk(chunk_id="e1", paper_id="p1-paper", chunk_index=0,
                                              text="Corrected excerpt", page=5)])
    before = tree(chat.root)
    report = answer(quality(chat, item_id=item["item_id"]))
    assert not report["evidence"][0]["matched"]
    assert report["evidence"][0]["location"] == "未匹配当前摘要或正文片段"
    assert "部分引文未匹配" in chat.client.get(f"/api/projects/p1/exports/items/{item['item_id']}").text
    assert service.load(include_chat=False).items[-1].content == item["content"]
    assert tree(chat.root) == before
    assert quality(chat, pid="p2", item_id=item["item_id"]).status_code == 404


def test_stale_draft_basis_is_visible_in_report_and_download(chat):
    attach_exports(chat)
    cid, draft = proposal(chat)
    service = chat.services["p1"]
    paper = service.store.list_papers()[0]
    paper.abstract += " Additional material."
    service.store.save_paper(paper)
    report = answer(quality(chat, conversation_id=cid, suggestion_id=draft["suggestion_id"]))
    assert next(c for c in report["checks"] if c["code"] == "basis")["status"] == "attention"
    exported = chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions/{draft['suggestion_id']}/export")
    assert exported.status_code == 200 and "方案或关联问题的依据已变化" in exported.text


def test_fulltext_match_is_only_ready_for_discussion_not_scientific_approval(chat):
    cid, draft = proposal(chat)
    from core.workspace.plan_schema import ResearchPlan

    plan = ResearchPlan.model_validate(draft["plan"])
    for condition in plan.conditions.model_dump():
        getattr(plan.conditions, condition).basis = "user"
    paper = Paper(paper_id="p1-paper", title="Test source", doi="10.0000/test")
    chunks = {paper.paper_id: [PaperChunk(chunk_id="chunk", paper_id=paper.paper_id, chunk_index=0,
                                        text="Not scientific evidence", page=None)]}
    report = assess_plan(plan, {paper.paper_id: paper}, chunks, target_id="draft", workspace_revision=2)
    assert report.status == "ready_for_discussion" and "专业审阅" in report.label
    assert report.evidence[0].location == "已存片段（未标页码；未核对全文）；阅读范围未声明" and report.evidence[0].page is None
    assert "不证明引文支持论断" in report.notice
    assert not hasattr(report, "score")
    missing = assess_plan(plan, {}, {}, target_id="draft", workspace_revision=2)
    assert missing.status == "needs_attention" and not missing.evidence[0].matched
    plan.references.append("unused")
    unused = assess_plan(plan, {paper.paper_id: paper}, chunks, target_id="draft", workspace_revision=2)
    assert next(c for c in unused.checks if c.code == "citations").status == "attention"


def test_quality_cannot_ignore_changed_or_missing_question(chat):
    cid, draft = proposal(chat)
    service = chat.services["p1"]
    w = service.load(include_chat=False)
    question = next(i for i in w.items if i.kind == "question")
    question.needs_review = True
    from core.workspace.service import KEY

    service.store.save_kv(KEY, w.model_dump(mode="json"))
    report = service.plan_quality(draft)
    assert next(c for c in report.checks if c.code == "basis").status == "attention"


def test_unreadable_evidence_returns_recoverable_error_without_writes(chat, monkeypatch):
    import sqlite3
    from core.knowledge.store import KnowledgeStore

    cid, draft = proposal(chat)
    before = tree(chat.root)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("fixture read failure")

    monkeypatch.setattr(KnowledgeStore, "get_paper_chunks", broken)
    result = quality(chat, conversation_id=cid, suggestion_id=draft["suggestion_id"])
    assert result.status_code == 503 and "未改写" in result.text
    assert tree(chat.root) == before
