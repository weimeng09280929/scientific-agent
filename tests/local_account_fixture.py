"""Offline boundaries shared by account API and browser acceptance fixtures."""
import json
from types import SimpleNamespace


def minimal_pdf():
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>"]
    data = b"%PDF-1.4\n"; offsets=[0]
    for index, obj in enumerate(objects,1):
        offsets.append(len(data));data+=str(index).encode()+b" 0 obj\n"+obj+b"\nendobj\n"
    start=len(data);data+=b"xref\n0 4\n0000000000 65535 f \n"
    for offset in offsets[1:]:data+=f"{offset:010d} 00000 n \n".encode()
    return data+f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


def install(config):
    from core.llm import LLMRegistry
    from core.research_tasks import sources
    import core.tools
    import requests

    state = {"model_calls": 0, "source_calls": 0, "public_calls": 0}

    class Registry:
        def complete(self, task_type, prompt=None, **kwargs):
            state["model_calls"] += 1
            if task_type == "research_plan":
                result = {"queries":[{"query":"offline account research","rationale":"隔离验收"}]}
            elif task_type == "research_rank":
                result = {"rankings":[]}
            elif task_type == "research_synthesize":
                rows = json.loads(prompt.split("候选文献：",1)[1].split("\n来源执行情况",1)[0])
                result = {"representative":[{"paper_id":r["paper_id"],"point":"离线摘要提供了一个待核查的研究对象。"} for r in rows[:2]],
                          "methods_relations":"","limitations":[{"point":"离线夹具不提供真实科研验证。","paper_ids":[r["paper_id"] for r in rows[:1]]}]}
            else:
                raise AssertionError("Unexpected offline model request: " + task_type)
            return SimpleNamespace(text=json.dumps(result, ensure_ascii=False))

    LLMRegistry.from_config = classmethod(lambda cls, cfg: Registry())
    config.dry_run = False

    def source(name):
        def call(*args, **kwargs):
            state["source_calls"] += 1
            return [SimpleNamespace(to_meta_dict=lambda: {
                "title":"Offline account study " + name, "authors":["Fixture Author"],
                "year":2024, "doi":"10.0000/account-" + name,
                "url":"https://fixture.invalid/" + name,
                "abstract":"Offline abstract for isolation verification. Not scientific evidence.",
                "arxiv_id":None,
            })]
        return call
    sources.SOURCE_FUNCS = {name:source(name) for name in sources.SOURCE_FUNCS}
    core.tools.mineru_is_available = lambda: False

    def forbidden(*args, **kwargs):
        state["public_calls"] += 1
        raise AssertionError("Public network forbidden in local account acceptance")
    requests.sessions.Session.request = forbidden
    return state
