"""Opt-in Step 7 live sample. Temporary projects only; hard HTTP call budgets.

Never run in CI. Requires --allow-live. Does not change config files or 8001.
The JSON output contains public sample material, not credentials/provider headers.
"""

import argparse
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--output", default=".pytest_cache/step7-live.json")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Explicit current-run authorization and --allow-live required")
    os.environ["SRA_WEB_SKIP_GUARD"] = "1"
    os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"
    # Source error classification captures WARNING records; do not disable them.
    logging.getLogger().setLevel(logging.WARNING)
    from runtime.cli import _load_env

    _load_env()
    import core.config as configuration
    import httpx
    import httpx2  # Installed OpenAI 3 SDK uses this transport, not httpx 0.x.
    import requests
    from fastapi.testclient import TestClient
    from core.llm import LLMRegistry
    from core.research_tasks.limits import SearchLimits
    from functools import partial

    audit = {"model_requests": [], "source_requests": [], "errors": []}
    original_http = httpx2.HTTPTransport.handle_request
    original_source = requests.sessions.Session.send

    def model_http(transport, request):
        if len(audit["model_requests"]) >= 6:
            raise RuntimeError("Authorized six-model-request budget exhausted")
        data = json.loads(request.content)
        if data.get("model") != "deepseek-v4.1-flash":
            raise RuntimeError("Model differs from authorized sample")
        entry = {
            "host": request.url.host,
            "model": data["model"],
            "max_tokens": data.get("max_tokens"),
            "started": time.time(),
        }
        audit["model_requests"].append(entry)
        print("LIVE model request", len(audit["model_requests"]), flush=True)
        try:
            response = original_http(transport, request)
            response.read()
            entry["http_status"] = response.status_code
            if response.status_code == 200:
                payload = json.loads(response.content)
                entry["usage"] = payload.get("usage")
                entry["finish_reason"] = payload.get("choices", [{}])[0].get(
                    "finish_reason"
                )
            return response
        finally:
            entry["elapsed_seconds"] = round(time.time() - entry["started"], 2)

    def source_http(session, request, **kwargs):
        host = urlparse(request.url).hostname
        if host not in {"api.openalex.org", "export.arxiv.org", "arxiv.org"}:
            raise RuntimeError("Source outside authorized hosts")
        if len(audit["source_requests"]) >= 6:
            raise RuntimeError("Authorized six-source-request budget exhausted")
        entry = {"host": host, "direct": os.environ.get("SRA_SOURCE_DIRECT") == "1"}
        audit["source_requests"].append(entry)
        started = time.monotonic()
        print("LIVE source request", len(audit["source_requests"]), host, flush=True)
        try:
            response = original_source(session, request, **kwargs)
            entry["http_status"] = response.status_code
            return response
        except Exception as exc:
            entry["error_class"] = type(exc).__name__
            raise
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - started, 2)

    httpx2.HTTPTransport.handle_request = model_http
    requests.sessions.Session.send = source_http
    with tempfile.TemporaryDirectory(prefix="sra-step7-live-") as root:
        cfg = configuration.GlobalConfig(
            paths=configuration.ProjectPaths.from_root(root), dry_run=False
        )
        cfg.llm.max_retries = 0  # No hidden SDK retries; registry retries count above.
        cfg.llm.default_timeout_seconds = 60
        configuration._default_config = cfg
        from web import api, workspace_api

        registry = LLMRegistry.from_config(cfg)
        workspace_api.LLMRegistry.from_config = classmethod(
            lambda cls, config: registry
        )
        workspace_api.ResearchTaskService = partial(
            workspace_api.ResearchTaskService,
            limits=SearchLimits(
                max_queries=1, max_candidates=6, max_results_per_call=4
            ),
        )
        with TestClient(api.app, raise_server_exceptions=False) as client:
            result = client.post(
                "/api/conversations/start",
                json={
                    "request_id": "step7-live",
                    "title": "联邦学习数据分布差异下的公平方法比较",
                    "domain": "ai",
                    "guidance_mode": "direct",
                    "entry_stage": "literature",
                },
            ).json()
            pid, cid = result["project_id"], result["conversation_id"]
            base = f"/api/projects/{pid}"
            chat = base + f"/conversations/{cid}"
            response = client.post(
                chat + "/messages",
                json={
                    "request_id": "live-search",
                    "content": "帮我检索 federated learning non-IID FedAvg FedProx comparison 的代表性论文",
                },
            )
            audit["search_start_http"] = response.status_code
            if response.status_code == 202:
                tid = response.json()["task_id"]
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    task = client.get(chat + "/research-tasks/" + tid).json()
                    if task.get("status") not in {"queued", "running"}:
                        break
                    time.sleep(0.25)
                audit["task"] = task
            shared = client.get(base + "/workspace?include_chat=false").json()
            audit["papers"] = shared.get("papers", [])
            # These are explicitly supplied sample conditions, not inferred user facts.
            conditions = {
                "goal": "比较同一公开任务下 FedAvg 与 FedProx 的研究方案",
                "resources": "CPU；GPU、数据许可及具体设备待确认",
                "time_budget": "待确认",
            }
            client.put(
                base + "/workspace/brief",
                json={
                    "expected_revision": shared["workspace"]["revision"],
                    "brief": conditions,
                },
            )
            shared = client.get(base + "/workspace?include_chat=false").json()
            q = client.post(
                base + "/workspace/items",
                json={
                    "expected_revision": shared["workspace"]["revision"],
                    "item": {
                        "kind": "question",
                        "title": "数据划分一致时，怎样公平比较 FedAvg 与 FedProx？",
                        "content": "固定同一公开分类任务与数据划分，如何设计公平比较 FedAvg 与 FedProx 对数据分布差异敏感性的方案？尚未执行。",
                        "paper_ids": [p["paper_id"] for p in audit["papers"][:3]],
                    },
                },
            )
            audit["question_http"] = q.status_code
            response = client.post(
                chat + "/messages",
                json={
                    "request_id": "live-plan",
                    "content": "请基于现有资料为当前问题生成计算研究方案；仅摘要可读时不要声称读过全文，未知条件标记待确认。",
                    "plan_request": {"method": "computational"},
                },
            )
            audit["plan_http"] = response.status_code
            audit["plan_response"] = response.json()
            if response.status_code == 200 and response.json().get("suggestions"):
                sid = response.json()["suggestions"][0]["suggestion_id"]
                revision = client.get(base + "/workspace?include_chat=false").json()[
                    "workspace"
                ]["revision"]
                applied = client.post(
                    chat + f"/suggestions/{sid}/apply",
                    json={"expected_revision": revision},
                )
                audit["adoption_http"] = applied.status_code
                if applied.status_code == 200:
                    item = applied.json()["item"]
                    download = client.get(base + f"/exports/items/{item['item_id']}")
                    audit["export_http"] = download.status_code
                    audit["export_text"] = download.text
            audit["workflow"] = client.get(
                base + "/workspace?include_chat=false"
            ).json()["workflow"]
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(target),
                "models": len(audit["model_requests"]),
                "sources": len(audit["source_requests"]),
                "plan_http": audit.get("plan_http"),
                "task_status": audit.get("task", {}).get("status"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
