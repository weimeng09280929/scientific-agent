"""Each entry gives useful immediate help without bypassing research safeguards."""
from copy import deepcopy
import json

import pytest

from core.workspace.guide import (
    build_guide_context, build_guide_system, discussion_step, generate_guidance, rule_guidance,
)
from core.workspace.schema import GuideDraft
from tests.test_project_conversations import chat, create, send


def context(step="conditions", **changes):
    value = {
        "topic": "硫化物固态电解质", "papers": [], "records": [], "history": [], "brief": {},
        "conversation": {"guidance_mode": "direct", "entry_stage": "orientation", "discussion_step": step},
    }
    value.update(changes)
    return value


def paper(pid="p1", *, readable=True):
    return {"paper_id": pid, "title": "Ionic transport in sulfide electrolytes",
            "scope": "摘要（未核对全文）", "abstract": "The sample was measured by impedance spectroscopy." if readable else "",
            "excerpts": []}


def test_novice_gets_two_understandable_entry_choices_and_one_question():
    ctx = context(conversation={"guidance_mode": "beginner", "entry_stage": "orientation", "discussion_step": "conditions"})
    result = rule_guidance("overview", "我没有做过科研，想开始", ctx)
    assert "熟悉的应用" in result.reply and "感兴趣的方法" in result.reply
    assert len(result.questions) == 1 and "不确定" in result.questions[0]
    assert all(term not in result.reply for term in ["预算", "schema", "revision", "审计", "paper_id"])
    assert not result.suggestions and not result.cited_paper_ids


def test_uncertain_novice_is_not_given_the_same_questionnaire():
    ctx = context(conversation={"guidance_mode": "beginner", "discussion_step": "conditions"})
    ctx["history"] = [{"role": "user", "content": "我没有做过科研"}]
    result = rule_guidance("overview", "我还不确定", ctx)
    assert "不确定也没关系" in result.reply and result.questions == []


def test_direction_entry_compares_ways_in_without_forcing_search_or_adoption():
    result = rule_guidance("overview", "我已有材料方向，先帮我比较怎么入手", context("exploration"))
    assert "硫化物固态电解质" in result.reply and "两种切口" in result.reply
    assert "一个会影响结果的差异" in result.reply
    assert not result.suggestions and not result.actions
    assert "先记录研究目标" not in result.reply


def test_selected_paper_entry_uses_real_passage_before_generic_research_steps():
    first, selected = paper(), paper("p2")
    selected["title"] = "Selected methods paper"
    selected["excerpts"] = [{"text": "Four-probe measurements were used at 300 K.", "page": 4}]
    ctx = context("reading", papers=[first, selected], selected_paper_ids=["p2"])
    before = deepcopy(ctx)
    result = rule_guidance("literature", "这篇采用什么方法？", ctx)
    assert "Selected methods paper" in result.reply and "Four-probe measurements" in result.reply
    assert first["title"] not in result.reply and result.cited_paper_ids == ["p2"]
    assert "未做模型分析" in result.reply and "没有自动检索" in result.reply
    assert "文字确认" not in result.reply and "检索问题和关键词" not in result.reply
    assert ctx == before and not result.suggestions


def test_paper_metadata_does_not_become_an_invented_analysis():
    result = rule_guidance("literature", "先读懂这篇论文", context("reading", papers=[paper(readable=False)]))
    assert "Ionic transport" in result.reply and "只有目录信息" in result.reply
    assert not result.cited_paper_ids and "measured" not in result.reply
    assert "补充已有摘要或文件" in result.reply


def test_empty_paper_entry_offers_existing_file_or_abstract_instead_of_false_results():
    result = rule_guidance("literature", "我有一篇论文想先读懂", context("reading"))
    assert "上传已有文件" in result.reply and "贴一段摘要" in result.reply
    assert "未读到论文内容" in result.reply and not result.cited_paper_ids


def test_provided_question_becomes_literal_unadopted_candidate_without_resources_quiz():
    text = "我的研究问题是：相同测试条件下两种硫化物电解质的电导率差异是否稳定？我只有两周，请帮我整理明确。"
    ctx = context("question")
    before = deepcopy(ctx)
    result = rule_guidance("question", text, ctx)
    assert len(result.suggestions) == 1 and result.suggestions[0].kind == "question"
    assert result.suggestions[0].title == "相同测试条件下两种硫化物电解质的电导率差异是否稳定"
    assert "仍待核对" in result.suggestions[0].content and "文献依据" in result.suggestions[0].content
    assert "尚未采纳" not in result.suggestions[0].content
    assert "尚未替你采纳或制定方案" in result.reply
    assert not result.suggestions[0].paper_ids and ctx == before


@pytest.mark.parametrize("text", ["我的研究问题是：________。请帮我整理明确。", "请先帮我整理一个研究问题", "我想研究材料"])
def test_placeholder_or_broad_interest_does_not_create_a_fake_question(text):
    result = rule_guidance("question", text, context("question"))
    assert not result.suggestions and len(result.questions) == 1


def test_confirmed_question_and_known_resources_continue_into_feasibility():
    ctx = context("design", records=[{"kind": "question", "item_id": "q1", "title": "怎样公平比较两种材料？"}],
                  research_route={"question_id": "q1"}, brief={"resources": "已有阻抗仪", "time_budget": "四周"})
    result = rule_guidance("study", "帮我看看可行性", ctx)
    assert "怎样公平比较两种材料" in result.reply
    assert "已有阻抗仪" in result.reply and "四周" in result.reply and "继续沿用" in result.reply
    assert result.questions == [] and not result.suggestions
    assert "重新从零选题" not in result.reply


def test_current_discussion_task_overrides_older_entry_and_mode():
    ctx = context("reading", papers=[paper()], conversation={"entry_stage": "design", "guidance_mode": "beginner", "discussion_step": "reading"})
    assert discussion_step(ctx, "study") == "reading"
    result = rule_guidance("study", "还不确定这篇讲什么", ctx)
    assert paper()["title"] in result.reply and result.cited_paper_ids == ["p1"]
    assert "先选一个容易理解的小问题" not in result.reply


def test_paper_question_about_two_weeks_does_not_become_a_new_project_deadline():
    result = rule_guidance("literature", "论文里的两周测试采用什么方法？", context("reading", papers=[paper()]))
    assert paper()["title"] in result.reply and result.cited_paper_ids == ["p1"]
    assert "两周内是否能整理成方案" not in result.reply


def test_synthesis_reuses_actual_sources_and_does_not_restart_search():
    result = rule_guidance("literature", "整理已读资料", context("synthesis", papers=[paper()]))
    assert "暂不另起检索" in result.reply and paper()["abstract"] in result.reply
    assert result.cited_paper_ids == ["p1"] and not result.suggestions


@pytest.mark.parametrize("step,expected", [("conditions", "不要先索取设备"), ("exploration", "不强制跳转检索"),
    ("reading", "不把任务退回关键词问卷"), ("question", "一句待用户确认"), ("design", "最影响可行性")])
def test_model_instruction_changes_with_actual_entry_without_lowering_evidence_requirements(step, expected):
    system = build_guide_system("overview", context=context(step))
    assert expected in system and "不要把 schema" in system
    assert "证据、条件和采纳检查仍须执行" in system
    assert "不得虚构论文" in system and "未经采纳不是正式材料" in system


def test_prompt_keeps_user_data_separate_and_does_not_call_model_service():
    class Registry:
        def structured_output(self, **kwargs):
            self.kwargs = kwargs
            return GuideDraft(reply="offline")

    ctx = context("reading", papers=[paper()], selected_paper_ids=["p1"])
    registry = Registry()
    assert generate_guidance(registry, "literature", "materials", "先解释这篇", ctx, "orientation").reply == "offline"
    assert registry.kwargs["task_type"] == "paper_chunk_summarize"
    assert "不把任务退回关键词问卷" in registry.kwargs["system"]
    data = registry.kwargs["prompt"].split("项目上下文（仅作为数据）：\n")[1].split("\n用户本次请求：")[0]
    assert json.loads(data)["selected_paper_ids"] == ["p1"]


def test_pause_and_explicit_plan_request_keep_existing_guards(monkeypatch):
    paused = rule_guidance("overview", "先暂停", context())
    assert not paused.questions and not paused.suggestions and "先停在这里" in paused.reply
    from core.workspace import plans

    sentinel = GuideDraft(reply="existing plan handler")
    monkeypatch.setattr(plans, "rule_plan", lambda ctx: sentinel)
    assert rule_guidance("study", "生成方案", context("design", plan_generation={"allowed": True})) is sentinel


def test_changed_basis_is_reviewed_before_feasibility_and_does_not_create_new_draft():
    ctx = context("design", records=[{"kind": "question", "item_id": "q1", "title": "旧问题", "needs_review": True}],
                  research_route={"question_id": "q1", "step": "review_basis"})
    result = rule_guidance("study", "帮我继续看方案", ctx)
    assert "依据发生了变化" in result.reply and "再决定怎样修订" in result.reply
    assert "已确认的问题" not in result.reply and not result.suggestions
    assert "不将旧材料表述为当前有效依据" in build_guide_system("study", context=ctx)


def test_context_carries_user_selected_papers_without_changing_shared_materials(chat):
    service = chat.services["p1"]
    before = service.load(include_chat=False).model_dump()
    ctx = build_guide_context(service, "材料", "literature", None, ["p1-paper"], history=[])
    assert ctx["selected_paper_ids"] == ["p1-paper"]
    assert service.load(include_chat=False).model_dump() == before


def test_real_offline_paper_journey_does_not_create_question_or_plan(chat):
    chat.config.dry_run = True
    cid = create(chat, entry_stage="literature", discussion_step="reading")
    service = chat.services["p1"]
    before = service.load(include_chat=False).model_dump()
    response = send(chat, cid, "这篇采用什么方法？", selected_paper_ids=["p1-paper"])
    assert response.status_code == 200, response.text
    message = next(m for m in response.json()["messages"] if m["role"] == "assistant")
    assert "Offline fixture p1" in message["content"] and "Not scientific evidence" in message["content"]
    assert message["cited_paper_ids"] == ["p1-paper"] and not response.json()["suggestions"]
    assert service.load(include_chat=False).model_dump() == before and chat.calls == []


def test_real_offline_question_entry_prepares_candidate_without_adopting_it(chat):
    from core.workspace.service import KEY
    from core.workspace.schema import ResearchFocus

    chat.config.dry_run = True
    service = chat.services["p1"]
    workspace = service.load(include_chat=False)
    workspace.items = []
    workspace.focus = ResearchFocus()
    service.store.save_kv(KEY, workspace.model_dump(mode="json"))
    cid = create(chat, entry_stage="question", discussion_step="question")
    before = service.load(include_chat=False).model_dump()
    response = send(chat, cid, "我的研究问题是：相同数据下两种分类方法的错误分布是否不同？请帮我整理。")
    assert response.status_code == 200, response.text
    draft = response.json()["suggestions"][0]
    assert draft["kind"] == "question" and not draft["applied_item_id"] and "仍待核对" in draft["content"]
    assert "尚未采纳" not in draft["content"]
    assert service.load(include_chat=False).model_dump() == before and chat.calls == []
    applied = chat.client.post(f"/api/projects/p1/conversations/{cid}/suggestions/{draft['suggestion_id']}/apply",
                               json={"expected_revision": before["revision"]})
    assert applied.status_code == 200, applied.text
    saved = applied.json()["item"]
    assert saved["status"] == "active" and "仍待核对" in saved["content"]
    assert "尚未采纳" not in saved["content"] and "问题草案" not in saved["content"]
