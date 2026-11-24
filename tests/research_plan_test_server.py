"""Real main app + temporary SQLite + offline plan response. No user data."""

import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"
os.environ["SRA_DRY_RUN"] = "true"


def build(temporary):
    from tests.conversation_home_test_server import build as base_build

    app = base_build(temporary)
    from web import workspace_api
    from core.workspace.plans import rule_plan

    original = workspace_api.generate_guidance
    calls = []

    def generate(registry, area, domain, text, context, purpose):
        calls.append(
            {"text": text, "plan_request": context.get("plan_generation"), "area": area}
        )
        if context.get("plan_generation"):
            d = rule_plan(context)
            d.reply = "离线软件验收方案，非科研结论；草案尚未采纳，也未执行任何实验。"
            p = next((p for p in context["papers"] if p.get("abstract")), None)
            if p:
                plan = d.suggestions[0].plan
                st = plan.sections[1].statements[0]
                st.text = "所提供摘要的原文为：" + p["abstract"]
                st.basis = "literature"
                st.quote = p["abstract"]
                st.paper_ids = [p["paper_id"]]
                plan.references = [p["paper_id"]]
            return d
        return original(registry, area, domain, text, context, purpose)

    workspace_api.generate_guidance = generate

    @app.get("/__plans__/state")
    def state():
        return {"calls": calls, "pid": os.getpid(), "root": str(temporary)}

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8018)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sra-plans-ui-") as temporary:
        uvicorn.run(
            build(temporary), host="127.0.0.1", port=args.port, log_level="warning"
        )
