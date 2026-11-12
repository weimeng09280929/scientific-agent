"""Explicit search diagnostics. List compatibility is retained for older callers."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import requests
import math
from xml.etree.ElementTree import ParseError


def retry_delay(value):
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError):
        try:
            return max(
                0.0,
                (
                    parsedate_to_datetime(value) - datetime.now(timezone.utc)
                ).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None


def error_details(exc):
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code in (429, 401, 403):
        category = {429: "rate_limited", 401: "authentication", 403: "access_denied"}[
            code
        ]
    elif isinstance(exc, (requests.Timeout, TimeoutError)):
        category = "timeout"
    elif isinstance(exc, requests.exceptions.ProxyError):
        category = "proxy_error"
    elif isinstance(exc, (ValueError, ParseError)):
        category = "parse_error"
    elif code is not None:
        category = "server_error" if code >= 500 else "http_error"
    elif isinstance(exc, requests.RequestException):
        category = "network"
    else:
        category = "unexpected_error"
    delay = retry_delay(getattr(response, "headers", {}).get("Retry-After"))
    if hasattr(exc, "reason") and hasattr(exc, "retry_after_seconds"):
        category, delay = exc.reason, exc.retry_after_seconds
    return {
        "error_class": category,
        "http_status": code,
        "retryable": category
        in {"rate_limited", "timeout", "proxy_error", "network", "server_error"},
        "retry_after_seconds": delay,
        "error": "当前入口未启用真实文献访问，请检查启动配置后手动重试" if category == "local_calls_disabled" else f"{category}" + (f" (HTTP {code})" if code else ""),
    }


class SearchResults(list):
    """Only explicit data, never log messages, determines search status."""

    def __init__(
        self,
        papers=(),
        *,
        response=None,
        raw_count=None,
        parse_errors=0,
        available=None,
    ):
        super().__init__(papers)
        self.status = "partial" if parse_errors else "ok" if self else "zero"
        if parse_errors and not self:
            self.status = "error"
        self.raw_count = raw_count if raw_count is not None else len(self)
        self.available = available
        self.parse_errors = parse_errors
        self.error_class = "parse_error" if parse_errors else ""
        self.error = f"{parse_errors} 条来源记录无法解析" if parse_errors else ""
        self.retryable = False
        self.retry_after_seconds = None
        self.http_status = getattr(response, "status_code", None)
        self.attempts = getattr(response, "sra_call_info", [])

    @classmethod
    def failure(cls, exc, response=None):
        result = cls()
        result.status = "error"
        result.raw_count = (
            None  # Failure/invalid payload is not a known zero-return count.
        )
        for key, value in error_details(exc).items():
            setattr(result, key, value)
        result.attempts = getattr(
            exc,
            "sra_call_info",
            getattr(getattr(exc, "response", None), "sra_call_info", []),
        )
        if response is not None:
            result.attempts = getattr(response, "sra_call_info", result.attempts)
            result.http_status = response.status_code
        return result
