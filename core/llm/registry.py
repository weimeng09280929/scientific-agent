"""LLM Provider 注册表与统一调用入口。

使用范式：
    registry = LLMRegistry.from_config(get_config())
    # 按 task_type 调用
    resp = registry.complete("paper_metadata_extract", prompt="...")
    obj = registry.structured_output("design_claim_extract", output_schema=..., prompt="...")
    emb = registry.embed(["text1", "text2"])

注册表负责：
- 按 task_type 路由到 provider+model
- 应用 task 配置中的 temperature/max_tokens
- 复用 provider 实例（避免重复初始化客户端）
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from core.config import GlobalConfig, get_config
from core.llm.base import (
    EmbeddingRequest,
    EmbeddingResponse,
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    StructuredOutputRequest,
)
from core.llm.task_router import TaskRouter
from core.llm.public_errors import public_error
from core.invocation_audit import audited_model, span

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class LLMRegistry:
    """LLM 统一调用入口。

    内部维护 provider 实例池（按 provider 名缓存）。
    """

    def __init__(
        self,
        router: TaskRouter,
        providers: dict[str, LLMProvider],
    ):
        self._router = router
        self._providers = providers

    @classmethod
    def from_config(cls, config: Optional[GlobalConfig] = None) -> "LLMRegistry":
        """从全局配置构造。

        懒加载 provider：先注入可用的 provider，缺失凭据的 provider 不注册。
        """
        config = config or get_config()
        router = TaskRouter.load(config.llm.tasks_config_path)

        providers: dict[str, LLMProvider] = {}

        # 官方 OpenAI 与本地兼容服务分别注册，不能因本地地址而初始化官方客户端。
        openai_key = os.environ.get(config.llm.openai_api_key_env)
        local_url = os.environ.get(config.llm.local_model_base_url_env)
        if openai_key:
            from core.llm.providers.openai_provider import OpenAIProvider

            providers["openai"] = OpenAIProvider(
                api_key=openai_key,
                base_url=None,  # OpenAI 官方
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
            )

        # local provider（兼容 OpenAI 协议的本地服务）
        if local_url:
            from core.llm.providers.openai_provider import OpenAIProvider

            providers["local"] = OpenAIProvider(
                api_key=openai_key or "EMPTY",
                base_url=local_url,
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
            )

        # Anthropic
        anthropic_key = os.environ.get(config.llm.anthropic_api_key_env)
        if anthropic_key:
            from core.llm.providers.anthropic_provider import AnthropicProvider

            providers["anthropic"] = AnthropicProvider(
                api_key=anthropic_key,
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
            )

        # ===== MiniMax / MiMo / DeepSeek =====
        # 三家均兼容 OpenAI 协议，复用 OpenAIProvider，通过 base_url + api_key 区分。
        # 只有 api_key 存在才注册（dry_run 模式下无 key 则跳过，节点用占位数据不依赖 provider）。
        from core.llm.providers.openai_provider import OpenAIProvider as _OAIProvider

        # MiniMax Token Plan（兼容 OpenAI 协议，用 sk-cp key）
        minimax_key = os.environ.get(config.llm.minimax_api_key_env)
        minimax_url = os.environ.get(config.llm.minimax_base_url_env) or config.llm.minimax_base_url_default
        if minimax_key:
            providers["minimax"] = _OAIProvider(
                api_key=minimax_key,
                base_url=minimax_url,
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
            )

        # 小米 MiMo（兼容 OpenAI 协议）
        mimo_key = os.environ.get(config.llm.mimo_api_key_env)
        mimo_url = os.environ.get(config.llm.mimo_base_url_env) or config.llm.mimo_base_url_default
        if mimo_key:
            providers["mimo"] = _OAIProvider(
                api_key=mimo_key,
                base_url=mimo_url,
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
            )

        # DeepSeek（兼容 OpenAI 协议）
        deepseek_key = os.environ.get(config.llm.deepseek_api_key_env)
        deepseek_url = os.environ.get(config.llm.deepseek_base_url_env) or config.llm.deepseek_base_url_default
        if deepseek_key:
            providers["deepseek"] = _OAIProvider(
                api_key=deepseek_key,
                base_url=deepseek_url,
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
            )

        spark_key = os.environ.get(config.llm.spark_api_key_env)
        spark_url = os.environ.get(config.llm.spark_base_url_env) or config.llm.spark_base_url_default
        if spark_key:
            providers["spark"] = _OAIProvider(
                api_key=spark_key,
                base_url=spark_url,
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
                provider_name="spark",
            )

        # 硅基流动（OpenAI 兼容协议）
        siliconflow_key = os.environ.get(config.llm.siliconflow_api_key_env)
        siliconflow_url = (
            os.environ.get(config.llm.siliconflow_base_url_env)
            or config.llm.siliconflow_base_url_default
        )
        if siliconflow_key:
            providers["siliconflow"] = _OAIProvider(
                api_key=siliconflow_key,
                base_url=siliconflow_url,
                timeout=config.llm.default_timeout_seconds,
                max_retries=config.llm.max_retries,
                provider_name="siliconflow",
            )

        return cls(router=router, providers=providers)

    # ===== 统一调用接口 =====

    @audited_model
    def complete(
        self,
        task_type: str,
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, str]]] = None,
        system: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
    ) -> LLMResponse:
        """按 task_type 调用 LLM 补全/对话。

        含升级重试：推理模型（deepseek-v4.1-flash 等）经部分网关调用时，
        reasoning token 也计入 max_tokens 预算——长 prompt 下推理会耗光全部
        预算（finish_reason=length 且 content 为空）。检测到该情况时自动
        放大 max_tokens 重试一次。
        """
        cfg = self._router.get(task_type)
        provider = self._get_provider(cfg.provider)

        def _build_request(max_tokens: int) -> LLMRequest:
            return LLMRequest(
                prompt=prompt,
                messages=messages,
                system=system,
                temperature=temperature_override if temperature_override is not None else cfg.temperature,
                max_tokens=max_tokens,
            )

        base_max_tokens = max_tokens_override or cfg.max_tokens
        resp = provider.complete(_build_request(base_max_tokens), model=cfg.model)

        raw = resp.raw or {}
        finish_reason = ((raw.get("choices") or [{}])[0]).get("finish_reason")
        if finish_reason == "length":
            from core.llm.base import LLMTruncatedError
            from core.invocation_audit import ACTIVE
            if ACTIVE.get() and ACTIVE.get().stop_on_model_failure:
                raise LLMTruncatedError("本次验收输出截断，按停止条件不追加调用")
            bumped = min(max(base_max_tokens * 2, 8192), 32768)
            if bumped <= base_max_tokens:
                raise LLMTruncatedError("模型正文输出仍被截断，未采用不完整内容")
            logger.warning("complete(%s): 输出截断，预算 %d → %d，仅重试一次",task_type,base_max_tokens,bumped)
            retry_resp = provider.complete(_build_request(bumped), model=cfg.model)
            retry_finish = (((retry_resp.raw or {}).get("choices") or [{}])[0]).get("finish_reason")
            if retry_finish == "length":
                raise LLMTruncatedError("模型正文输出重试后仍被截断，未采用不完整内容")
            return retry_resp
        return resp

    @audited_model
    def structured_output(
        self,
        task_type: str,
        output_schema: Type[T],
        prompt: Optional[str] = None,
        messages: Optional[list[dict[str, str]]] = None,
        system: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
    ) -> T:
        """按 task_type 调用 LLM 结构化输出。返回 Pydantic 实例。

        含一次重试：若首次返回 schema 定义本身（MiniMax M3 常见错误模式），
        自动追加「请返回实例数据，不要返回 schema 定义」强提示重试一次。
        """
        cfg = self._router.get(task_type)
        provider = self._get_provider(cfg.provider)
        token_budget = cfg.max_tokens if max_tokens_override is None else max_tokens_override
        if not 256 <= token_budget <= 32768:
            raise ValueError("结构化输出预算须在256到32768之间")
        request = StructuredOutputRequest(
            output_schema=output_schema,
            prompt=prompt,
            messages=messages,
            system=system,
            temperature=temperature_override if temperature_override is not None else cfg.temperature,
            max_tokens=token_budget,
        )
        try:
            result = provider.structured_output(request, model=cfg.model)
            return self._coerce(result, output_schema)
        except LLMError as e:
            from core.llm.base import LLMTruncatedError
            from core.invocation_audit import ACTIVE
            if ACTIVE.get() and ACTIVE.get().stop_on_model_failure:
                raise
            if isinstance(e, LLMTruncatedError):
                # 推理和最终 JSON 共享预算。最多额外请求一次，且不超过 32768。
                bumped = min(max(token_budget * 2, 8192), 32768)
                if bumped <= token_budget:
                    raise
                logger.warning("structured_output(%s) 被截断，预算 %d → %d，重试一次", task_type, token_budget, bumped)
                request.max_tokens = bumped
                with span("phase", "model", "model_repair:truncated_output"):
                    return self._coerce(provider.structured_output(request, model=cfg.model), output_schema)
            err_msg = str(e)
            # 「返回 schema 定义」「不符合 schema」「返回空/非 JSON」（网关偶发抽风
            # 返回空内容，如 tokendance 中转的 deepseek）均值得重试一次；
            # 其他错误（如网络异常、鉴权失败）直接抛
            retryable = (
                "schema 定义本身" in err_msg
                or "不符合 schema" in err_msg
                or "非合法 JSON" in err_msg
            )
            if not retryable:
                raise
            logger.warning(
                "structured_output 首次失败（%s），追加实例提示重试一次",
                public_error(e),
            )
            # 在 prompt 末尾追加强提示
            reminder = (
                "\n\n【重要】请直接返回符合 schema 的实例 JSON 数据，"
                "不要返回 $defs / properties / type 等 schema 元字段。"
                "例如若 schema 要求 {ideas: list[IdeaDraftItem]}，"
                "应返回 {\"ideas\": [{\"text\": \"具体思路描述\", ...}]}。"
            )
            if prompt is not None:
                request = StructuredOutputRequest(
                    output_schema=output_schema,
                    prompt=prompt + reminder,
                    system=system,
                    temperature=request.temperature,
                    max_tokens=token_budget,
                )
            elif messages is not None:
                msgs = list(messages)
                if msgs and msgs[-1].get("role") == "user":
                    msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] + reminder}
                else:
                    msgs.append({"role": "user", "content": reminder})
                request = StructuredOutputRequest(
                    output_schema=output_schema,
                    messages=msgs,
                    system=system,
                    temperature=request.temperature,
                    max_tokens=token_budget,
                )
            with span("phase", "model", "model_repair:invalid_structure"):
                result = provider.structured_output(request, model=cfg.model)
            return self._coerce(result, output_schema)

    @staticmethod
    def _coerce(result: Any, output_schema: Type[T]) -> T:
        if not isinstance(result, output_schema):
            if isinstance(result, dict):
                return output_schema.model_validate(result)
            raise LLMError(
                f"Provider 返回类型 {type(result)} 不符合 {output_schema}"
            )
        return result

    def embed(self, texts: list[str]) -> EmbeddingResponse:
        """调用嵌入模型。所有任务共用同一嵌入配置。"""
        emb_cfg = self._router.embedding_config()
        provider = self._get_provider(emb_cfg.provider)
        return provider.embed(EmbeddingRequest(texts=texts), model=emb_cfg.model)

    # ===== 注册管理 =====

    def register_provider(self, name: str, provider: LLMProvider) -> None:
        """手动注册 provider。用于测试或扩展。"""
        self._providers[name] = provider

    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    def _get_provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            raise LLMError(
                f"Provider {name} 未注册（可能缺少对应环境变量凭据），"
                f"当前可用 providers={list(self._providers.keys())}"
            )
        provider = self._providers[name]
        from core.invocation_audit import ACTIVE
        if ACTIVE.get() and not getattr(provider,"audited_transport",False):
            raise LLMError("当前提供方尚未接入可约束的请求审计，本次未发出调用；请使用已支持的兼容接口")
        return provider
