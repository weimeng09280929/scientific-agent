"""OpenAI 兼容 Provider。

支持：
- OpenAI 官方 API（GPT 系列）
- 兼容 OpenAI 协议的本地部署模型（vLLM, Ollama, LM Studio 等）

结构化输出实现：
- 优先使用 response_format=json_schema（GPT-4o 及兼容服务支持）
- 退化为 response_format=json_object + prompt 引导
- 最终用 Pydantic 校验，失败则抛 LLMError
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from core.llm.base import (
    EmbeddingRequest,
    EmbeddingResponse,
    LLMError,
    LLMTruncatedError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    StructuredOutputRequest,
    strip_think_tags,
)

T = TypeVar("T", bound=BaseModel)


class OpenAIProvider(LLMProvider):
    """OpenAI 兼容 Provider。"""

    provider_name = "openai"
    audited_transport = True

    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str] = None,
        timeout: int = 120,
        max_retries: int = 3,
        provider_name: str = "openai",
        http_client=None,
    ):
        try:
            from openai import OpenAI, DefaultHttpxClient
        except ImportError as e:
            raise ImportError("未安装 openai 包，请运行: pip install openai") from e

        # Disable SDK-internal retries. Each network attempt passes the actual
        # SDK client's hooks, including failures; no httpx-version assumptions.
        self._retries = max(0, min(3, max_retries))
        self._pending = ContextVar("sra_model_attempts_" + str(id(self)), default=None)
        client = http_client or DefaultHttpxClient(follow_redirects=False)
        client.follow_redirects = False
        client.event_hooks["request"].append(self._before_http)
        client.event_hooks["response"].append(self._after_http)
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "max_retries": 0,
            "http_client": client,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._base_url = base_url
        self.provider_name = provider_name

    def _before_http(self, request):
        from core.invocation_audit import ACTIVE

        scope = ACTIVE.get()
        if not scope:
            return
        payload = json.loads(request.content)
        reserved = None
        if scope.pricing:
            # UTF-8 bytes is a deliberately conservative input token reservation,
            # not a reported tokenizer usage. Output includes reasoning budget.
            reserved = (
                len(request.content) * scope.pricing["input"]
                + payload.get("max_tokens", 0) * scope.pricing["output"]
            ) / 1_000_000
        ticket = scope.begin(
            "network", "model", payload.get("model", "unknown"), reserved_cost=reserved
        )
        pending = self._pending.get()
        if pending is not None:
            pending.append((id(request), scope, ticket))

    def _after_http(self, response):
        from core.tools.search_result import retry_delay

        for identity, scope, ticket in self._pending.get() or []:
            if identity != id(response.request):
                continue
            response.read()
            usage = None
            if response.status_code == 200:
                try:
                    usage = response.json().get("usage")
                except (ValueError, AttributeError):
                    pass
            scope.finish(
                ticket,
                outcome="success" if response.status_code < 300 else "http_error",
                http_status=response.status_code,
                usage=usage,
                retry_after=retry_delay(response.headers.get("Retry-After")),
            )

    def _request(self, fn, **kwargs):
        from core.invocation_audit import CallLimitError
        from core.tools.search_result import retry_delay
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        token = self._pending.set([])
        try:
            for attempt in range(self._retries + 1):
                try:
                    return fn(**kwargs)
                except Exception as exc:
                    for _, scope, ticket in self._pending.get():
                        scope.finish(ticket, outcome=type(exc).__name__)
                    cause = exc
                    while cause:
                        if isinstance(cause, CallLimitError):
                            raise cause
                        cause = cause.__cause__
                    code = getattr(exc, "status_code", None)
                    retryable = (
                        isinstance(exc, (APIConnectionError, APITimeoutError))
                        or isinstance(exc, APIStatusError)
                        and (code in (408, 409, 429) or code >= 500)
                    )
                    response = getattr(exc, "response", None)
                    delay = (
                        retry_delay(response.headers.get("Retry-After"))
                        if response is not None
                        else None
                    )
                    # Do not sleep for long server cooldowns or retry tightly.
                    from core.invocation_audit import ACTIVE

                    if (
                        (ACTIVE.get() and ACTIVE.get().stop_on_model_failure)
                        or not retryable
                        or attempt >= self._retries
                        or delay is not None
                        and delay > 2
                    ):
                        raise
                    time.sleep(max(delay or 0, min(2, 2**attempt)))
        finally:
            self._pending.reset(token)

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        messages = self._build_messages(request)
        try:
            resp = self._request(
                self._client.chat.completions.create,
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                stop=request.stop,
            )
        except Exception as e:
            raise LLMError(
                f"模型请求失败 [{type(e).__name__}]，请查看原请求状态后明确重试"
            ) from e

        text = resp.choices[0].message.content or ""
        # 剥离推理模型（MiniMax-M3 / glm-5.3-flash 等）拼进 content 的 <think> 思考链，只留最终答复
        text = strip_think_tags(text)
        usage = resp.usage
        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider_name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )

    def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        try:
            resp = self._request(
                self._client.embeddings.create,
                model=model,
                input=request.texts,
            )
        except Exception as e:
            raise LLMError(f"OpenAI embed 调用失败: {e}") from e

        embeddings = [d.embedding for d in resp.data]
        usage = resp.usage
        return EmbeddingResponse(
            embeddings=embeddings,
            model=model,
            provider=self.provider_name,
            total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
        )

    def structured_output(
        self, request: StructuredOutputRequest, model: str
    ) -> BaseModel:
        schema = request.output_schema
        schema_json = schema.model_json_schema()

        messages = self._build_messages_for_structured(request, schema)

        try:
            resp = self._request(
                self._client.chat.completions.create,
                model=model,
                messages=messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            raise LLMError(
                f"方案模型请求失败 [{type(e).__name__}]，原消息已保留，请核对状态后明确重试"
            ) from e

        from core.invocation_audit import span
        with span("phase", "model", "structure_validation"):
            return self._parse_structured(resp, request)

    def _parse_structured(self, resp, request):
        schema = request.output_schema
        if resp.choices[0].finish_reason == "length":
            raise LLMTruncatedError(
                f"模型结构化输出被截断（预算 {request.max_tokens} token，含推理消耗）"
            )
        text = strip_think_tags(resp.choices[0].message.content or "")
        # 提取 JSON：处理 markdown 代码块包裹、前置说明文字等情况
        text = self._extract_json(text)

        # 检测 MiniMax 把 schema 定义本身当返回的常见错误模式：
        # 返回 {"$defs": ...} / {"$schema": ...} / {"properties": ..., "type": "object"}
        # 这种情况下重新提示 LLM 生成实例数据
        if self._looks_like_schema(text):
            raise LLMError(
                "模型返回了 schema 定义本身而非实例数据，请改用 complete 或调整 prompt"
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(
                f"结构化输出返回非合法 JSON: {e}\n原始返回:\n{text[:500]}"
            ) from e

        try:
            return schema.model_validate(data)
        except ValidationError as e:
            raise LLMError(
                f"结构化输出不符合 schema: {e}\n原始返回:\n{text[:500]}"
            ) from e

    @staticmethod
    def _looks_like_schema(text: str) -> bool:
        """检测返回是否是 schema 定义而非实例数据。

        MiniMax M3 偶尔会把输入的 JSON Schema 原样返回，常见特征：
        - 含 "$defs" / "$schema" / "$id" 顶层键
        - 含 "properties" + "type": "object" 且无实际业务字段
        """
        if not text:
            return False
        stripped = text.strip()
        if not stripped.startswith("{"):
            return False
        # 顶层 schema 标志键
        for key in ('"$defs"', '"$schema"', '"$id"', '"definitions"'):
            if key in stripped[:200]:
                return True
        return False

    @staticmethod
    def _extract_json(text: str) -> str:
        """从 LLM 返回中提取 JSON 字符串。

        处理以下情况：
        1. 纯 JSON（直接返回）
        2. markdown 代码块包裹（```json ... ``` 或 ``` ... ```）
        3. 前置/后置说明文字 + JSON（提取第一个 { 到最后一个 } 之间的内容）
        """
        stripped = text.strip()

        # 情况 1：纯 JSON
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        # 情况 2：markdown 代码块包裹
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 2:
                body_lines = lines[1:]
                if body_lines and body_lines[-1].strip().startswith("```"):
                    body_lines = body_lines[:-1]
                inner = "\n".join(body_lines).strip()
                if inner.startswith("{") and inner.endswith("}"):
                    return inner

        # 情况 3：从文本中提取第一个 { 到最后一个 } 之间的内容
        first_brace = stripped.find("{")
        last_brace = stripped.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            return stripped[first_brace : last_brace + 1]

        # 兜底：返回原文让 json.loads 报错
        return stripped

    def _build_messages_for_structured(
        self,
        request: StructuredOutputRequest,
        schema: Type[BaseModel],
    ) -> list[dict[str, str]]:
        """构造结构化输出的 messages。

        把 JSON Schema 注入 system prompt，要求返回符合 schema 的 JSON 实例。
        关键：明确告诉模型「生成实例数据」而非「复述 schema 定义」，
        避免 MiniMax M3 把 $defs/properties 当返回的常见错误模式。
        """
        schema_str = json.dumps(
            schema.model_json_schema(), ensure_ascii=False, indent=2
        )
        # 用 schema 的字段名作为强提示，告诉模型要填什么
        field_hints: list[str] = []
        for name, info in schema.model_json_schema().get("properties", {}).items():
            desc = info.get("description", "")
            field_hints.append(f'- "{name}": {desc}')
        hints_text = "\n".join(field_hints) if field_hints else ""

        system = (request.system or "") + (
            "\n\n请基于用户请求生成**实例数据**（不是 schema 定义本身），"
            "返回一个符合以下 JSON Schema 的 JSON 对象。\n"
            "硬性要求：\n"
            "1. 仅返回 JSON 对象本身，不要包裹 markdown 代码块，不要任何解释文字\n"
            "2. 不要返回 $defs / $schema / properties / type 等 schema 元字段\n"
            "3. 必须为所有 required 字段填入真实业务数据（字符串/数字/数组等）\n"
            f"\n需要填充的字段：\n{hints_text}\n"
            f"\n参考 JSON Schema：\n{schema_str}"
        )

        if request.messages is not None:
            msgs = list(request.messages)
        else:
            msgs = [{"role": "user", "content": request.prompt or ""}]

        return [{"role": "system", "content": system}] + msgs
