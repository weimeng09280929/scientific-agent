"""Isolated real app for browser acceptance. ALL state is temporary; models offline."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SRA_WEB_SKIP_GUARD"] = "1"
os.environ["SRA_WEB_SKIP_PROJECT_SCAN"] = "1"
os.environ["SRA_DRY_RUN"] = "true"


def build(temporary):
    import core.config as configuration

    configuration._default_config = configuration.GlobalConfig(
        paths=configuration.ProjectPaths.from_root(temporary), dry_run=False
    )
    from fastapi import FastAPI
    from web import api, workspace_api
    from core.knowledge.schema import (
        Paper,
        PaperChunk,
        Experiment,
        ExperimentStatus,
        ArtifactType,
    )
    from core.workspace.schema import (
        ItemInput,
        Message,
        Suggestion,
        GuideDraft,
        SuggestedItem,
    )
    from core.workspace.service import WorkspaceService, KEY
    from core.workspace.conversations import ConversationService
    from core.workspace.conversation_schema import ConversationCreate, ConversationSend
    from core.artifacts import ArtifactManager, ArtifactContentStore

    calls = []
    failures = set()
    # Count release permits: the browser can navigate/release after optimistic
    # "processing" but before the offline generator has entered its wait. An
    # Event.clear() here would lose that valid early signal and sleep 40 seconds.
    gate = threading.Semaphore(0)

    def generate(registry, area, domain, text, context, purpose):
        calls.append(
            {"text": text, "history": context["history"], "records": context["records"]}
        )
        if "OFFLINE_FAIL" in text and text not in failures:
            failures.add(text)
            raise RuntimeError("Offline injected failure")
        if "OFFLINE_WAIT" in text:
            gate.acquire(timeout=40)
        return GuideDraft(
            reply="离线验收回复（不是真实科研证据）："
            + text
            + "\n\n本轮止于研究方案。示例公式：$N=2$。",
            suggestions=[
                SuggestedItem(
                    kind="note",
                    title="待核对草案：" + text[:24],
                    content="仅供软件验收，须由用户确认。",
                )
            ],
        )

    workspace_api.generate_guidance = generate
    from core.llm import LLMRegistry

    LLMRegistry.from_config = classmethod(lambda cls, cfg: object())
    data = api.create_project(
        api.CreateProjectRequest(topic="已有项目 · 联邦学习软件夹具", domain="ai")
    )
    pid = data["project_id"]
    db = configuration._default_config.paths.project_db(pid)
    s = WorkspaceService(pid, db)
    s.store.save_paper(
        Paper(
            paper_id="fixture-paper",
            title="Offline Federated Learning Fixture",
            abstract="Software fixture, not a verified publication",
        )
    )
    s.store.save_paper_chunks(
        [
            PaperChunk(
                chunk_id="fixture-chunk",
                paper_id="fixture-paper",
                chunk_index=0,
                text="Original fixture excerpt",
                page=1,
            )
        ]
    )
    q = s.add_item(
        ItemInput(
            kind="question",
            title="已有研究问题",
            content="离线夹具，尚未验证。",
            paper_ids=["fixture-paper"],
        ),
        0,
    ).items[-1]
    s.add_item(
        ItemInput(
            kind="plan",
            title="已有研究方案",
            content="明确比较条件与指标；尚未执行。",
            related_ids=[q.item_id],
        ),
        1,
    )
    w = s.load(include_chat=False)
    w.messages = [
        Message(
            message_id="legacy-user",
            role="user",
            content="历史对话，保留原有顺序与引用",
            area="question",
            mode="user",
        ),
        Message(
            message_id="legacy-reply",
            role="assistant",
            content="历史科研回复，不是已验证结论。",
            area="question",
            mode="model",
            cited_paper_ids=["fixture-paper"],
            suggestion_ids=["legacy-draft"],
        ),
    ]
    w.suggestions = [
        Suggestion(
            suggestion_id="legacy-draft",
            based_on_revision=w.revision,
            title="旧草案缺少依据快照",
            content="不能绕过复核采纳。",
        )
    ]
    s.store.save_kv(KEY, w.model_dump(mode="json"))
    c = ConversationService(pid, db)
    long = c.create(
        ConversationCreate(request_id="long-fixture", title="长历史分页验收")
    )["conversation_id"]
    with c._connection(True) as conn:
        for i in range(77):
            c._insert_message(
                conn,
                long,
                Message(
                    message_id=f"history-{i}",
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"历史页 {i}：这是用于检查分页顺序与滚动位置的软件夹具。\n\n"
                    + "保留研究上下文，不代表验证了科学结论。" * 8,
                    area="question",
                    mode="user" if i % 2 == 0 else "model",
                ).model_dump(mode="json"),
            )
    orphan = c.create(ConversationCreate(request_id="orphan", title="中断恢复验收"))[
        "conversation_id"
    ]

    def crash(*args):
        raise SystemExit("offline fixture interruption")

    try:
        c.send(
            orphan,
            ConversationSend(content="中断的软件请求", request_id="orphan-request"),
            "offline",
            crash,
        )
    except SystemExit:
        pass
    s.store.save_experiment(
        Experiment(
            experiment_id="historical-exp",
            name="已有实验记录（离线夹具）",
            status=ExperimentStatus.COMPLETED,
            metrics={"fixture_metric": 0.1},
            result_summary="历史软件验收记录，不是真实实验。",
        )
    )
    ArtifactManager(
        s.store,
        ArtifactContentStore(
            configuration._default_config.paths.project_artifacts(pid)
        ),
    ).create_artifact(
        ArtifactType.PAPER_DRAFT,
        title="已有历史稿件",
        content="历史稿件正文，仅用于读取和导出测试。",
    )
    missing = api.create_project(
        api.CreateProjectRequest(topic="数据库缺失夹具", domain="ai")
    )["project_id"]
    missing_db = configuration._default_config.paths.project_db(missing)
    assert missing_db.resolve().is_relative_to(Path(temporary).resolve())
    missing_db.unlink()

    @api.app.get("/__fixture__/state")
    def fixture_state():
        return {
            "project_id": pid,
            "long_conversation": long,
            "orphan_conversation": orphan,
            "missing_project": missing,
            "pid": os.getpid(),
            "calls": calls,
            "root": str(temporary),
        }

    @api.app.post("/__fixture__/release")
    def release():
        gate.release()
        return {"released": True}

    @api.app.post("/__fixture__/excerpt")
    def excerpt():
        s.store.save_paper_chunks(
            [
                PaperChunk(
                    chunk_id="fixture-chunk",
                    paper_id="fixture-paper",
                    chunk_index=0,
                    text="Corrected fixture excerpt",
                    page=3,
                )
            ]
        )
        return {"changed": True}

    old = FastAPI()

    @old.get("/api/projects")
    def old_projects():
        return {"projects": []}

    api.app.mount("/legacy", old)
    return api.app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sra-conversation-ui-") as temporary:
        uvicorn.run(
            build(temporary), host="127.0.0.1", port=args.port, log_level="warning"
        )
