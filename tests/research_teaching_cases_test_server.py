"""Isolated actual main application. Any model/search/network call fails."""

import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"
os.environ["SRA_DRY_RUN"] = "true"


def build(root):
    import core.config as config

    config._default_config = config.GlobalConfig(
        paths=config.ProjectPaths.from_root(root), dry_run=False
    )
    from web import api, workspace_api
    from core.llm import LLMRegistry
    import requests
    import httpx

    calls = []

    def forbidden(*a, **k):
        calls.append("forbidden external operation")
        raise AssertionError("Teaching attempted external operation")

    workspace_api.generate_guidance = forbidden
    LLMRegistry.from_config = forbidden
    requests.sessions.Session.request = forbidden
    httpx.HTTPTransport.handle_request = forbidden

    @api.app.get("/__fixture__/audit")
    def audit():
        return {"external_calls": calls, "projects": len(api._PROJECTS)}

    return api.app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8022)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sra-teaching-acceptance-") as root:
        uvicorn.run(build(root), host="127.0.0.1", port=args.port, log_level="warning")
