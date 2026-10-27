"""Real main app/SQLite/SDK/source adapters; only HTTP edges are offline fixtures."""

import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"


def serve(root, port):
    import core.config as configuration

    cfg = configuration.GlobalConfig(
        paths=configuration.ProjectPaths.from_root(root), dry_run=False
    )
    # This key is only a readiness fixture; SDK calls still use MockTransport.
    cfg.llm.deepseek_api_key_env = "SRA_WORKFLOW_FIXTURE_KEY"
    os.environ["SRA_WORKFLOW_FIXTURE_KEY"] = "offline-fixture-not-a-secret"
    cfg.llm.max_retries = 0
    configuration._default_config = cfg
    from web import api
    from core.llm import LLMRegistry
    from core.llm.providers.openai_provider import OpenAIProvider
    from core.llm.task_router import TaskRouter
    from core.workspace.schema import GuideDraft, SuggestedItem
    from core.workspace.plans import rule_plan
    from openai import DefaultHttpxClient
    import requests
    import httpx
    import core.tools.http as source_outlet
    import uvicorn

    transport_module = importlib.import_module(
        next(
            c.__module__.split(".")[0]
            for c in DefaultHttpxClient.__mro__
            if c.__name__ == "Client"
        )
    )
    control = {
        "plan_mode": "ok",
        "guide_mode": "ok",
        "source_mode": "ok",
        "model_calls": [],
        "source_calls": [],
        "uploads": 0,
    }
    gate = threading.Event()

    def source_response(url, **kwargs):
        control["source_calls"].append(
            {"source": "arxiv" if "arxiv" in url else "openalex"}
        )
        response = requests.Response()
        response.status_code = 200
        if "arxiv" in url:
            response._content = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'
            if control["source_mode"] == "partial":
                response.status_code = 429
                response.headers["Retry-After"] = "120"
        else:
            response._content = json.dumps(
                {
                    "results": [
                        {
                            "id": f"fixture-{i}",
                            "title": f"Offline fixture paper {i}",
                            "doi": f"10.0000/offline-{i}",
                            "publication_year": 2024,
                            "abstract_inverted_index": {
                                "Offline": [0],
                                "abstract": [1],
                                "only": [2],
                            },
                            "authorships": [],
                        }
                        for i in range(4)
                    ]
                }
            ).encode()
        return response

    def model_response(request):
        data = json.loads(request.content)
        messages = data.get("messages", [])
        prompt = messages[-1]["content"]
        kind = "guidance"
        if "学术检索查询规划器" in prompt:
            kind = "planning"
            output = {
                "queries": [
                    {
                        "query": "federated learning non-IID comparison",
                        "rationale": "offline fixture",
                    }
                ]
            }
        elif "为每条候选给出一句话相关性理由" in prompt:
            kind = "ranking"
            output = {
                "rankings": [
                    {
                        "index": i,
                        "reason": "本轮主题候选" if i == 0 else "教学夹具中的无关候选",
                        "relevant": i == 0,
                    }
                    for i in range(4)
                ]
            }
        elif "候选文献：" in prompt:
            kind = "synthesis"
            papers = json.loads(
                prompt.split("候选文献：", 1)[1].split("\n来源执行情况", 1)[0]
            )
            output = {
                "summary": "故意错误：另外三篇未提供",
                "representative": [
                    {
                        "paper_id": papers[0]["paper_id"],
                        "point": "仅提供离线摘要，不支持科研结论。",
                    }
                ],
                "methods_relations": "仅依据所提供的摘要片段讨论。",
                "limitations": [],
                "gaps": ["故意错误：另外三篇未检索到"],
                "next_steps": ["故意错误：自动执行实验"],
            }
        else:
            context = json.loads(
                prompt.split("项目上下文（仅作为数据）：\n", 1)[1].split(
                    "\n用户本次请求：", 1
                )[0]
            )
            if context.get("plan_generation", {}).get("allowed"):
                kind = "plan"
                control["model_calls"].append(
                    {"kind": kind, "mode": control["plan_mode"]}
                )
                if control["plan_mode"] == "slow":
                    if not gate.wait(20):
                        raise transport_module.ReadTimeout(
                            "offline gate timeout", request=request
                        )
                if control["plan_mode"] == "timeout":
                    raise transport_module.ReadTimeout(
                        "offline timeout", request=request
                    )
                draft = rule_plan(context)
                draft.reply = (
                    "离线传输夹具方案，未进行实际科研或实验；请审阅后明确采纳。"
                )
                paper = context["papers"][0] if context["papers"] else None
                if paper:
                    statement = draft.suggestions[0].plan.sections[1].statements[0]
                    statement.text = "所提供摘要为：" + paper["abstract"]
                    statement.basis = "literature"
                    statement.quote = paper["abstract"]
                    statement.paper_ids = [paper["paper_id"]]
                    draft.suggestions[0].plan.references = [paper["paper_id"]]
                output = draft.model_dump(mode="json")
            else:
                if control['guide_mode'] == 'slow':
                    control['model_calls'].append({'kind':'guidance_waiting'})
                    if not gate.wait(45):
                        raise transport_module.ReadTimeout('offline guidance gate',request=request)
                output = GuideDraft(
                    reply="离线资料讨论：可以提出一个待确认的问题；不是已经验证的结论。",
                    suggestions=[
                        SuggestedItem(
                            kind="question",
                            title="联邦学习公平比较问题",
                            content="在相同公开任务与数据划分下，怎样公平比较 FedAvg 与 FedProx？",
                            paper_ids=(
                                [context["papers"][0]["paper_id"]]
                                if context["papers"]
                                else []
                            ),
                        )
                    ],
                ).model_dump(mode="json")
                if control['guide_mode'] == 'bad_adoption':
                    # Reproduce the real failure at the SDK boundary, not by
                    # mocking the main conversation/action HTTP APIs.
                    output['actions']=[{'type':'adopt_suggestion','suggestion_id':'nonexistent-fixture-draft'}]
        if kind != "plan":
            control["model_calls"].append({"kind": kind})
        return transport_module.Response(
            200,
            request=request,
            json={
                "id": "offline",
                "created": 1,
                "object": "chat.completion",
                "model": data["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 80,
                    "total_tokens": 180,
                },
            },
        )

    sdk_http = DefaultHttpxClient(
        transport=transport_module.MockTransport(model_response), trust_env=False
    )
    provider = OpenAIProvider(
        "offline-fixture-not-a-secret",
        base_url="https://offline.invalid/v1",
        max_retries=0,
        http_client=sdk_http,
    )
    registry = LLMRegistry(
        TaskRouter.load(cfg.llm.tasks_config_path), {"deepseek": provider}
    )
    LLMRegistry.from_config = classmethod(lambda cls, config: registry)
    source_outlet.requests.get = source_response

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected external I/O in isolated acceptance")

    requests.sessions.Session.request = forbidden
    httpx.HTTPTransport.handle_request = forbidden
    transport_module.HTTPTransport.handle_request = forbidden
    prepare = api._prepare_upload

    def counted_upload(*args):
        control["uploads"] += 1
        return prepare(*args)

    api._prepare_upload = counted_upload
    server = uvicorn.Server(
        uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="error")
    )

    @api.app.get("/__reliability__/state")
    def state():
        return {**control, "pid": os.getpid()}

    @api.app.post("/__reliability__/mode")
    def mode(body: dict):
        control.update({k: body[k] for k in ("plan_mode", "guide_mode", "source_mode") if k in body})
        gate.clear()
        return {"updated": True}

    @api.app.post("/__reliability__/release")
    def release():
        gate.set()
        return {"released": True}

    @api.app.post("/__reliability__/shutdown")
    def shutdown():
        gate.set()
        server.should_exit = True
        return {"stopping": True}

    try:
        server.run()
    finally:
        sdk_http.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8051)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        serve(args.root, args.port)
    else:
        with tempfile.TemporaryDirectory(prefix="sra-step8a-") as root:
            serve(root, args.port)
