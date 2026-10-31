"""Bound multipart before spooling; no auto FastAPI form/body buffering."""
import asyncio
from contextlib import asynccontextmanager
from starlette.formparsers import MultiPartParser, MultiPartException
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import UploadFile
from fastapi import HTTPException
from core.resource_limits import RESOURCES


class RemoteFileTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, "远端文件超过本地接收大小限制，未保存文件；请获取较小文件后再导入。")


def read_remote_bounded(response):
    """The legacy PDF proxy is a file ingress too; never use response.content."""
    raw = bytearray()
    try:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=RESOURCES.limits.chunk_bytes):
            if len(raw) + len(chunk) > RESOURCES.limits.file_bytes:
                raise RemoteFileTooLarge()
            raw.extend(chunk)
        return bytes(raw)
    finally:
        response.close()


class BoundedMultipart(MultiPartParser):
    def __init__(self, request):
        limits = RESOURCES.limits
        self.limits = limits
        self.received = self.file_size = self.header_size = 0
        self.ended = False
        self.spool_max_size = limits.spool_bytes
        if len(request.headers.get("content-type", "").encode()) > limits.header_bytes:
            raise HTTPException(413, "上传请求头过长，请重新选择文件后重试")
        super().__init__(request.headers, self.bounded_stream(request), max_files=1,
                         max_fields=2, max_part_size=limits.field_bytes)

    async def bounded_stream(self, request):
        # Content-Length is just an early rejection; every actual byte is counted.
        size = request.headers.get("content-length")
        if size:
            try:
                if int(size) < 0:
                    raise ValueError()
                if int(size) > self.limits.body_bytes:
                    raise HTTPException(413, "上传请求超过大小限制，请缩小文件后重新选择。")
            except ValueError:
                raise HTTPException(400, "上传请求长度无效") from None
        async for chunk in request.stream():
            self.received += len(chunk)
            if self.received > self.limits.body_bytes:
                raise HTTPException(413, "上传请求超过大小限制，请缩小文件后重新选择。")
            # Bound each parser callback's pending data, including ASGI servers
            # which supply a larger input chunk.
            for start in range(0, len(chunk), self.limits.chunk_bytes):
                yield chunk[start:start + self.limits.chunk_bytes]

    def on_part_begin(self):
        super().on_part_begin()
        self.file_size = self.header_size = 0

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_size += end - start
            if self.file_size > self.limits.file_bytes:
                raise HTTPException(413, "文件超过大小限制，请缩小文件后重新选择。")
        super().on_part_data(data, start, end)

    def _header(self, size):
        self.header_size += size
        if self.header_size > self.limits.header_bytes:
            raise HTTPException(413, "文件名或上传头信息过长，请简化后重新选择。")

    def on_header_field(self, data, start, end):
        self._header(end-start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data, start, end):
        self._header(end-start)
        super().on_header_value(data, start, end)

    def on_end(self):
        self.ended = True
        super().on_end()

    async def parse(self):
        try:
            result = await asyncio.wait_for(super().parse(), timeout=self.limits.receive_seconds)
            if not self.ended:
                raise HTTPException(400, "上传未完整接收，请重新选择文件；可先核对原上传回执。")
            return result
        except (TimeoutError, asyncio.TimeoutError):
            raise HTTPException(408, "上传接收超时，请重新选择文件后手动重试。") from None
        except MultiPartException as exc:
            if str(exc).startswith(("Too many files", "Too many fields", "Part exceeded")):
                raise HTTPException(413, "上传字段或文件数量／大小超过限制（每次 1 个文件、最多 2 个文本字段）。") from None
            raise HTTPException(400, "上传格式不完整，请重新选择文件；可先核对原上传回执。") from None
        except MultipartParseError:
            raise HTTPException(400, "上传格式无法解析，请重新选择文件；可先核对原上传回执。") from None


@asynccontextmanager
async def receive_upload(request):
    with RESOURCES.acquire("receive"):
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise HTTPException(415, "请选择文件并通过上传表单提交")
        parser = BoundedMultipart(request)
        try:
            form = await parser.parse()
            keys = [k for k, _ in form.multi_items()]
            if set(keys) - {"file", "title", "request_id"} or len(keys) != len(set(keys)):
                raise HTTPException(422, "上传字段无效或重复")
            file = form.get("file")
            if not isinstance(file, UploadFile) or any(isinstance(form.get(k), UploadFile) for k in ("title", "request_id")):
                raise HTTPException(422, "请选择一个文件")
            yield form, file
        finally:
            # Only parser-owned anonymous temporary handles. No directory scan,
            # no historical project cleanup, no named temp file on process death.
            for handle in parser._files_to_close_on_error:
                handle.close()


async def read_bounded(file):
    raw = bytearray()
    while True:
        chunk = await file.read(RESOURCES.limits.chunk_bytes)
        if not chunk:
            break
        if len(raw) + len(chunk) > RESOURCES.limits.file_bytes:
            raise HTTPException(413, "文件超过大小限制，请缩小后重新选择。")
        raw.extend(chunk)
    return bytes(raw)
