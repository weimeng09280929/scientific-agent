"""Isolated browser fixture. The API, storage and generation policy are real."""
import argparse
import tempfile
from pathlib import Path

import uvicorn

from core.workspace.plans import rule_plan
from core.workspace.schema import GuideDraft
from tests.research_partner_fixture import sample, create


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--research-gap", action="store_true")
    parser.add_argument("--paper-reading", action="store_true")
    parser.add_argument("--guided-reading", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("sra-entry-"):
        raise ValueError("Use a fresh temporary test root")
    root.mkdir()
    with sample(root) as s:
        cid = create(s)
        peer = create(s, discussion_step="reading")
        other = s["a"].post(
            f'/api/projects/{s["target"]}/conversations', json={"title": "另一个项目的独立讨论"}
        ).json()["conversation"]["conversation_id"]
        foreign_cid = s["b"].post(
            f'/api/projects/{s["foreign"]}/conversations',
            json={"title": "另一个账号的独立讨论", "discussion_step": "question"},
        ).json()["conversation"]["conversation_id"]
        if args.research_gap:
            from tests.research_gap_fixture import draft_for, seed
            from core.workspace.research_coach import gap_requested

            seed(s)
        pdf_id = None
        if args.paper_reading or args.guided_reading:
            from tests.question_paper_reading_fixture import explain, seed as seed_reading

            pdf_id = seed_reading(s)
        if args.guided_reading:
            from tests.guided_paper_reading_fixture import passage_reply, seed_passages

            seed_passages(s)

        def reply(context, text):
            if context.get("plan_generation", {}).get("allowed"):
                return rule_plan(context)
            if args.guided_reading and "passage" in context:
                return passage_reply(context, text)
            if args.paper_reading or args.guided_reading:
                return explain(context,text)
            if args.research_gap and gap_requested(text):
                count = sum(p["content_available"] != "metadata" for p in context["papers"])
                return GuideDraft.model_validate(draft_for(context, "comparison" if count > 1 else "stated_limitation" if count else "hypothesis"))
            if "给出两个候选" in text or "重新给出候选" in text:
                names = ("候选甲：检索词覆盖", "候选乙：重排与误检") if "重新" not in text else ("新候选：摘要噪声",)
                return GuideDraft(
                    reply="离线验收样本。以下问题可分别核对。",
                    cited_paper_ids=["abstract-only"],
                    suggestions=[
                        {"kind": "question", "title": name, "content": name + "在相同测试条件下有何差别？",
                         "paper_ids": ["abstract-only"]}
                        for name in names
                    ],
                    actions=[{"type": "adopt_suggestion", "suggestion_index": 0}],
                )
            return GuideDraft(
                reply="离线验收样本。可以先比较已有方法和需要控制的条件。",
                cited_paper_ids=["abstract-only"] if any(p["paper_id"] == "abstract-only" for p in context["papers"]) else [],
                suggested_replies=[{"label": "用例子解释一下", "explanation": "用一个例子说明对照的用途。",
                                    "content": "请举例解释对照的用途。"}],
                actions=[{"type": "open_settings"}],
            )

        s["control"]["result"] = reply
        app = s["api"].app
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8113, log_level="warning"))

        @app.get("/__entry__/state")
        def state():
            return {k: s[k] for k in ("pid", "target", "foreign", "calls", "state")} | {
                "cid": cid, "peer": peer, "other": other, "foreign_cid": foreign_cid, "pdf_id": pdf_id,
            }

        if args.paper_reading:
            @app.post("/__entry__/replace-reading-text")
            def replace_reading_text():
                from core.knowledge.schema import PaperChunk
                chunks = s["store"].get_paper_chunks(pdf_id)
                method = next(chunk for chunk in chunks if chunk.page == 4)
                s["store"].save_paper_chunks([
                    method.model_copy(update={"text":"Replacement source text for historical snapshot verification."}),
                ])
                return {"changed": True}

        if args.guided_reading:
            @app.post("/__entry__/remove-birch")
            def remove_birch():
                with s["store"]._connect() as conn:
                    conn.execute("DELETE FROM papers WHERE paper_id='birch'")
                return {"changed": True}

        @app.post("/__entry__/shutdown")
        def stop():
            server.should_exit = True
            return {"stopping": True}

        server.run()


if __name__ == "__main__":
    main()
