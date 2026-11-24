"""Empty real main app; offline rules only. Optional temp root for restart QA."""

import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"


def serve(root, port):
    import core.config as configuration

    configuration._default_config = configuration.GlobalConfig(
        paths=configuration.ProjectPaths.from_root(root), dry_run=True
    )
    from web import api
    from core.llm import LLMRegistry
    import requests
    import httpx
    import httpx2
    import uvicorn

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline acceptance must not call external services")

    LLMRegistry.from_config = forbidden
    requests.sessions.Session.request = forbidden
    httpx.HTTPTransport.handle_request = forbidden
    httpx2.HTTPTransport.handle_request = forbidden
    server = uvicorn.Server(
        uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="error")
    )

    @api.app.get("/__acceptance__/state")
    def state():
        return {"projects": len(api._PROJECTS), "pid": os.getpid()}

    @api.app.post("/__acceptance__/shutdown")
    def shutdown():
        server.should_exit = True
        return {"stopping": True}

    server.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8047)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        serve(args.root, args.port)
    else:
        with tempfile.TemporaryDirectory(prefix="sra-step7-offline-") as root:
            serve(root, args.port)
