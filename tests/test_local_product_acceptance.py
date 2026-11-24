"""Step 7 cross-module journeys; actual routes, temporary data, no external calls."""

import pytest
from types import SimpleNamespace
from tests.test_conversation_home import home, tree
from core.workspace.service import WorkspaceService
from web.project_exports_api import build_exports_router


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import requests
    import httpx
    import httpx2
    from core.llm import LLMRegistry

    def forbidden(*args, **kwargs):
        pytest.fail("Offline acceptance attempted an external call")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(LLMRegistry, "from_config", forbidden)


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def product(home):
    from web import api

    app = home.client.app
    app.add_api_route(
        "/api/projects/{project_id}/upload-paper", api.upload_paper, methods=["POST"]
    )
    app.include_router(
        build_exports_router(
            lambda: home.config,
            api._require_project,
            lambda s: api._workspace_workflow(s, include_materials=True),
            api.download_artifact,
            api._make_download,
            api._md_to_docx_response,
            api._md_to_pdf_response,
        )
    )
    return home


def begin(h, rid="local-start"):
    return ok(
        h.client.post(
            "/api/conversations/start",
            json={"request_id": rid, "title": "联邦学习软件验收", "domain": "ai"},
        )
    )


def shared(h, pid):
    return ok(h.client.get(f"/api/projects/{pid}/workspace?include_chat=false"))


def adopt_plan(h, result):
    pid, cid = result["project_id"], result["conversation_id"]
    base = f"/api/projects/{pid}"
    w = shared(h, pid)["workspace"]
    question = ok(
        h.client.post(
            base + "/workspace/items",
            json={
                "expected_revision": w["revision"],
                "item": {
                    "kind": "question",
                    "title": "两种方法怎样公平比较？",
                    "content": "固定同一任务与划分，比较 FedAvg 与 FedProx；尚未执行。",
                },
            },
        )
    )
    assert question
    reply = ok(
        h.client.post(
            base + f"/conversations/{cid}/messages",
            json={
                "request_id": "own-plan",
                "content": "请生成计算研究方案",
                "plan_request": {"method": "computational"},
            },
        )
    )
    assert reply["status"] == "completed"
    suggestion = reply["suggestions"][0]
    assert not any(i["kind"] == "plan" for i in shared(h, pid)["workspace"]["items"])
    applied = ok(
        h.client.post(
            base
            + f"/conversations/{cid}/suggestions/{suggestion['suggestion_id']}/apply",
            json={"expected_revision": shared(h, pid)["workspace"]["revision"]},
        )
    )
    exported = h.client.get(base + f"/exports/items/{applied['item']['item_id']}")
    assert exported.status_code == 200 and "待确认" in exported.text
    assert shared(h, pid)["workflow"]["flow"]["planReady"]
    assert not any(
        i["kind"] in {"result", "finding", "outline"}
        for i in shared(h, pid)["workspace"]["items"]
    )


def test_vague_interest_import_then_question_plan_adopt_export(product):
    h = product
    r = begin(h)
    assert begin(h) == r
    base = f"/api/projects/{r['project_id']}/conversations/{r['conversation_id']}"
    payload = {"request_id": "vague", "content": "我想做人工智能，但没有做过科研。"}
    first = ok(h.client.post(base + "/messages", json=payload))
    assert (
        first["status"] == "completed"
        and not shared(h, r["project_id"])["workspace"]["items"]
    )
    assert (
        ok(h.client.post(base + "/messages", json=payload))["messages"]
        == first["messages"]
    )
    text = "自己编写的软件测试材料：这不是已发表论文。\n" * 600 + "END_OF_FULL_UPLOAD"
    uploaded = ok(
        h.client.post(
            f"/api/projects/{r['project_id']}/upload-paper",
            files={"file": ("material.md", text.encode(), "text/markdown")},
        )
    )
    service = WorkspaceService(
        r["project_id"],
        h.config.paths.project_db(r["project_id"]),
        initialize_store=False,
    )
    chunks = service.store.get_paper_chunks(uploaded["paper_id"])
    assert "END_OF_FULL_UPLOAD" in "".join(c.text for c in chunks)
    assert "上传文本" in shared(h, r["project_id"])["papers"][0]["reading_scope"]
    adopt_plan(h, r)


@pytest.mark.parametrize(
    "filename,body",
    [
        ("bad.exe", b"x"),
        ("../bad.pdf", b"x"),
        ("empty.md", b""),
        ("encoding.txt", b"\xff"),
    ],
)
def test_invalid_import_does_not_mutate_project(product, filename, body):
    r = begin(product)
    before = tree(product.root)
    response = product.client.post(
        f"/api/projects/{r['project_id']}/upload-paper",
        files={"file": (filename, body, "application/octet-stream")},
    )
    assert response.status_code == 400
    assert tree(product.root) == before


def test_pdf_parse_failure_is_not_abstract_and_same_names_do_not_overwrite(
    product, monkeypatch
):
    import core.tools

    monkeypatch.setattr(core.tools, "mineru_is_available", lambda: True)
    calls = []

    def fail(path):
        calls.append(path)
        raise RuntimeError("injected parser failure")

    monkeypatch.setattr(core.tools, "parse_pdf_with_mineru", fail)
    r = begin(product)
    for body in (b"%PDF-first", b"%PDF-second"):
        uploaded = ok(
            product.client.post(
                f"/api/projects/{r['project_id']}/upload-paper",
                files={"file": ("same.pdf", body, "application/pdf")},
            )
        )
        assert uploaded["parse_error"] and uploaded["chunks"] == 0
    assert len(calls) == 2 and calls[0] != calls[1]
    assert calls[0].read_bytes() == b"%PDF-first"
    for paper in shared(product, r["project_id"])["papers"]:
        assert not paper["abstract"] and paper["scope"] == "仅元信息"
        assert "未读取正文" in paper["reading_scope"]
    db = product.config.paths.project_db(r["project_id"])
    db.rename(db.with_suffix(".backup"))
    before = tree(product.root)
    assert (
        product.client.post(
            f"/api/projects/{r['project_id']}/upload-paper",
            files={"file": ("other.md", b"valid", "text/markdown")},
        ).status_code
        == 404
    )
    assert tree(product.root) == before and not db.exists()


def test_parsed_pdf_is_extracted_once_and_reading_scope_reaches_guidance(
    product, monkeypatch
):
    import core.tools
    from core.workspace.guide import build_guide_context

    calls = []

    def parse(path):
        calls.append(path)
        return SimpleNamespace(
            mode="offline-extractor",
            title="Local fixture PDF",
            full_text="Only a supplied excerpt, not independently verified full text.",
            sections=[
                SimpleNamespace(
                    heading="Excerpt",
                    level=1,
                    page=1,
                    text="The offline fixture contains a readable source excerpt, not research findings.",
                )
            ],
            figures=[],
            tables=[],
            equations=[],
            references=[],
        )

    monkeypatch.setattr(core.tools, "mineru_is_available", lambda: True)
    monkeypatch.setattr(core.tools, "parse_pdf_with_mineru", parse)
    r = begin(product)
    pid = r["project_id"]
    imported = ok(
        product.client.post(
            f"/api/projects/{pid}/upload-paper",
            files={"file": ("sample.pdf", b"%PDF-offline-fixture", "application/pdf")},
        )
    )
    assert len(calls) == 1 and imported["chunks"] == 1 and not imported["parse_error"]
    paper = ok(product.client.get(f"/api/projects/{pid}/papers"))["papers"][0]
    assert paper["reading_scope"] == "PDF 提取片段：未确认全文完整性"
    service = WorkspaceService(
        pid, product.config.paths.project_db(pid), initialize_store=False
    )
    context = build_guide_context(
        service, "Local fixture", "literature", None, [paper["paper_id"]], history=[]
    )
    assert context["papers"][0]["scope"] == paper["reading_scope"]


def test_teaching_to_own_research_can_form_independent_plan(product):
    h = product
    r = ok(
        h.client.post(
            "/api/teaching-cases/start",
            json={
                "request_id": "lesson",
                "case_id": "fl-heterogeneity",
                "version": "1.1.0",
            },
        )
    )
    base = (
        f"/api/projects/{r['project_id']}/conversations/{r['conversation_id']}/teaching"
    )
    for option in (
        "begin",
        "continue",
        "compare",
        "adopt_question",
        "small",
        "show_plan",
        "adopt_plan",
    ):
        state = ok(h.client.get(base))
        ok(
            h.client.post(
                base + "/choices",
                json={
                    "request_id": "choice-" + option,
                    "expected_version": state["state_version"],
                    "option_id": option,
                },
            )
        )
    old = shared(h, r["project_id"])
    own = ok(
        h.client.post(
            base + "/fork",
            json={
                "request_id": "fork",
                "title": "我的联邦学习方法比较",
                "resources": "尚不确定",
            },
        )
    )
    data = shared(h, own["project_id"])
    assert data.get("teaching") is None and not data["workflow"]["flow"]["planReady"]
    assert data["workspace"]["brief"]["time_budget"] == ""
    assert all(i["kind"] == "note" for i in data["workspace"]["items"])
    assert len(data["papers"]) == 3
    adopt_plan(h, own)
    assert shared(h, r["project_id"]) == old


def test_actual_service_restart_restores_bound_case_and_can_continue(tmp_path):
    import json
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path
    from urllib.request import Request, urlopen

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    root = tmp_path / "restart-data"

    def call(path, body=None):
        req = Request(
            f"http://127.0.0.1:{port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    def launch():
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("local_product_test_server.py")),
                "--port",
                str(port),
                "--root",
                str(root),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            try:
                call("/__acceptance__/state")
                return process
            except OSError:
                time.sleep(0.1)
        process.terminate()
        process.wait(timeout=10)
        pytest.fail("Isolated restart server did not start")

    def stop(process):
        if process.poll() is None:
            try:
                call("/__acceptance__/shutdown", {})
                process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)

    process = launch()
    try:
        result = call(
            "/api/teaching-cases/start",
            {
                "request_id": "restart-case",
                "case_id": "fl-heterogeneity",
                "version": "1.1.0",
            },
        )
        base = f"/api/projects/{result['project_id']}/conversations/{result['conversation_id']}"

        def choose(option):
            state = call(base + "/teaching")
            return call(
                base + "/teaching/choices",
                {
                    "request_id": "restart-" + option,
                    "option_id": option,
                    "expected_version": state["state_version"],
                },
            )

        for option in (
            "begin",
            "continue",
            "compare",
            "adopt_question",
            "small",
            "show_plan",
        ):
            choose(option)
        state, messages = call(base + "/teaching"), call(base + "/messages")
        stop(process)
        process = launch()
        before = tree(root)
        assert call(base + "/teaching") == state
        assert call(base + "/messages") == messages
        assert (
            tree(root) == before
        ), "Restart and read must not migrate/repair saved progress"
        choose("adopt_plan")
        assert call(base + "/teaching")["completed"]
        material = call(
            f"/api/projects/{result['project_id']}/workspace?include_chat=false"
        )
        assert material["workflow"]["flow"]["planReady"]
        assert not any(
            i["kind"] in {"result", "finding", "outline"}
            for i in material["workspace"]["items"]
        )
    finally:
        stop(process)
