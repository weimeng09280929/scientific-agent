"""Real application contracts with archived r2 failures and offline SDK responses."""

from hashlib import sha256
import json
from pathlib import Path

import pytest

from core.workspace.evidence_refs import fact_catalog, resolve_fact
from core.workspace.paper_reading import numbered_papers, provided_paper, resolve_quote, verify_denominator_explanation
from core.workspace.plans import UserStatementEvidenceError, prepare_plan, render_plan_evidence, validate_plan
from core.workspace.schema import DirectPaperWireDraft, GapSource, GuideDraft, GuideWireDraft
from core.workspace.service import WorkspaceError
from tests.test_project_conversations import apply, chat, create, send
from tests.test_research_plan_templates import attach_exports, core_payload
from tests.test_stage_b_acceptance_repairs import replay, valid_plan


def packet(text):
    return json.JSONDecoder().raw_decode(text.split("项目上下文（仅作为数据）：\n", 1)[1])[0]


def scoped(request, *, revision=2, project="a"):
    return {"project_id": project, "conversation_id": "local", "revision": revision, "request_id": request}


@pytest.fixture(scope="module")
def r2():
    root = Path(__file__).resolve().parents[1] / "docs/real-user-acceptance/novice-retest-20261110"
    expected = {
        "model-inputs.json": "babd1c8a9a48cb36c73e17449efe9494aeb42a984953b50c81ff73016b2ea512",
        "gateway-responses.json": "1427c4e7892500688e95fda98338ec31d31239c400dafce7a6f2823b5be55014",
    }
    data = {}
    for name, digest in expected.items():
        if not (root / name).is_file():
            pytest.skip("historical acceptance receipts are kept in the local validation archive")
        blob = (root / name).read_bytes()
        assert sha256(blob).hexdigest() == digest
        data[name] = json.loads(blob)
    return data["model-inputs.json"], data["gateway-responses.json"]


@pytest.mark.parametrize("attempt", [3, 4, 9])
def test_original_r2_malformed_quotes_remain_rejected(r2, attempt):
    requests, responses = r2
    request, response = requests[attempt-1], responses[attempt-1]
    assert request["attempt_id"] == response["attempt_id"]
    ctx = packet(request["request"]["messages"][-1]["content"])
    papers = {p["paper_id"]: p for p in ctx["papers"]}
    sources = GuideDraft.model_validate_json(response["choices"][0]["content"]).reading_citations
    results = []
    for source in sources:
        try:
            results.append(bool(resolve_quote(source, papers)))
        except WorkspaceError:
            results.append(False)
    assert any(results) and not all(results)


@pytest.mark.parametrize("attempt", [14, 15])
def test_original_r2_attribution_errors_remain_rejected(r2, attempt):
    requests, responses = r2
    wire = requests[attempt-1]["request"]["messages"][-1]["content"]
    plan = GuideDraft.model_validate_json(responses[attempt-1]["choices"][0]["content"]).suggestions[0].plan
    with pytest.raises(UserStatementEvidenceError):
        validate_plan(plan, packet(wire), wire.split("用户本次请求：\n", 1)[1])


def test_numbered_paper_uses_exact_snapshot_and_rejects_wrong_scope():
    original = "A condition does not apply to 2.5 mg.\n" * 9
    paper = {"paper_id": "p", "title": "Trial", "abstract": "", "excerpts": [
        {"text": original, "excerpt_id": "e", "chunk_id": "chunk", "page": 3, "start": 40, "end": 40+len(original)}
    ]}
    delivered = numbered_papers([paper], scoped("one"))[0]
    ref, lo, hi = delivered["excerpts"][0]["evidence_parts"][0]
    pick = GapSource(evidence_id=ref)
    match = resolve_quote(pick, {"p": delivered})
    assert match.quote == original[lo:hi] and match.page == 3 and match.start == 40
    assert (match.quote_start, match.quote_end) == (lo, hi)
    for altered in [
        GapSource(evidence_id=ref, paper_id="q"),
        GapSource(evidence_id=ref, quote=match.quote.replace("does not", "does")),
        GapSource(evidence_id=ref, quote="A condition does not apply." + original[-12:]),
    ]:
        with pytest.raises(WorkspaceError):
            resolve_quote(altered, {"p": delivered})
    for other in [scoped("two"), scoped("one", revision=3), scoped("one", project="b")]:
        with pytest.raises(WorkspaceError):
            resolve_quote(pick, {"p": numbered_papers([paper], other)[0]})
    with pytest.raises(WorkspaceError):
        resolve_quote(pick, {"p": {**delivered, "excerpts": []}})


def test_fact_catalog_excludes_changed_background_and_clipped_history():
    context = {"brief": {"resources": "没有 GPU（仅 2 台 CPU）", "goal": ""}, "project_background": {"revision": 7},
               "history": [
                   {"role": "user", "message_id": "old", "full_user_message": True, "background_outdated": True, "content": "旧说法"},
                   {"role": "user", "message_id": "clipped", "content": "没有限制"},
                   {"role": "user", "message_id": "recent", "source_revision": 7, "full_user_message": True, "content": "只允许这批公开数据"},
               ]}
    facts = fact_catalog(context, "帮我安排一次小规模比较。", scoped("one"))
    refs = {fact["record_id"]: key for key, fact in facts.items()}
    assert set(refs) == {"resources", "recent", "one"}
    text, origin = resolve_fact(refs["resources"], facts, condition="resources")
    assert text == context["brief"]["resources"] and origin.version == "7"
    for kw in [{"supplied": ("只有 CPU",)}, {"condition": "budget"}]:
        with pytest.raises(WorkspaceError):
            resolve_fact(refs["resources"], facts, **kw)
    with pytest.raises(WorkspaceError):
        resolve_fact(refs["resources"], fact_catalog(context, "其他请求", scoped("two", project="b")))


def test_wire_contract_exposes_only_references_and_retains_exact_legacy_parser():
    definitions = GuideWireDraft.model_json_schema()["$defs"]
    assert set(definitions["GapSource"]["properties"]) == {"evidence_id"}
    assert [set(s["required"]) for s in definitions["PlanStatement"]["oneOf"]] == [
        {"basis", "fact_id"}, {"basis", "text", "evidence_id"}, {"basis", "text"},
    ]
    assert "revised_from_draft_id" not in definitions["ResearchPlan"]["properties"]
    assert "research_gaps" not in DirectPaperWireDraft.model_json_schema()["properties"]
    old = {"reply": "历史格式。", "reading_citations": [{"paper_id": "p", "part": "abstract", "quote": "Source."}]}
    assert GuideWireDraft.model_validate(old).reading_citations[0].quote == "Source."


@pytest.mark.parametrize("text,wrong", [
    ("两者只有在近似“一问一段命中”时才一致。", True),
    ("每个查询只命中一条时，两个指标自然相同。", True),
    ("两者只有在 k = 1 且命中判定相同的情况下才等价。", False),
    ("即使每个查询只命中一段，两个分母仍然不相同。", False),
])
def test_denominator_example_is_computed_from_different_counting_units(text, wrong):
    paper = {"paper_id": "p", "title": "Measures", "abstract": "", "excerpts": [
        {"excerpt_id": "method", "text": "The fraction of ques-\ntions for which the set contains an answer."},
        {"excerpt_id": "table", "text": "The percentage of top 5 retrieved\npassages that contain the answer."},
    ]}
    numbered = numbered_papers([paper], scoped("ratio"))[0]
    example = numbered["denominator_contrast"]["mathematical_example"]
    assert example["hit_question_percent"] == 40 and example["positive_passage_percent"] == 8
    if wrong:
        with pytest.raises(WorkspaceError, match="两个指标"):
            verify_denominator_explanation(text, [numbered])
    else:
        verify_denominator_explanation(text, [numbered])
    # This known contrast is only tested when both expressions were supplied.
    restricted = numbered_papers([{**paper, "excerpts": paper["excerpts"][:1]}], scoped("ratio"))[0]
    assert "denominator_contrast" not in restricted


def test_site_renders_arithmetic_without_asking_model_to_recalculate(chat, replay):
    from core.knowledge.schema import Paper, PaperChunk

    store = chat.services["p1"].store
    store.save_paper(Paper(paper_id="p1-paper", title="Metric boundaries", metadata={"chunk_origin": "body"}))
    store.save_paper_chunks([
        PaperChunk(paper_id="p1-paper", chunk_id="method", chunk_index=0, page=2,
                   text="Retrieval accuracy, which is the fraction of ques-\ntions for which the set contains an answer."),
        PaperChunk(paper_id="p1-paper", chunk_id="table", chunk_index=1, page=5,
                   text="Table 2: The percentage of top 5 retrieved\npassages that contain the answer."),
    ])

    def model(request):
        ctx = packet(request["messages"][-1]["content"])
        contrast = ctx["papers"][0]["denominator_contrast"]
        assert "mathematical_example" not in contrast
        return json.dumps({"reply": "正文按问题统计，表注字面是按段统计。",
                           "reading_citations": [{"evidence_id": contrast["query_denominator_evidence_id"]}]})

    replay.choice = model
    response = send(chat, create(chat), "表 2 的命中分母是什么？", "render-example", selected_paper_ids=["p1-paper"])
    assert response.status_code == 200, response.text
    result = response.json()["messages"][-1]
    assert any("4/10=40%" in note and "4/50=8%" in note for note in result["delivery_warnings"])
    assert len(replay.network) == 1


def test_sdk_numbered_plan_is_saved_and_explicit_draft_revision_keeps_lineage(chat, replay):
    cid = create(chat)
    attach_exports(chat)
    service = chat.services["p1"]
    w = service.load()
    brief = w.brief.model_copy(update={"goal": "比较检索效果（预研，不保证提升）"})
    service.save_brief(brief, w.revision)
    # Pick a verified fixture question after the brief change.
    w = service.load()
    from core.workspace.schema import ItemInput, ResearchFocus
    revised_workspace = service.add_item(
        ItemInput(kind="question", title="小规模检索比较", content="两种方法是否有差异？", paper_ids=["p1-paper"]),
        w.revision,
    )
    qid = revised_workspace.items[-1].item_id
    service.save_focus(ResearchFocus(question=qid), revised_workspace.revision)

    def model(request):
        ctx = packet(request["messages"][-1]["content"])
        draft = valid_plan(ctx, literature=True).model_dump()
        plan = draft["suggestions"][0]["plan"]
        goal = next(key for key, item in ctx["user_facts"].items() if item["record_id"] == "goal")
        paper = ctx["papers"][0]
        plan["sections"][0]["statements"].append({"basis": "user", "fact_id": goal})
        next(section for section in plan["sections"] if section["key"] == "related_work")["statements"][0] = {
            "basis": "literature", "text": "所提供材料给出研究依据。", "evidence_id": paper["preview_evidence_id"],
        }
        plan["references"] = []
        draft["reading_citations"] = [{"evidence_id": paper["preview_evidence_id"]}]
        if revision := ctx["plan_generation"].get("draft_revision"):
            assert set(revision) == {"title", "plan"}
            assert revision["title"] == first["title"]
            assert "fact_origin" not in json.dumps(revision)
            plan["method_details"]["evaluation"] = "只报告两种方法各自的指标，统一计算口径。"
        return json.dumps(core_payload(GuideDraft.model_validate(draft)), ensure_ascii=False)

    replay.choice = model
    req = {"question_id": qid, "method": "computational"}
    r = send(chat, cid, "请生成研究方案。", "with-ids", plan_request=req)
    assert r.status_code == 200, r.text
    first = r.json()["suggestions"][0]
    fact = next(s for s in first["plan"]["sections"][0]["statements"] if s.get("fact_id"))
    assert fact["text"] == fact["quote"] == brief.goal and fact["fact_origin"]["record_id"] == "goal"
    assert first["plan"]["references"] == ["p1-paper"]
    other = create(chat)
    assert send(chat, other, "请修改方案。", "cross-draft",
                plan_request={**req, "revises_suggestion_id": first["suggestion_id"]}).status_code == 422
    assert len(replay.network) == 1
    r = send(chat, cid, "请明确修改评价。", "bound-revision",
             plan_request={**req, "revises_suggestion_id": first["suggestion_id"]})
    assert r.status_code == 200, r.text
    revised = r.json()["suggestions"][0]
    assert revised["plan"]["revised_from_draft_id"] == first["suggestion_id"]
    assert revised["suggestion_id"] != first["suggestion_id"] and revised["plan"]["question_id"] == qid
    draft_path = f"/api/projects/p1/conversations/{cid}/suggestions/{revised['suggestion_id']}/export"
    draft_export = chat.client.get(draft_path)
    assert draft_export.status_code == 200
    assert "未采纳讨论稿" in draft_export.text and "文献原文快照" in draft_export.text
    assert first["suggestion_id"] in draft_export.text and "见证据 1" in revised["content"]
    assert "替代的正式方案：无" in draft_export.text
    assert "修订来源草案：" + first["suggestion_id"] in draft_export.text
    result = apply(chat, cid, revised["suggestion_id"])
    assert result.status_code == 200, result.text
    formal = chat.client.get("/api/projects/p1/exports/items/" + result.json()["item"]["item_id"])
    assert formal.status_code == 200 and "文献原文快照" in formal.text and "当前正式方案" in formal.text
    assert "文献原文快照" in service.export_markdown()
    assert len(replay.network) == 2


def test_unchanged_live_revision_replays_with_full_evidence_appendix():
    root = Path(__file__).resolve().parents[1] / "docs/real-user-acceptance/numbered-evidence-20261111/raw-failures"
    if not all((root / name).is_file() for name in ("13-input.json", "13-response.json")):
        pytest.skip("historical acceptance receipts are kept in the local validation archive")
    inputs = (root / "13-input.json").read_bytes()
    response = (root / "13-response.json").read_bytes()
    assert sha256(inputs).hexdigest() == "9bfdf6bc5d9088d9e2411d62828444e50fadc6bda3d2875ecda7b546017b07b9"
    assert sha256(response).hexdigest() == "4b2e2f758e0a3420a6561d3e7ba17c2fe1e4590c5be35b74e4c2fd2567f37bc5"
    wire = json.loads(inputs)["request"]["messages"][-1]["content"]
    context = packet(wire)
    raw = GuideDraft.model_validate_json(json.loads(response)["choices"][0]["content"])
    draft = prepare_plan(raw, context, wire.split("用户本次请求：\n", 1)[1]).suggestions[0]
    assert draft.plan.evidence_layout == "appendix-v1" and len(draft.content) <= 12000
    assert draft.plan.revised_from_draft_id == context["plan_generation"]["draft_revision"]["suggestion_id"]
    appendix = render_plan_evidence(draft.plan)
    quotes = {s.quote for section in draft.plan.sections for s in section.statements if s.evidence_id}
    assert len(quotes) == 7
    for quote in quotes:
        assert "\n".join("> "+line for line in quote.split("\n")) in appendix


def test_unchanged_live_revision_with_duplicate_keys_stays_failed(chat, replay):
    path = Path(__file__).resolve().parents[1] / (
        "docs/real-user-acceptance/numbered-evidence-20261111/raw-failures/14-response.json"
    )
    if not path.is_file():
        pytest.skip("historical acceptance receipts are kept in the local validation archive")
    blob = path.read_bytes()
    assert sha256(blob).hexdigest() == "82e873f923e518e6a1e347144bf5b65eb8cc7c7c87f1af1a07d5de1ad52e28b3"
    replay.choice = json.loads(blob)["choices"][0]
    assert replay.choice["finish_reason"] == "stop"
    cid = create(chat)
    request_id = "r3-duplicate-keys"
    result = send(chat, cid, "请为当前问题生成研究方案", request_id,
                  plan_request={"method": "computational"}).json()
    assert result["status"] == "failed" and not result["suggestions"]
    assert result["failure"]["reason"] == "structure_invalid"
    assert not result["failure"]["can_retry"]
    assert [m["role"] for m in result["messages"]] == ["user"]
    assert any("duplicate_key" in entry["name"] for entry in result["call_audit"]["entries"])
    retry = chat.client.post(f"/api/projects/p1/conversations/{cid}/requests/{request_id}/retry", json={})
    assert retry.json()["status"] == "failed"
    assert len(replay.network) == 1


@pytest.mark.parametrize("budget,saved", [
    ("一台笔记本就能完成比较；可用算力待确认。", False),
    ("预计单机单卡可完成小规模评测。具体卡型待确认。", False),
    ("先记录内存、索引体积和耗时，再验证一台工作站是否能跑完。", True),
])
def test_undocumented_equipment_sufficiency_does_not_become_a_fact(chat, replay, budget, saved):
    def model(request):
        draft = valid_plan(packet(request["messages"][-1]["content"]))
        draft.suggestions[0].plan.method_details.budget = budget
        return json.dumps(core_payload(draft), ensure_ascii=False)

    replay.choice = model
    data = send(chat, create(chat), "请生成研究方案。", "feasibility",
                plan_request={"method": "computational"}).json()
    assert data["status"] == ("completed" if saved else "failed")
    assert bool(data["suggestions"]) is saved
    assert len(replay.network) == 1


@pytest.mark.parametrize("short,prompt,needed", [
    ("BPR", "这篇论文的方法怎样工作？请说明输入、关键步骤和输出。", ("Hamming", "rerank")),
    ("BPR", "只看这篇论文。没看懂重排：它在重排时，问题和段落各用什么表示？",
     ("Hamming", "inner product", "continuous")),
    ("DPR", "只读《Dense Passage Retrieval for Open-Domain Question Answering》。它的方法怎么找段落？", ("dot product", "encoder")),
    ("DPR", "看 §5.1 和表 2，DPR 和 BM25 比得怎么样？有没有例外？",
     ("Table 2:", "With the exception of SQuAD", "fraction of ques")),
    ("BPR", "看 §4.1 和表 1，只看第一条和看前二十条，结果有什么区别？索引有多大？",
     ("Table 1:", "BPR performs worse than", "percentage of")),
])
def test_real_public_pdf_selection_keeps_processing_steps_and_comparison_context(short, prompt, needed):
    from pypdf import PdfReader
    from core.knowledge.paper_text import text_windows
    from core.knowledge.schema import Paper, PaperChunk

    root = Path(__file__).resolve().parents[1] / "docs/real-user-acceptance/novice-retest-20261110"
    path = next(root.glob("downloaded-" + short + "-*.pdf"), None)
    if path is None:
        pytest.skip("the original PDF is kept in the local validation archive")
    pages = [p.extract_text() for p in PdfReader(path).pages]
    paper = Paper(paper_id=short, title=short, abstract=pages[0][:1200],
                  metadata={"chunk_origin": "body", "text_origin": "body_opening"})
    chunks = []
    for page, text in enumerate(pages, start=1):
        for lo, hi in text_windows(text, 1600, 180):
            i = len(chunks)
            chunks.append(PaperChunk(paper_id=short, chunk_id=str(i), chunk_index=i, text=text[lo:hi], page=page))
    found = provided_paper(paper, chunks, text=prompt)
    text = "\n".join(e["text"] for e in found["excerpts"])
    assert all(s.casefold() in text.casefold() for s in needed), (needed, [(e["page"], e["excerpt_id"]) for e in found["excerpts"]])
    for excerpt in found["excerpts"]:
        raw = chunks[excerpt["chunk_index"]].text
        assert excerpt["text"] == raw[excerpt["start"]:excerpt["end"]]
