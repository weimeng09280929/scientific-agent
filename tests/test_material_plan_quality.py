"""Material comparison cues are source-bound, read-only and never scientific approval."""
from copy import deepcopy

import pytest

from core.knowledge.schema import Paper, PaperChunk
from core.workspace.plan_quality import PlanQualityReport, assess_plan, render_quality
from core.workspace.plan_schema import ResearchPlan
from tests.test_project_conversations import chat, create, tree
from tests.test_research_plan_templates import answer, generate, model, attach_exports


def plan_for(quote, *, statement="比较固态电解质的离子电导率", method="computational"):
    sections = ["objective", "related_work", "hypothesis", "conditions", "method",
                "validation", "feasibility", "limitations", "references"]
    design = {"data_task": "待讨论", "baselines": "待讨论", "comparisons": "待讨论",
              "budget": "待讨论", "evaluation": "待讨论", "reproducibility": "待讨论"}
    return ResearchPlan.model_validate({
        "method": method, "question_id": "q1",
        "sections": [{"key": key, "statements": [{
            "text": statement if key == "related_work" else "待讨论事项",
            "basis": "literature" if key == "related_work" else "proposal",
            "paper_ids": ["p1"] if key == "related_work" else [],
            "quote": quote if key == "related_work" else "",
        }]} for key in sections],
        "conditions": {field: {"value": value, "basis": "user", "quote": value}
                       for field, value in {"resources": "四探针设备", "time_budget": "四周",
                                            "budget": "一万元", "data_access": "自有论文"}.items()},
        "method_details": {"type": "computational", **design},
        "unconfirmed": ["比较条件仍需专业审核"], "references": ["p1"],
    })


def report_for(quote, *, available=None, statement="比较固态电解质的离子电导率"):
    plan = plan_for(quote, statement=statement)
    paper = Paper(paper_id="p1", title="A source")
    chunks = {"p1": [PaperChunk(chunk_id="c1", paper_id="p1", chunk_index=0,
                                text=quote if available is None else available, page=8)]}
    return assess_plan(plan, {"p1": paper}, chunks, target_id="plan1", workspace_revision=7)


def test_material_quote_retains_original_units_temperature_and_cues_without_conversion():
    quote = "The measured ionic conductivity of Li6PS5Cl was 1.2 × 10−3 S cm−1 at 25 °C by impedance on a bulk sample."
    report = report_for(quote)
    row = report.material_review[0]
    assert row.source_matched
    assert row.quantities == ["1.2 × 10−3 S cm−1"]
    assert row.temperatures == ["25 °C"]
    assert row.evidence_type == "experimental" and row.evidence_type_mentions == ["measured"]
    assert {"impedance", "bulk", "sample"} <= set(row.condition_mentions)
    assert report.status == "needs_attention"
    assert next(c for c in report.checks if c.code == "material_comparability").status == "attention"
    assert "300" not in " ".join(row.temperatures)
    assert not hasattr(row, "score") and "不自动换算" in report.material_notice


def test_unmatched_quote_never_becomes_visible_material_data():
    report = report_for("DFT predicted conductivity of 90 mS/cm at 300 K", available="Corrected original")
    row = report.material_review[0]
    assert not row.source_matched
    assert not row.quantities and not row.temperatures and not row.evidence_type_mentions
    assert row.evidence_type == "unspecified" and "先修复来源匹配" in row.review_points[0]
    assert "数值／单位片段：90" not in render_quality(report)


@pytest.mark.parametrize("quote,quantities,temperatures", [
    ("3.4 mS cm⁻¹ at 300-350 K", ["3.4 mS cm⁻¹"], ["300-350 K"]),
    ("1.2 W m−1 K−1 at 300 K", ["1.2 W m−1 K−1"], ["300 K"]),
    ("A band gap of 1.5 eV and modulus 25 GPa at −25 °C", ["1.5 eV", "25 GPa"], ["−25 °C"]),
    ("125 mAh/g and 10 wt% at room temperature", ["125 mAh/g", "10 wt%"], []),
    ("在300 K时实测电导率为2 mS/cm。", ["2 mS/cm"], ["300 K"]),
    ("在800 K时其ZT为1.2。", ["ZT为1.2"], ["800 K"]),
    ("+1.2e-3 S/cm at +25 °C", ["+1.2e-3 S/cm"], ["+25 °C"]),
    ("−1.2 × 10^-3 eV at −25 °C", ["−1.2 × 10^-3 eV"], ["−25 °C"]),
    ("1.2-2.4 mS/cm at 25–30 °C", ["1.2-2.4 mS/cm"], ["25–30 °C"]),
    ("1,200 mS/cm at 1,200 K", [], []),
    ("−1,200 eV at −1,200 °C", [], []),
    ("1,5 eV; 1,2e-3 S/cm; ZT=1,2", [], []),
    ("1.2–1,5 mS/cm at 1,200-1,500 K", [], []),
    ("2 mS/cm^2; 1 eV/Å; 300 K/s; 2 K⁻¹", [], []),
    ("The unsupported notation 10−3 S/cm needs manual reading", [], []),
    ("Li6PS5Cl was studied", [], []),
])
def test_literal_quantity_variants_do_not_treat_formula_or_unit_exponent_as_temperature(quote, quantities, temperatures):
    row = report_for(quote).material_review[0]
    assert row.quantities == quantities and row.temperatures == temperatures


def test_mixed_and_negated_type_cues_do_not_follow_the_plan_method():
    row = report_for("Experimental values are 2 mS/cm; DFT calculated 3 mS/cm at 300 K.").material_review[0]
    assert row.evidence_type == "mixed"
    assert any("不能直接合并排名" in point for point in row.review_points)
    row = report_for("No experimental data were available; DFT predicted a value of 1.5 eV.").material_review[0]
    assert row.evidence_type == "computational"
    assert any("不能将预测当作实测结果" in point for point in row.review_points)
    row = report_for("The conductivity was 2 mS/cm at room temperature.").material_review[0]
    assert row.evidence_type == "unspecified"
    assert "room temperature" in row.condition_mentions and not row.temperatures
    assert any("研究类型替文献定性" in point for point in row.review_points)


def test_missing_cues_are_review_questions_not_innovation_or_paper_deficiencies():
    row = report_for("Solid-state electrolytes are discussed.").material_review[0]
    assert any("不能由此判断论文缺失数据" in point for point in row.review_points)
    report = report_for("The algorithm improved accuracy by 5%.", statement="比较已有材料中的算法性能")
    assert report.material_review == []
    assert all(check.code != "material_comparability" for check in report.checks)
    assert report.status == "ready_for_discussion"


def test_quality_is_readonly_export_consistent_and_old_reports_still_parse():
    quote = "DFT calculated ionic conductivity at 300 K."
    plan = plan_for(quote)
    before = deepcopy(plan.model_dump())
    paper = Paper(paper_id="p1", title="A source", abstract=quote)
    original_paper = deepcopy(paper.model_dump())
    report = assess_plan(plan, {"p1": paper}, {}, target_id="plan1", workspace_revision=7)
    assert plan.model_dump() == before and paper.model_dump() == original_paper
    assert [(c.field, c.value, c.basis) for c in report.condition_details] == [
        ("resources", "四探针设备", "user"), ("time_budget", "四周", "user"),
        ("budget", "一万元", "user"), ("data_access", "自有论文", "user")]
    text = render_quality(report)
    assert "材料性质与可比性审阅" in text and "300 K" in text and "四周" in text
    assert "来源：A source" in text and "计算线索" in text
    assert "p1" not in text
    old = report.model_dump(exclude={"material_review", "material_notice", "condition_details"})
    parsed = PlanQualityReport.model_validate(old)
    assert parsed.version == 1 and parsed.material_review == [] and parsed.condition_details == []
    legacy = assess_plan(None, {}, {}, target_id="legacy", workspace_revision=7)
    assert legacy.status == "legacy" and not legacy.material_review and not legacy.condition_details


def test_real_material_draft_api_and_export_use_same_readonly_review(chat, monkeypatch):
    from core.llm import LLMRegistry

    attach_exports(chat)
    service = chat.services["p1"]
    paper = service.store.list_papers()[0]
    paper.abstract = "The measured ionic conductivity of the solid-state electrolyte was 2 mS/cm at 300 K."
    service.store.save_paper(paper)
    cid = create(chat)
    chat.control["generate"] = lambda area, text, ctx: model(ctx, literature=True)
    draft = answer(generate(chat, cid))["suggestions"][0]
    before = tree(chat.root)
    calls = len(chat.calls)
    monkeypatch.setattr(LLMRegistry, "from_config", lambda *a: pytest.fail("review called a model"))
    report = answer(chat.client.get("/api/projects/p1/workspace/plan-quality", params={
        "conversation_id": cid, "suggestion_id": draft["suggestion_id"]}))
    row = report["material_review"][0]
    assert row["source_matched"] and row["quantities"] == ["2 mS/cm"]
    assert row["temperatures"] == ["300 K"] and row["evidence_type"] == "experimental"
    assert report["evidence"][0]["location"] == "摘要字段（未核对全文）"
    exported = chat.client.get(f"/api/projects/p1/conversations/{cid}/suggestions/{draft['suggestion_id']}/export")
    assert exported.status_code == 200
    assert "材料性质与可比性审阅" in exported.text and "2 mS/cm" in exported.text
    assert tree(chat.root) == before and len(chat.calls) == calls
    paper.abstract = "The source has been corrected."
    service.store.save_paper(paper)
    before = tree(chat.root)
    changed = answer(chat.client.get("/api/projects/p1/workspace/plan-quality", params={
        "conversation_id": cid, "suggestion_id": draft["suggestion_id"]}))
    assert not changed["material_review"][0]["source_matched"]
    assert changed["material_review"][0]["quantities"] == []
    assert tree(chat.root) == before and len(chat.calls) == calls
