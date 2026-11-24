"""Isolated Step 5 UI fixture: conversation-driven research tasks.

Real app, temp databases, controllable retrieval fixtures, no provider calls
and no public network requests. Reuses the conversation-home server base.
"""

import argparse
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"
os.environ["SRA_DRY_RUN"] = "true"


class FakeRegistry:
    """离线模型夹具：规划/筛选/整理返回合法 JSON，引用只允许本轮候选。"""

    def complete(self, task_type, prompt=None, **kwargs):
        from types import SimpleNamespace

        if task_type == "research_plan":
            return SimpleNamespace(
                text='{"queries": [{"query": "FIXTURE_QUERY 联邦学习隐私保护", "rationale": "夹具查询"}]}'
            )
        if task_type == "research_rank":
            return SimpleNamespace(text='{"rankings": []}')
        return SimpleNamespace(
            text='{"summary": "夹具整理：仅覆盖本轮取得的候选文献，不代表领域全貌。",'
            ' "representative": [], "methods_relations": "", "limitations": [],'
            ' "gaps": ["夹具环境未连接真实来源"], "next_steps": ["继续阅读候选论文"]}'
        )


def build(temporary):
    from tests.conversation_home_test_server import build as base_build

    app = base_build(temporary)

    from core.llm import LLMRegistry
    from core.research_tasks import sources as rt_sources

    LLMRegistry.from_config = classmethod(lambda cls, cfg: FakeRegistry())

    control = {"mode": "ok", "calls": [], "seq": 0}
    gate = threading.Semaphore(0)

    def make_fake(name):
        def fn(query, max_results=10, source_subquery="", year_from=None, year_to=None, timeout=30):
            control["seq"] += 1
            seq = control["seq"]
            control["calls"].append({"source": name, "query": query, "seq": seq})
            mode = control["mode"]
            if mode == "blocked":
                gate.acquire(timeout=30)  # 模拟无法即时中断的外部调用
            if mode == "fail" or (mode == "partial" and name == "arxiv"):
                raise RuntimeError("HTTP 429 fixture")
            if mode == "zero":
                return []

            class Paper:
                def to_meta_dict(self):
                    return {
                        "title": f"Fixture Paper {seq}: {query}",
                        "authors": ["Fixture Author"],
                        "year": 2024,
                        "abstract": f"Fixture abstract about {query}. Not scientific evidence.",
                        "doi": f"10.1/fix{seq}",
                        "arxiv_id": None,
                        "url": f"https://fixture.invalid/{seq}",
                        "pdf_url": "",
                        "venue": "Fixture Venue",
                        "source_subquery": "",
                    }

            return [Paper() for _ in range(3)]

        return fn

    rt_sources.SOURCE_FUNCS = {
        "openalex": make_fake("openalex"),
        "arxiv": make_fake("arxiv"),
    }

    @app.get("/__research__/state")
    def research_state():
        return {**control, "pid": os.getpid(), "root": str(temporary)}

    @app.post("/__research__/mode")
    def research_mode(request: dict):
        control["mode"] = request.get("mode", "ok")
        return {"mode": control["mode"]}

    @app.post("/__research__/release")
    def research_release():
        gate.release()
        return {"released": True}

    @app.post("/__research__/reset")
    def research_reset():
        control["mode"] = "ok"
        control["calls"] = []
        control["seq"] = 0
        return {"reset": True}

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8017)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sra-research-ui-") as temporary:
        uvicorn.run(
            build(temporary), host="127.0.0.1", port=args.port, log_level="warning"
        )
