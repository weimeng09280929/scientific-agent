"""Isolated Step 4B UI fixture; real APIs, temp databases, no provider calls."""

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
    from web import api, workspace_api
    from core.workspace.schema import GuideDraft, ReplyOption

    # Configuration inspection must use fixture credentials, never the user's.
    api._CONFIG.llm.deepseek_api_key_env = "SRA_GUIDANCE_FIXTURE_KEY"
    os.environ["SRA_GUIDANCE_FIXTURE_KEY"] = "offline-fixture-not-a-secret"

    import requests
    import httpx
    import httpx2

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected external I/O in isolated guidance acceptance")

    requests.sessions.Session.request = forbidden
    httpx.HTTPTransport.handle_request = forbidden
    httpx2.HTTPTransport.handle_request = forbidden

    original = workspace_api.generate_guidance
    calls = []

    def generate(registry, area, domain, text, context, purpose):
        calls.append({"text": text, "context": context})
        if "GUIDANCE" not in text:
            return original(registry, area, domain, text, context, purpose)
        replies = [
            {
                "label": "比较另一种方法",
                "content": "请比较另一种更简单的方法，不直接替我确认。",
            },
            {
                "label": "我还不确定",
                "content": "我还不确定，请解释需要先确认哪一个关键条件。",
            },
        ]
        if "DELAY" in text:
            replies[0] = {"label": "延迟选项验收", "content": "OFFLINE_WAIT 选项消息"}
        if "PLAN" in text:
            # New generations use Step 6A's method contract; all original
            # reference/adoption/staleness UI assertions remain in the suite.
            from core.workspace.plans import rule_plan

            result = rule_plan(context)
            result.reply = "离线软件验收，并非真实科研证据；方案尚未采纳，没有运行实验。示例公式：$N=2$。"
            result.suggested_replies = [ReplyOption.model_validate(r) for r in replies]
            plan = result.suggestions[0].plan
            paper = next(p for p in context["papers"] if p.get("abstract"))
            st = plan.sections[1].statements[0]
            st.text = "提供的摘要原文：" + paper["abstract"]
            st.basis, st.quote, st.paper_ids = (
                "literature",
                paper["abstract"],
                [paper["paper_id"]],
            )
            plan.references = [paper["paper_id"]]
            return result
        if "SETTINGS" in text:
            return GuideDraft(
                reply="离线验收：可以调整解释深度，不改变正式材料。",
                suggested_replies=replies,
                actions=[{"type": "open_settings"}, {"type": "open_plan"}],
            )
        return GuideDraft(
            reply="离线验收：先从一个小问题入手，不必马上形成整套方案。",
            suggested_replies=replies,
            actions=[{"type": "open_settings"}],
        )

    workspace_api.generate_guidance = generate

    @app.get("/__guidance__/state")
    def state():
        return {"calls": calls, "pid": os.getpid(), "root": str(temporary)}

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8015)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sra-guidance-ui-") as temporary:
        uvicorn.run(
            build(temporary), host="127.0.0.1", port=args.port, log_level="warning"
        )
