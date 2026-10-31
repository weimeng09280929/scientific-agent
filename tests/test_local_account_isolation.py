"""Isolated FastAPI account boundary tests; no real model or project directory."""
import importlib
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


@pytest.fixture
def isolated():
    with tempfile.TemporaryDirectory(prefix="sra-auth-") as root:
        root = Path(root)
        env = {
            "SRA_AUTH_MODE": "accounts",
            "SRA_AUTH_INSECURE_HTTP": "1",
            "SRA_PROJECTS_DIR": str(root / "projects"),
            "SRA_DATA_ROOT": str(root),
            "SRA_ACCOUNTS_DB": str(root / "accounts.db"),
            "SRA_WEB_SKIP_GUARD": "1",
            "SRA_WEB_SKIP_PROJECT_SCAN": "1",
            "SRA_DRY_RUN": "true",
            "SRA_ALLOW_LOCAL_REGISTRATION": "0",
        }
        original = {key: os.environ.get(key) for key in env}
        os.environ.update(env)
        try:
            from core.local_accounts import AccountStore
            store = AccountStore(root / "accounts.db")
            store.initialize()
            secret_a = "correct horse battery A!"
            secret_b = "correct horse battery B!"
            store.create_user(" Tester-A@Example.com ", secret_a)
            store.create_user("tester-b@example.com", secret_b)
            from core import config
            config._default_config = None
            from web import api
            importlib.reload(api)
            from tests.local_account_fixture import install
            api._account_test_state = install(api._CONFIG)
            with TestClient(api.app) as a, TestClient(api.app) as b:
                yield root, store, a, b, SecretStr(secret_a), SecretStr(secret_b)
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _login(client, email, password):
    password = password.get_secret_value() if isinstance(password,SecretStr) else password
    result = client.post("/api/auth/login", json={"email": email, "password": password}, headers={"X-SRA-Login":"1"})
    assert result.status_code == 200, result.text
    token = result.json()["csrf_token"]
    client.headers.update({"X-SRA-CSRF": token})
    return result.json()["user"]


def test_accounts_and_routes(isolated):
    root, store, a, b, password_a, password_b = isolated
    assert a.get("/api/projects").status_code == 401
    old=a.get("/static/workspace.html",follow_redirects=False)
    assert old.status_code == 307 and old.headers["location"] == "/workspace"
    assert a.post("/api/auth/login", json={"email":"tester-a@example.com", "password":"wrong"}, headers={"X-SRA-Login":"1"}).status_code == 401
    user_a = _login(a, "TESTER-A@example.COM", password_a)
    user_b = _login(b, "tester-b@example.com", password_b)
    assert user_a["id"] != user_b["id"]
    assert user_a["email"] == "tester-a@example.com"
    assert user_a["email_verified"] is False
    assert a.post("/api/projects", json={"topic":"A project"}, headers={"X-SRA-CSRF":"bad"}).status_code == 403
    pa = a.post("/api/projects", json={"topic":"same name"}).json()["project_id"]
    pb = b.post("/api/projects", json={"topic":"same name"}).json()["project_id"]
    assert pa != pb
    assert {x["project_id"] for x in a.get("/api/projects").json()["projects"]} == {pa}
    assert {x["project_id"] for x in b.get("/api/projects").json()["projects"]} == {pb}
    for method, path in [
        ("get", f"/api/projects/{pa}/status"),
        ("get", f"/api/projects/{pa}/dashboard"),
        ("get", f"/api/projects/{pa}/notes"),
        ("get", f"/api/projects/{pa}/download/research-report"),
        ("get", f"/api/projects/{pa}/papers/notreal/pdf"),
        ("post", f"/api/projects/{pa}/notes"),
        ("post", f"/api/projects/{pa}/upload-paper"),
        ("delete", f"/api/projects/{pa}"),
    ]:
        response = getattr(b, method)(path)
        assert response.status_code == 404, (path, response.status_code, response.text)
    assert b.post("/api/projects/batch-delete", json={"project_ids":[pa]}).status_code == 404
    assert a.get("/api/projects/nonexistent/status").status_code == 404
    assert b.get("/api/projects", params={"q":"same name"}).json()["total"] == 1
    assert b.get("/api/projects", params={"include_project_id":pa}).json()["current_project"] is None
    assert b.get("/api/conversations/recent").status_code == 200
    start = {"request_id":"same-start", "title":"independent study", "domain":"computer_science"}
    started_a = a.post("/api/conversations/start", json=start)
    started_b = b.post("/api/conversations/start", json=start)
    assert started_a.status_code == 200, started_a.text
    assert started_b.status_code == 200, started_b.text
    assert started_a.json()["project_id"] != started_b.json()["project_id"]
    again = a.post("/api/conversations/start", json=start)
    assert again.status_code == 200
    assert again.json()["project_id"] == started_a.json()["project_id"]
    assert b.get(f"/api/projects/{started_a.json()['project_id']}/status").status_code == 404
    cid_b = started_b.json()["conversation_id"]
    mixed = a.get(f"/api/projects/{started_a.json()['project_id']}/conversations/{cid_b}")
    assert mixed.status_code == 404
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get(f"/api/projects/{pa}/status").status_code == 401
    assert b.get(f"/api/projects/{pb}/status").status_code == 200
    store.disable_user("tester-b@example.com")
    assert b.get(f"/api/projects/{pb}/status").status_code == 401
    assert a.get("/api/projects").status_code == 401
    assert (root / "projects" / pa / "knowledge.db").exists()
    for path in (f"/projects/{pa}/knowledge.db",f"/static/../../projects/{pa}/knowledge.db"):
        assert a.get(path).status_code == 404


def test_email_normalization_and_duplicate(isolated):
    _, store, _, _, _, _ = isolated
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.create_user("TESTER-A@EXAMPLE.COM", "another safe password")
    uid = store.create_user("tester-a+alias@example.com", "another safe password")
    assert uid


def test_expiry_rate_limit_and_unclaimed(isolated):
    root, store, a, _, password_a, _ = isolated
    from core.knowledge import KnowledgeStore
    import sqlite3
    from contextlib import closing
    old = root / "projects" / "historical_unclaimed" / "knowledge.db"
    KnowledgeStore(old)
    _login(a, "tester-a@example.com", password_a)
    names = {row["project_id"] for row in a.get("/api/projects").json()["projects"]}
    assert "historical_unclaimed" not in names
    assert a.get("/api/projects/historical_unclaimed/status").status_code == 404
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("UPDATE sessions SET expires_at=0 WHERE user_id=(SELECT id FROM users WHERE email=?)",
                     ("tester-a@example.com",))
    assert a.get("/api/projects").status_code == 401
    for _ in range(5):
        assert store.login("unknown@example.com", "incorrect", "192.0.2.9") is None
    assert store.login("tester-a@example.com", password_a.get_secret_value(), "192.0.2.9") is None


def test_real_resources_and_every_project_route(isolated, monkeypatch):
    import io
    import json
    import time
    from tests.local_account_fixture import minimal_pdf
    from web import api
    # A successful import now requires readable parsed content. Keep this a
    # positive ownership/download regression, with an explicit offline parser.
    from types import SimpleNamespace
    import core.tools
    monkeypatch.setattr(core.tools, "mineru_is_available", lambda: True)
    monkeypatch.setattr(core.tools, "parse_pdf_with_mineru", lambda *args: SimpleNamespace(
        mode="fixture", sections=[], figures=[], title="", full_text="Offline parsed attachment for isolation checks",
        tables=[], equations=[], references=[]))
    root, store, a, b, password_a, password_b = isolated
    _login(a, "tester-a@example.com", password_a)
    _login(b, "tester-b@example.com", password_b)
    identities = []
    for client, label in ((a,"A"),(b,"B")):
        result = client.post("/api/conversations/start", json={"request_id":"shared-start", "title":"same title"})
        assert result.status_code == 200, result.text
        pid, cid = result.json()["project_id"], result.json()["conversation_id"]
        assert client.post(f"/api/projects/{pid}/notes", json={"text":label+" private note"}).status_code == 200
        upload = client.post(f"/api/projects/{pid}/upload-paper", files={"file":("same.pdf",minimal_pdf(),"application/pdf")},data={"request_id":"shared-upload", "title":label+" attachment"})
        assert upload.status_code == 200, upload.text
        paper = upload.json()["paper_id"]
        pdf = client.get(f"/api/projects/{pid}/papers/{paper}/pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        task = client.post(f"/api/projects/{pid}/conversations/{cid}/research-tasks", json={"request_id":"shared-task","content":"请检索离线资料并整理现状","topic":"offline account research"})
        assert task.status_code == 200, task.text
        tid = task.json()["task_id"]
        for _ in range(100):
            view = client.get(f"/api/projects/{pid}/conversations/{cid}/research-tasks/{tid}").json()
            if view["status"] in {"completed","partial","failed","cancelled"}: break
            time.sleep(.1)
        assert view["status"] == "completed", view
        report = client.get(f"/api/projects/{pid}/research-tasks/{tid}/export",params={"version":1})
        assert report.status_code == 200 and "参考文献与阅读范围" in report.text
        assert "private, no-store" in report.headers["cache-control"]
        identities.append((pid,cid,paper,tid))
    pa,ca,paper_a,task_a=identities[0]
    pb,cb,paper_b,task_b=identities[1]
    assert pa != pb
    assert a.get(f"/api/projects/{pa}/conversations/{cb}/research-tasks/{task_b}").status_code == 404
    assert a.get(f"/api/projects/{pa}/research-tasks/{task_b}/export").status_code == 404
    assert a.get(f"/api/projects/{pa}/papers/{paper_b}/pdf").status_code == 404
    assert a.post(f"/api/projects/{pa}/conversations/{cb}/research-tasks/{task_a}/cancel").status_code == 404
    # Exercise all installed project route methods, including legacy execution,
    # upload, reports, audit, restart, adoption and historical downloads.
    matrix=[]
    values={"project_id":pa,"conversation_id":ca,"task_id":task_a,"paper_id":paper_a,
            "request_id":"shared-task","artifact_type":"research-report",
            "suggestion_id":"known-draft","item_id":"known-item","artifact_id":"known-artifact","conflict_id":"known-conflict",
            "message_id":"known-message","action_id":"known-action"}
    global_routes={"/api/projects","/api/projects/batch-delete","/api/conversations/start","/api/conversations/recent",
                   "/api/teaching-cases","/api/teaching-cases/start","/api/conversation-capabilities","/api/data-sources",
                   "/api/auth/me","/api/auth/login","/api/auth/logout","/api/auth/options","/api/auth/register","/api/resource-limits"}
    with TestClient(api.app) as anonymous:
        for template, operations in api.app.openapi()["paths"].items():
            if template.startswith("/api/") and not template.startswith("/api/projects/{project_id}"):
                assert template in global_routes, template
            if not template.startswith("/api/projects/{project_id}"): continue
            path=template
            for key,value in values.items(): path=path.replace("{"+key+"}",value)
            assert "{" not in path, path
            for method in sorted(set(operations) & {"get","post","put","patch","delete"}):
                other=b.request(method,path)
                anon=anonymous.request(method,path)
                assert other.status_code == 404, (method,template,other.status_code)
                assert anon.status_code == 401, (method,template,anon.status_code)
                matrix.append({"method":method,"route":template,"non_owner":404,"anonymous":401})
        store.disable_user("tester-b@example.com")
        for row in matrix:
            path=row["route"]
            for key,value in values.items(): path=path.replace("{"+key+"}",value)
            assert b.request(row["method"],path).status_code == 401
            row["disabled"]=401
    assert len(matrix)>60
    if os.environ.get("SRA_ACCOUNT_EVIDENCE_DIR"):
        target=Path(os.environ["SRA_ACCOUNT_EVIDENCE_DIR"]);target.mkdir(parents=True,exist_ok=True)
        (target/"route-coverage.json").write_text(json.dumps(matrix,ensure_ascii=False,indent=2),encoding="utf-8")
    assert api._account_test_state["public_calls"] == 0


def test_csrf_cookie_session_rotation_and_restart(isolated):
    from core.local_accounts import AccountStore
    root, store, a, _, password_a, _ = isolated
    body={"email":"tester-a@example.com","password":password_a.get_secret_value()}
    assert a.post("/api/auth/login",json=body).status_code == 403
    assert a.post("/api/auth/login",json=body,headers={"X-SRA-Login":"1","Origin":"https://other.invalid"}).status_code == 403
    first=a.post("/api/auth/login",json=body,headers={"X-SRA-Login":"1"})
    cookie=first.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie and "max-age=43200" in cookie
    old=a.cookies.get("sra_session")
    assert AccountStore(store.path).session(old) is not None  # new store/process state needs no in-memory registry
    assert a.post("/api/projects",json={"topic":"no csrf"}).status_code == 403
    second=a.post("/api/auth/login",json=body,headers={"X-SRA-Login":"1"})
    assert second.status_code == 200
    assert store.session(old) is None
    assert a.get("/api/projects",headers={"X-SRA-CSRF":first.json()["csrf_token"]}).status_code == 401
    a.headers["X-SRA-CSRF"]=second.json()["csrf_token"]
    assert a.get("/api/projects").status_code == 200
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/projects").status_code == 401


def test_file_containment_and_owner_archive(isolated):
    from core.knowledge import KnowledgeStore
    from core.knowledge.schema import Paper
    root, store, a, b, password_a, password_b = isolated
    _login(a,"tester-a@example.com",password_a);_login(b,"tester-b@example.com",password_b)
    pa=a.post("/api/projects",json={"topic":"A"}).json()["project_id"]
    pb=b.post("/api/projects",json={"topic":"B"}).json()["project_id"]
    secret=root/"projects"/pb/"secret.pdf";secret.write_bytes(b"%PDF-B-private")
    knowledge=KnowledgeStore(root/"projects"/pa/"knowledge.db")
    knowledge.save_paper(Paper(paper_id="outside-file",title="path fixture",pdf_path=str(secret)))
    assert a.get(f"/api/projects/{pa}/papers/outside-file/pdf").status_code == 404
    before=(root/"projects"/pb/"knowledge.db").read_bytes()
    archived=a.post("/api/projects/batch-delete",json={"project_ids":[pa]})
    assert archived.status_code == 200, archived.text
    assert (root/"projects"/pb/"knowledge.db").read_bytes() == before
    assert a.get(f"/api/projects/{pa}/status").status_code == 404
    assert b.get(f"/api/projects/{pa}/status").status_code == 404
    assert list((root/".project-trash").glob("*/"+pa+"/knowledge.db"))


def test_secure_cookie_and_explicit_local_mode(isolated, monkeypatch):
    from web import api
    _, _, _, _, password_a, _ = isolated
    monkeypatch.delenv("SRA_AUTH_INSECURE_HTTP")
    with TestClient(api.app,base_url="https://testserver") as secure:
        result=secure.post("/api/auth/login",headers={"X-SRA-Login":"1"},json={"email":"tester-a@example.com","password":password_a.get_secret_value()})
        assert "secure" in result.headers["set-cookie"].lower()
        assert secure.get("/api/auth/me").status_code == 200
    monkeypatch.setattr(api,"_AUTH_MODE","local")
    with TestClient(api.app,client=("192.0.2.3",12000)) as remote:
        assert remote.get("/api/projects").status_code == 403
    with TestClient(api.app) as local:
        assert local.get("/api/projects").status_code == 200
        assert local.get("/api/projects",headers={"Host":"outside.invalid"}).status_code == 403
        assert local.post("/api/projects",headers={"Origin":"https://outside.invalid"},json={"topic":"cross-site"}).status_code == 403


def test_password_bounds_and_read_download_does_not_migrate(isolated):
    from core.local_accounts import validate_password
    from core.knowledge import KnowledgeStore
    from core.knowledge.schema import Paper
    from tests.local_account_fixture import minimal_pdf
    from contextlib import closing
    import sqlite3
    root, _, a, _, password_a, _=isolated
    for value in ("x"*7,"x"*257):
        with pytest.raises(ValueError):validate_password(value)
    validate_password("Ab7!"*64)
    _login(a,"tester-a@example.com",password_a)
    invalid=a.post("/api/auth/login",headers={"X-SRA-Login":"1"},json={"email":"tester-a@example.com","password":"sensitive-invalid-"*30})
    assert invalid.status_code == 401 and "sensitive-invalid" not in invalid.text
    pid=a.post("/api/projects",json={"topic":"historical shape"}).json()["project_id"]
    db=root/"projects"/pid/"knowledge.db"
    pdf=db.parent/"existing.pdf";pdf.write_bytes(minimal_pdf())
    KnowledgeStore(db).save_paper(Paper(paper_id="saved-pdf",title="existing",pdf_path=str(pdf)))
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TABLE evidence_log")
    before=db.read_bytes()
    assert a.get(f"/api/projects/{pid}/papers/saved-pdf/pdf").status_code == 200
    assert db.read_bytes() == before


def test_auth_precedes_teaching_guard(isolated):
    _, _, a, b, password_a, password_b = isolated
    _login(a,"tester-a@example.com",password_a);_login(b,"tester-b@example.com",password_b)
    catalog=a.get("/api/teaching-cases").json()["cases"]
    case=catalog[0]
    result=a.post("/api/teaching-cases/start",json={"request_id":"owned-lesson","case_id":case["case_id"],"version":case["version"]})
    assert result.status_code == 200, result.text
    pid=result.json()["project_id"]
    assert a.post(f"/api/projects/{pid}/run",headers={"X-SRA-CSRF":"invalid"}).status_code == 403
    assert b.post(f"/api/projects/{pid}/run").status_code == 404


def test_background_publication_stays_owned_after_logout(isolated, monkeypatch):
    import threading
    import time
    from core.llm import LLMRegistry
    _, _, a, b, password_a, password_b=isolated
    _login(a,"tester-a@example.com",password_a);_login(b,"tester-b@example.com",password_b)
    created=a.post("/api/conversations/start",json={"request_id":"background-owned","title":"A background"}).json()
    pid,cid=created["project_id"],created["conversation_id"]
    ready,release=threading.Event(),threading.Event()
    registry=LLMRegistry.from_config(None)
    class Delayed:
        def complete(self,kind,*args,**kwargs):
            if kind=="research_synthesize":
                ready.set()
                if not release.wait(10):raise AssertionError("offline gate not released")
            return registry.complete(kind,*args,**kwargs)
    monkeypatch.setattr(LLMRegistry,"from_config",classmethod(lambda cls,cfg:Delayed()))
    try:
        started=a.post(f"/api/projects/{pid}/conversations/{cid}/research-tasks",json={"request_id":"background-task","content":"请检索离线资料并整理现状","topic":"offline account research"})
        assert started.status_code == 200, started.text
        assert ready.wait(5)
        tid=started.json()["task_id"]
        assert a.post("/api/auth/logout").status_code == 200
        assert a.get(f"/api/projects/{pid}/conversations/{cid}/research-tasks/{tid}").status_code == 401
        assert b.get(f"/api/projects/{pid}/conversations/{cid}/research-tasks/{tid}").status_code == 404
        release.set();_login(a,"tester-a@example.com",password_a)
        for _ in range(100):
            view=a.get(f"/api/projects/{pid}/conversations/{cid}/research-tasks/{tid}").json()
            if view["status"]=="completed":break
            time.sleep(.1)
        assert view["status"]=="completed"
        assert b.get(f"/api/projects/{pid}/research-tasks/{tid}/export").status_code==404
    finally:
        release.set()
