"""Step 4A real FastAPI fixture: 153 temporary projects, no external models."""

import argparse
import json
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.conversation_home_test_server import build


def application(root):
    app = build(root)
    from web import api
    from core.knowledge import KnowledgeStore, ArtifactType
    from core.knowledge.schema import Artifact
    from core.workspace.conversations import ConversationService
    from core.workspace.conversation_schema import ConversationCreate
    from core.workspace.schema import Message

    original = list(api._PROJECTS)[0]
    extra = []
    template = Path(root) / "empty-project-template.db"
    KnowledgeStore(template)
    for i in range(151):
        pid = f"catalog_{i:03d}"
        db = api._CONFIG.paths.project_db(pid)
        db.parent.mkdir(parents=True)
        with (
            closing(sqlite3.connect(template)) as source,
            closing(sqlite3.connect(db)) as conn,
            conn,
        ):
            source.backup(conn)
            conn.executemany(
                "INSERT INTO kv_store(key,value,updated_at) VALUES (?,?,?)",
                [
                    (
                        key,
                        json.dumps(value, ensure_ascii=False),
                        f"2026-10-{(i%14)+1:02d}T10:00:00",
                    )
                    for key, value in {
                        "research.topic": f"分页项目 {i:03d} · 原主题",
                        "project.display_name": f"项目 {i:03d}",
                        "project.domain": "ai",
                        "project.created_at": "2026-09-01T00:00:00+00:00",
                    }.items()
                ],
            )
        api._PROJECTS[pid] = api.ProjectState(
            project_id=pid,
            topic=f"分页项目 {i:03d} · 原主题",
            display_name=f"项目 {i:03d}",
            domain="ai",
            created_at="2026-09-01",
        )
        extra.append(pid)
    api._PROJECTS[extra[-1]].status = "running"
    api._PROJECTS[extra[-2]].status = "pending_human"
    same = []
    for pid in [original, extra[0]]:
        c = ConversationService(pid, api._CONFIG.paths.project_db(pid))
        for i in range(5):
            cid = c.create(
                ConversationCreate(request_id=f"nav-{i}", title="同名研究讨论")
            )["conversation_id"]
            with c._connection(True) as conn:
                c._insert_message(
                    conn,
                    cid,
                    Message(
                        role="user",
                        content=f"仅属于项目 {pid} 的消息 {i}",
                        area="question",
                        mode="user",
                    ).model_dump(mode="json"),
                )
                conn.execute(
                    "UPDATE sra_conversations SET updated_at=? WHERE conversation_id=?",
                    (f"2026-10-20T00:{i+10:02d}:00+00:00", cid),
                )
            same.append({"pid": pid, "cid": cid})
    store = KnowledgeStore(api._CONFIG.paths.project_db(original))
    for i in range(1, 4):
        store.save_artifact(
            Artifact(
                artifact_id=f"method-v{i}",
                artifact_group="nav-method",
                version=i,
                artifact_type=ArtifactType.METHOD_DOC,
                title="已有方法方案",
                content=f"方法正文 v{i} · 待核对，软件夹具。\n\n$N={i}$",
                created_at=datetime(2026, 10, 17, 14, i),
            )
        )

    @app.get("/__navigation__/state")
    def nav_state():
        return {
            "project_id": original,
            "empty_project": extra[1],
            "other_project": extra[0],
            "same_titles": same,
            "protected": extra[-2:],
            "root": str(root),
            "pid": os.getpid(),
            "total": len(api._PROJECTS),
        }

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8014)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sra-project-nav-") as root:
        uvicorn.run(
            application(root), host="127.0.0.1", port=args.port, log_level="warning"
        )
