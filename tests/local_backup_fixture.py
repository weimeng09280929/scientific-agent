"""Disposable sample and app context; public network edges are forbidden."""
from contextlib import contextmanager, closing
import importlib
import json
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

EMAILS = ("backup-a@example.com", "backup-b@example.com")
PASSWORDS = (SecretStr("Offline restore sample A!"), SecretStr("Offline restore sample B!"))


@contextmanager
def app_at(root):
    root = Path(root).resolve()
    env = {"SRA_AUTH_MODE": "accounts", "SRA_AUTH_INSECURE_HTTP": "1",
           "SRA_DATA_ROOT": str(root), "SRA_PROJECTS_DIR": str(root / "projects"),
           "SRA_ACCOUNTS_DB": str(root / "accounts.db"), "SRA_WEB_SKIP_GUARD": "1",
           "SRA_WEB_SKIP_PROJECT_SCAN": "0", "SRA_DRY_RUN": "true", "SRA_ALLOW_LOCAL_REGISTRATION": "0"}
    with patch.dict(os.environ, env):
        from core import config
        config._default_config = None
        from web import api
        importlib.reload(api)
        from tests.local_account_fixture import install
        from core.workspace.schema import GuideDraft
        from core.workspace.plans import rule_plan
        from core.invocation_audit import ACTIVE
        from core.llm import LLMRegistry
        from core.research_tasks import sources
        import core.tools
        import requests

        # install() also serves standalone browser fixtures. Restore its
        # process-wide substitutes when an in-process test exits.
        with patch.object(LLMRegistry, "from_config", LLMRegistry.__dict__["from_config"]), \
             patch.object(sources, "SOURCE_FUNCS", sources.SOURCE_FUNCS), \
             patch.object(core.tools, "mineru_is_available", core.tools.mineru_is_available), \
             patch.object(requests.sessions.Session, "request", requests.sessions.Session.request):
            state = install(api._CONFIG)
            state.update(guidance_calls=0, fault=None)

            def generate(registry, area, domain, text, context, purpose):
                state["guidance_calls"] += 1
                audit = ACTIVE.get()
                ticket = audit.begin("network", "model", "offline-backup")
                audit.finish(ticket, outcome="error" if state["fault"] else "ok")
                if state["fault"] == "timeout":
                    raise TimeoutError("offline backup sample failure")
                if state["fault"] == "structure":
                    from core.workspace.service import WorkspaceError
                    raise WorkspaceError("离线依据校验失败")
                return rule_plan(context) if context.get("plan_generation", {}).get("allowed") else GuideDraft(reply="恢复副本离线回复，仍需用户核对。")

            from core.resource_limits import RESOURCES, ResourceLimits
            from dataclasses import replace
            with patch("web.workspace_api.generate_guidance", generate), \
                 patch.object(core.tools, "mineru_is_available", lambda: True), \
                 patch.object(core.tools, "parse_pdf_with_mineru", lambda *a: SimpleNamespace(mode="offline", sections=[], figures=[], tables=[], equations=[], references=[], title=None, full_text="Offline PDF text; no scientific result.")), \
                 patch.object(RESOURCES, "limits", replace(ResourceLimits(), heavy_user=1, heavy_global=2)):
                yield api, state


def login(client, index):
    r = client.post("/api/auth/login", headers={"X-SRA-Login": "1"}, json={"email": EMAILS[index], "password": PASSWORDS[index].get_secret_value()})
    assert r.status_code == 200, "Disposable login failed"
    client.headers["X-SRA-CSRF"] = r.json()["csrf_token"]
    return r.json()["user"]


def expect(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def seed(root):
    root = Path(root)
    from core.local_accounts import AccountStore
    store = AccountStore(root / "accounts.db"); store.initialize()
    ids = [store.create_user(email, password.get_secret_value()) for email, password in zip(EMAILS, PASSWORDS)]
    result = {"users": [], "user_ids": ids}
    from tests.local_account_fixture import minimal_pdf
    from core.artifacts.version import ArtifactManager, ArtifactContentStore
    from core.knowledge import KnowledgeStore
    from core.knowledge.schema import ArtifactType
    from core.workspace.conversations import ConversationService, ConversationStorageError
    with app_at(root) as (api, state):
        with TestClient(api.app) as a, TestClient(api.app) as b:
            for index, client in enumerate((a, b)):
                login(client, index)
                start_body = {"request_id": "same-start", "title": "同名恢复样本", "domain": "ai"}
                started = expect(client.post("/api/conversations/start", json=start_body))
                pid, cid = started["project_id"], started["conversation_id"]
                base = f"/api/projects/{pid}"
                chat = base + f"/conversations/{cid}"
                second = expect(client.post(base + "/conversations", json={"title": "第二个对话", "request_id": "second"}))["conversation"]["conversation_id"]
                upload = expect(client.post(base + "/upload-paper", files={"file": ("same.pdf", minimal_pdf(), "application/pdf")}, data={"request_id": "same-upload"}))
                workspace = expect(client.get(base + "/workspace"))["workspace"]
                workspace = expect(client.put(base + "/workspace/brief", json={"expected_revision": workspace["revision"], "brief": {"resources": "只有 CPU，没有 GPU", "time_budget": "四周"}}))["workspace"]
                question = expect(client.post(base + "/workspace/items", json={"expected_revision": workspace["revision"], "item": {"kind": "question", "title": "明确采纳的正式问题", "content": "在 CPU 条件下比较两种计算方法，尚未执行。", "paper_ids": [upload["paper_id"]]}}))["workspace"]["items"][-1]
                versions = []
                for v in (1, 2):
                    body = {"request_id": f"plan-{v}", "content": "请生成计算研究方案" if v == 1 else "请修订当前计算研究方案", "plan_request": {"method": "computational", "question_id": question["item_id"], **({"supersedes_id": versions[-1]} if versions else {})}, "wait_for_completion": True}
                    draft = expect(client.post(chat + "/messages", json=body))["suggestions"][0]
                    applied = expect(client.post(chat + f"/suggestions/{draft['suggestion_id']}/apply", json={}))
                    assert applied["item"]["plan_version"] == v
                    versions.append(applied["item"]["item_id"])
                # A third version remains a draft and cannot displace v2.
                draft = expect(client.post(chat + "/messages", json={"request_id": "unadopted", "content": "请修订当前计算研究方案", "plan_request": {"method": "computational", "supersedes_id": versions[-1]}, "wait_for_completion": True}))["suggestions"][0]
                note = expect(client.post(base + "/notes", json={"text": "恢复后仍属于本账号的笔记 " + str(index)}))
                task_url = chat + "/research-tasks"
                task = expect(client.post(task_url, json={"request_id": "same-report", "content": "检索离线资料", "topic": "offline account research"}))
                deadline = time.monotonic() + 20
                while True:
                    task = expect(client.get(task_url + "/" + task["task_id"]))
                    if task["status"] in ("completed", "partial", "failed") and not task["work_still_running"]:
                        break
                    assert time.monotonic() < deadline, "Offline report did not complete"
                    time.sleep(.02)  # Terminal/readiness polling, not concurrency proof.
                assert task["status"] == "completed", task.get("error")
                assert client.get(base + f"/research-tasks/{task['task_id']}/export?version=1").status_code == 200
                db = api._CONFIG.paths.project_db(pid)
                manager = ArtifactManager(KnowledgeStore(db), ArtifactContentStore(api._CONFIG.paths.project_artifacts(pid)))
                artifact_v1 = manager.create_artifact(ArtifactType.METHOD_DOC, "文件中的版本产物", content="独立文件版本正文\n" * 1200)
                artifact = manager.new_version(artifact_v1.artifact_group, content="修订后的文件版本正文\n" * 1200)
                # Transport failure is directly retryable; invalid structure isn't.
                for failure in ("timeout", "structure"):
                    state["fault"] = failure
                    r = client.post(base + f"/conversations/{second}/messages", json={"request_id": "failed-" + failure, "content": "解释研究范围"})
                    assert r.json()["status"] == "failed"
                state["fault"] = None
                # An enclosing budget must survive restoration too, including
                # its failed reservation; a fresh session gives no allowance.
                budget_cid = expect(client.post(base + "/conversations", json={"title": "上层限制保留", "request_id": "upper-budget"}))["conversation"]["conversation_id"]
                from core.invocation_audit import AuditScope
                state["fault"] = "timeout"
                with AuditScope(db, pid, "backup-outer", group_id="backup-upper-" + pid, limits={"model": 1, "source": 2}).activate():
                    r = client.post(base + f"/conversations/{budget_cid}/messages", json={"request_id": "upper-exhausted", "content": "解释研究范围"})
                    assert not r.json()["failure"]["can_retry"] and r.json()["failure"]["model_attempts_remaining"] == 0
                state["fault"] = None
                # Deterministic lost storage at pre-execution: the accepted user
                # message survives, no worker runs, and the OS lease is released.
                with patch("core.workspace.conversations.threading.Thread.start", side_effect=RuntimeError("offline start fault")), \
                     patch.object(ConversationService, "_fail", side_effect=ConversationStorageError("offline storage fault")):
                    r = client.post(base + f"/conversations/{second}/messages", json={"request_id": "interrupted-chat", "content": "请修订当前计算研究方案", "plan_request": {"method": "computational", "supersedes_id": versions[-1]}, "wait_for_completion": False})
                    assert r.status_code == 503
                # Persist a queued search without executing it. Different
                # process identity makes it interrupted after restore/restart.
                from core.research_tasks.service import ResearchTaskService, SearchTaskRequest
                from core.resource_limits import RESOURCES, ACTOR
                c = ConversationService(pid, db)
                task_cid = expect(client.post(base + "/conversations", json={"title": "待恢复检索", "request_id": "third"}))["conversation"]["conversation_id"]
                def never_start(fn):
                    raise RuntimeError("offline runner start fault")
                deferred = ResearchTaskService(pid, db, c, runner=never_start)
                actor_token = ACTOR.set(ids[index])
                try:
                    with patch.object(ResearchTaskService, "PROCESS_IDENTITY", "offline-prior-process"), \
                         patch.object(deferred, "_update", side_effect=sqlite3.OperationalError("offline task storage fault")):
                        try:
                            deferred.start(task_cid, SearchTaskRequest(request_id="interrupted-search", topic="offline queued", content="检索离线资料"))
                        except sqlite3.OperationalError:
                            pass
                        else:
                            raise AssertionError("Missing controlled task failure")
                finally:
                    ACTOR.reset(actor_token)
                with closing(sqlite3.connect(db)) as conn:
                    queued_id = conn.execute("SELECT task_id FROM sra_research_tasks WHERE request_id='interrupted-search'").fetchone()[0]
                assert not RESOURCES.snapshot()["heavy"]
                result["users"].append({"project_id": pid, "conversation_id": cid, "second_conversation_id": second,
                    "paper_id": upload["paper_id"], "question_id": question["item_id"], "plan_ids": versions,
                    "draft_id": draft["suggestion_id"], "task_id": task["task_id"], "interrupted_task_id": queued_id, "task_conversation_id": task_cid,
                    "artifact_id": artifact.artifact_id, "artifact_v1_id": artifact_v1.artifact_id, "start_body": start_body, "budget_conversation_id": budget_cid,
                    "material_fingerprint": c.material_state()["fingerprint"]})
                downloads = {}
                for name, suffix in {
                    "attachment.pdf": f"/papers/{upload['paper_id']}/pdf",
                    "report-v1.md": f"/research-tasks/{task['task_id']}/export?version=1",
                    "plan-v1.md": f"/exports/items/{versions[0]}?format=md",
                    "plan-v2.md": f"/exports/items/{versions[1]}?format=md",
                    "artifact-v1.md": f"/exports/artifacts/{artifact_v1.artifact_id}?format=md",
                    "artifact-v2.md": f"/exports/artifacts/{artifact.artifact_id}?format=md",
                }.items():
                    response = client.get(base + suffix); assert response.status_code == 200
                    import hashlib
                    downloads[name] = {"url": base + suffix, "sha256": hashlib.sha256(response.content).hexdigest(), "size": len(response.content)}
                result["users"][-1]["downloads"] = downloads
            result["_old_cookies"] = [SecretStr(client.cookies.get("sra_session")) for client in (a, b)]
            result["offline_state"] = {k: v for k, v in state.items() if k != "fault"}
    return result
