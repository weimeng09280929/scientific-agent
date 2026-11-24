"""Temporary real main app with an offline SDK transport for P1 browser QA."""

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
    from tests.test_real_workflow_reliability import sdk_client, model_response
    from core.llm.base import LLMRequest
    from core.llm.providers.openai_provider import OpenAIProvider
    from core.workspace.schema import GuideDraft
    from web import workspace_api

    app = base_build(temporary)
    state = {"fail": True, "attempts": 0}

    def handler(request):
        state["attempts"] += 1
        if state["fail"]:
            raise module.ConnectError("offline fixture", request=request)
        return model_response(module, request)

    client, module = sdk_client(handler)
    provider = OpenAIProvider("offline-key", base_url="https://offline.invalid/v1",
                              max_retries=3, http_client=client)

    def generate(registry, area, domain, text, context, purpose):
        provider.complete(LLMRequest(prompt="offline browser fixture"), model="fixture")
        return GuideDraft(reply="恢复后的离线回复；尚未采纳任何正式材料。")

    workspace_api.generate_guidance = generate

    @app.get("/__p1__/state")
    def view_state():
        return dict(state)

    @app.post("/__p1__/recover")
    def recover():
        state["fail"] = False
        return dict(state)

    return app


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8066)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sra-p1-browser-") as temporary:
        uvicorn.run(build(temporary), host="127.0.0.1", port=args.port,
                    log_level="warning")
