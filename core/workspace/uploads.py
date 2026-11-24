"""Small upload receipt in the existing project DB; existing paper storage reused."""

from contextlib import closing, contextmanager, ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from core.workspace import conversation_lock
from core.knowledge.store import KnowledgeStore


class UploadError(Exception):
    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


class PreparedUpload:
    def __init__(self):
        self.data = {"paper": None, "chunks": [], "kv": {}, "evidence": []}

    def save_paper(self, paper):
        self.data["paper"] = paper.model_dump(mode="json")

    def save_paper_chunks(self, chunks):
        self.data["chunks"].extend(c.model_dump(mode="json") for c in chunks)

    def save_kv(self, key, value):
        self.data["kv"][key] = value

    def log_evidence(self, value):
        self.data["evidence"].append(value)


class UploadService:
    def __init__(self, db, project_id):
        self.db, self.project_id = Path(db), project_id
        if not self.db.is_file():
            raise UploadError("项目数据库不存在，未创建替代数据库", 404)

    @staticmethod
    def validate(filename, raw):
        if filename != Path(filename).name or any(
            c in filename for c in ("/", "\\", ":")
        ):
            raise UploadError("文件名不能包含目录或路径，请重命名后上传", 400)
        suffix = Path(filename).suffix.lower()
        if suffix not in (".pdf", ".txt", ".md"):
            raise UploadError("不支持的文件类型", 400)
        if not raw:
            raise UploadError("文件为空，请选择包含正文的文件", 400)
        if suffix != ".pdf":
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise UploadError("文本不是有效 UTF-8，请转换编码后重试", 400) from exc
            if not text.strip():
                raise UploadError("文件没有可读取的正文", 400)

    @contextmanager
    def connection(self, write=False):
        if not self.db.is_file():
            raise UploadError("项目数据库不存在", 404)
        with closing(
            sqlite3.connect(
                self.db.resolve().as_uri() + "?mode=" + ("rw" if write else "ro"),
                uri=True,
                timeout=15,
            )
        ) as conn:
            conn.row_factory = sqlite3.Row
            if write:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        """CREATE TABLE IF NOT EXISTS sra_upload_requests (
                        request_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                        payload_hash TEXT NOT NULL, paper_id TEXT NOT NULL, status TEXT NOT NULL,
                        prepared TEXT, response TEXT, error TEXT, attempt INTEGER NOT NULL)"""
                    )
                    yield conn
            else:
                yield conn

    def status(self, rid):
        with self.connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='sra_upload_requests'"
            ).fetchone():
                raise UploadError("上传请求不存在", 404)
            row = conn.execute(
                "SELECT * FROM sra_upload_requests WHERE request_id=? AND project_id=?",
                (rid, self.project_id),
            ).fetchone()
            if row is None:
                raise UploadError("上传请求不存在或不属于当前项目", 404)
            status = row["status"]
            if status == "processing" and not conversation_lock.is_held(
                self.db, "upload:" + rid
            ):
                status = "interrupted"
            response = json.loads(row["response"]) if row["response"] else {}
            return {
                **response,
                "request_id": rid,
                "status": status,
                "error": row["error"],
                "attempt": row["attempt"],
            }

    def _publish(self, conn, prepared):
        from core.knowledge.schema import Paper, PaperChunk

        class TransactionStore(KnowledgeStore):
            @contextmanager
            def _connect(self):
                yield conn

        # No schema creation or commits inside this adapter. Reuse all existing
        # entity serialization in one outer transaction including the receipt.
        writer = TransactionStore(self.db, read_only=True)
        writer.save_paper(Paper.model_validate(prepared["paper"]))
        writer.save_paper_chunks(
            [PaperChunk.model_validate(c) for c in prepared["chunks"]]
        )
        for key, value in prepared["kv"].items():
            writer.save_kv(key, value)
        for value in prepared["evidence"]:
            writer.log_evidence(value)

    def process(self, rid, filename, raw, title, prepare):
        self.validate(filename, raw)
        if not rid or len(rid) > 128 or not rid.strip():
            raise UploadError("上传请求编号无效", 422)
        signature = sha256(
            json.dumps(
                [filename, title, sha256(raw).hexdigest()], ensure_ascii=False
            ).encode()
        ).hexdigest()
        with ExitStack() as owner:
            duplicate = False
            with self.connection(True) as conn:
                prior = conn.execute(
                    "SELECT * FROM sra_upload_requests WHERE request_id=?", (rid,)
                ).fetchone()
                if prior and (
                    prior["project_id"] != self.project_id
                    or prior["payload_hash"] != signature
                ):
                    raise UploadError("同一上传 request_id 已用于不同文件或标题", 409)
                if prior and prior["status"] == "completed":
                    duplicate = True
                else:
                    acquired = owner.enter_context(
                        conversation_lock.hold(self.db, "upload:" + rid)
                    )
                    if not acquired:
                        duplicate = True
                    else:
                        from core.resource_limits import RESOURCES
                        owner.enter_context(RESOURCES.acquire())
                    if acquired and prior:
                        conn.execute(
                            "UPDATE sra_upload_requests SET status='processing',error=NULL,attempt=attempt+1 WHERE request_id=?",
                            (rid,),
                        )
                    elif acquired:
                        conn.execute(
                            "INSERT INTO sra_upload_requests VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                rid,
                                self.project_id,
                                signature,
                                "paper_" + uuid4().hex[:12],
                                "processing",
                                None,
                                None,
                                None,
                                1,
                            ),
                        )
            if duplicate:
                result = self.status(rid)
                result["already_uploaded"] = result["status"] == "completed"
                return result
            try:
                KnowledgeStore(
                    self.db
                )  # Explicit write only; initialize legacy paper schema if needed.
                with self.connection() as conn:
                    row = conn.execute(
                        "SELECT * FROM sra_upload_requests WHERE request_id=?", (rid,)
                    ).fetchone()
                if row["prepared"]:
                    saved = json.loads(row["prepared"])
                else:
                    captured = PreparedUpload()
                    response = prepare(captured, row["paper_id"])
                    saved = {"data": captured.data, "response": response}
                    with self.connection(True) as conn:
                        conn.execute(
                            "UPDATE sra_upload_requests SET prepared=? WHERE request_id=?",
                            (json.dumps(saved, ensure_ascii=False), rid),
                        )
                response = {
                    **saved["response"],
                    "status": "completed",
                    "request_id": rid,
                    "already_uploaded": False,
                }
                with self.connection(True) as conn:
                    self._publish(conn, saved["data"])
                    conn.execute(
                        "UPDATE sra_upload_requests SET status='completed',response=?,error=NULL WHERE request_id=?",
                        (json.dumps(response, ensure_ascii=False), rid),
                    )
                return response
            except Exception as exc:
                with self.connection(True) as conn:
                    conn.execute(
                        "UPDATE sra_upload_requests SET status='failed',error=? WHERE request_id=?",
                        (
                            "上传尚未完成；已保存准备数据可在明确重试时复用 ["
                            + type(exc).__name__
                            + "]",
                            rid,
                        ),
                    )
                if isinstance(exc, UploadError):
                    raise
                raise UploadError(
                    "上传尚未完成，原请求已保留；请核对状态后用同一文件与编号重试", 503
                ) from exc
