"""Reader exports use citations and names; JSON keeps unchanged audit identity."""

import pytest

from core.workspace.plans import export_plan_content, render_plan
from core.workspace.plan_schema import ResearchPlan
from tests.test_project_conversations import chat, create, apply, tree
from tests.test_research_plan_templates import answer, generate, model, attach_exports


def test_plan_export_relabels_generated_body_without_rewriting_custom_text(chat):
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: model(ctx, literature=True)
    draft = answer(generate(chat, cid))["suggestions"][0]
    plan = ResearchPlan.model_validate(draft["plan"])
    from core.workspace.schema import WorkItem

    item = WorkItem(kind="plan", title="Review", content="User preface\n\n" + render_plan(plan) + "\n\nUser appendix", plan=plan)
    original = item.content
    readable = export_plan_content(item, {"p1-paper": "1"})
    assert "p1-paper" not in readable and "[1]" in readable
    assert readable.startswith("User preface") and readable.endswith("User appendix")
    assert "模板 stem-plan" not in readable and item.content == original


@pytest.mark.parametrize("fmt", ["md", "docx", "pdf"])
def test_draft_and_saved_exports_have_readable_references_and_no_internal_ids(chat, fmt):
    attach_exports(chat)
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: model(ctx, literature=True)
    draft = answer(generate(chat, cid))["suggestions"][0]
    draft_path = f"/api/projects/p1/conversations/{cid}/suggestions/{draft['suggestion_id']}/export?format={fmt}"
    preview = chat.client.get(draft_path)
    assert preview.status_code == 200
    assert preview.text.startswith("# ") and "未采纳讨论稿" in preview.text
    assert draft["suggestion_id"] not in preview.text and "p1-paper" not in preview.text
    item = answer(apply(chat, cid, draft["suggestion_id"]))["item"]
    from core.workspace.schema import ResearchFocus

    service = chat.services["p1"]
    service.save_focus(ResearchFocus(question=item["related_ids"][0], plan=item["item_id"]), service.load().revision)
    before = tree(chat.root)
    exported = chat.client.get(f"/api/projects/p1/exports/items/{item['item_id']}?format={fmt}")
    body = exported.text
    assert exported.status_code == 200 and body.startswith("# ")
    assert all(token not in body for token in (item["item_id"], cid, "p1-paper", item["related_ids"][0], "stem-plan-v1", "False"))
    assert "[1]" in body and "Offline fixture p1" in body and "交付检查" in body
    assert "版本：1" in body and "当前正式方案" in body
    assert chat.services["p1"].load().items[-1].content == item["content"]
    assert tree(chat.root) == before


def test_workspace_markdown_uses_names_but_json_keeps_ids(chat):
    service = chat.services["p1"]
    service.load()
    before = tree(chat.root)
    response = chat.client.get("/api/projects/p1/workspace/export")
    assert response.status_code == 200
    for item in service.load().items:
        assert item.item_id not in response.text and item.title in response.text
    assert "p1-paper" not in response.text
    raw = chat.client.get("/api/projects/p1/workspace/export?format=json").json()
    assert raw["project_id"] == "p1" and raw["items"][0]["item_id"]
    assert tree(chat.root) == before
