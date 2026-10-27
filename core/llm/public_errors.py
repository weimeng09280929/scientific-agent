"""Fixed public diagnostics: exception text may contain credentials or document data."""

from __future__ import annotations

import sqlite3

from core.llm.base import LLMTruncatedError
from core.llm.task_router import TaskNotFoundError


_MESSAGES = {
    "authentication": "模型凭据未通过验证；请在服务端核对凭据后明确重试。",
    "access_denied": "模型访问被拒绝；请核对服务商权限与可用模型后明确重试。",
    "rate_limited": "模型服务正在限流；请等待服务商要求的时间后明确重试。",
    "server_error": "模型服务暂时不可用；请稍后核对任务状态再明确重试。",
    "request_rejected": "模型服务拒绝了请求；请核对任务路由、模型标识与服务商额度。",
    "timeout": "模型请求超时；请核对连接及原请求状态，避免立即重复计费。",
    "network": "模型服务连接失败；请在服务端核对网络和模型端点后明确重试。",
    "missing_configuration": "模型配置无法使用；请核对任务路由、凭据和依赖后重启服务。",
    "truncated_output": "模型输出被截断，未采用不完整内容；请缩小问题后明确重试。",
    "invalid_response": "模型回复格式未通过校验；未采用不完整内容，可核对调用记录后明确重试。",
    "invalid_evidence": "模型整理未通过依据校验；检索结果已保留，请核对引用范围后只重试整理。",
    "budget_exhausted": "本次请求已达到调用上限；请核对调用记录后决定是否明确重试。",
    "storage": "任务保存未完成；请先读取已有任务状态，核对存储后明确重试。",
    "historical_diagnostic": "历史诊断原文已隐藏；请核对当前配置与调用记录后明确重试。",
    "unexpected_error": "任务未能完成；已有材料保留，请核对任务状态与调用记录后明确重试。",
}
_STAGES = {
    "planning": "模型规划失败（research_plan），已回退规则规划",
    "ranking": "模型筛选失败（research_rank），已回退规则筛选",
    "synthesis": "模型整理失败（research_synthesize），已回退规则模式报告；截断或不可解析的输出未被采用",
}
SYNTHESIS_FAILURE = "整理阶段失败（检索结果已保留，可只重试整理）："
SOURCE_FAILURE = "全部检索来源失败；请查看来源状态，检查网络或等待限流结束后明确重试。"
RESUME_FAILURE = "补跑仍未成功；已交付报告保持不变，请查看失败来源状态后再明确补跑。"


def public_error(exc: Exception) -> str:
    """Inspect only known types/status codes, including wrapped provider errors."""
    chain, current = [], exc
    while current is not None and len(chain) < 6 and all(current is not e for e in chain):
        chain.append(current)
        if isinstance(current, LLMTruncatedError):
            return _message("truncated_output")
        reason = getattr(current, "reason", None)
        if reason in ("budget_exhausted", "rate_limited"):
            return _message(reason)
        response = getattr(current, "response", None)
        status = getattr(current, "status_code", None) or getattr(response, "status_code", None)
        if isinstance(status, int) and 400 <= status <= 599:
            return _message(
                {401: "authentication", 403: "access_denied", 429: "rate_limited"}.get(
                    status, "server_error" if status >= 500 else "request_rejected"
                )
            )
        if isinstance(current, TimeoutError) or type(current).__name__ in (
            "APITimeoutError", "Timeout", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
        ):
            return _message("timeout")
        if isinstance(current, ConnectionError) or type(current).__name__ in (
            "APIConnectionError", "ConnectionError", "ProxyError", "ConnectError", "NetworkError",
        ):
            return _message("network")
        if isinstance(current, (TaskNotFoundError, FileNotFoundError, ImportError)):
            return _message("missing_configuration")
        if isinstance(current, sqlite3.Error):
            return _message("storage")
        if type(current).__name__ == "SynthesisValidationError":
            return _message("invalid_evidence")
        current = current.__cause__ or current.__context__
    if any(isinstance(e, (ValueError, TypeError, KeyError, AttributeError)) for e in chain):
        return _message("invalid_response")
    return _message("unexpected_error")


def _message(category: str) -> str:
    return f"[{category}] {_MESSAGES[category]}"


def fallback_note(stage: str, exc: Exception) -> str:
    return f"{_STAGES[stage]}（{public_error(exc)}）"


def stored_note(value: str | None, stage: str) -> str:
    """Do not republish old raw exception strings; leave the stored record intact."""
    if not value:
        return ""
    allowed = {f"{_STAGES[stage]}（{_message(category)}）" for category in _MESSAGES}
    if stage == "planning":
        allowed.add("模型规划输出为空")
    return value if value in allowed else f"{_STAGES[stage]}（{_message('historical_diagnostic')}）"


def stored_error(value: str | None) -> str | None:
    if not value:
        return value
    allowed = {_message(category) for category in _MESSAGES}
    allowed.update(SYNTHESIS_FAILURE + _message(category) for category in _MESSAGES)
    allowed.update((SOURCE_FAILURE, RESUME_FAILURE, "项目已有其他检索任务在执行，本任务未启动"))
    return value if value in allowed else _message("historical_diagnostic")
