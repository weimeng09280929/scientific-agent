"""检索来源的统一 HTTP 出口。

代理策略（默认尊重环境既有配置，不做全局禁用）：
- 本地回环永远直连：请求库遵循 NO_PROXY；服务入口已把 localhost、127.0.0.1、
  ::1 并入进程级 NO_PROXY（保留既有条目），避免本地模型网关被代理劫持。
- `SRA_SOURCE_DIRECT=1` 时，检索来源请求显式绕过代理——仅作用于本出口的
  检索调用，不影响模型客户端或其他出网请求；默认关闭，不强制。
- 诊断脱敏：错误分类只依据状态码与异常类型，不输出代理认证信息或完整
  认证 URL。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def ensure_loopback_direct():
    """把回环地址并入进程级 NO_PROXY（幂等；保留既有条目，不清空配置）。"""
    for var in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(var, "")
        entries = [e.strip() for e in current.split(",") if e.strip()]
        missing = [h for h in _LOOPBACK_HOSTS if h not in entries]
        if missing:
            os.environ[var] = ",".join([*entries, *missing])


def source_direct_enabled() -> bool:
    return os.environ.get("SRA_SOURCE_DIRECT", "").strip().lower() in ("1", "true", "yes", "on")


def _source_proxies():
    """SRA_SOURCE_DIRECT 开启时对来源请求显式绕过代理；默认 None 尊重环境。"""
    if source_direct_enabled():
        return {"http": None, "https": None, "all": None}
    return None


def classify_failure(message: str) -> str:
    """把来源失败信息归类，供界面区分处置方式；不新增敏感信息。"""
    lowered = (message or "").lower()
    if "proxy" in lowered or ("connect" in lowered and "tunnel" in lowered):
        return "proxy_error"  # 代理连接失败（常见于代理不可用或拒绝该目标）
    if "timed out" in lowered or "timeout" in lowered or "读取超时" in lowered:
        return "timeout"  # 来源超时：可稍后补跑
    if "429" in lowered or "rate limit" in lowered or "限流" in lowered:
        return "rate_limited"  # 限流：降低频率后重试
    if "401" in lowered or "403" in lowered or "forbidden" in lowered or "unauthorized" in lowered:
        return "auth_or_blocked"  # 认证失败或来源拒绝（含 IP 拦截）
    if "connection" in lowered or "network" in lowered or "name resolution" in lowered or "11001" in lowered:
        return "network"  # 网络/DNS 不可达
    return "unknown"


def http_get(url, *, timeout, params=None, headers=None):
    """来源检索统一 GET：默认尊重环境代理，可选来源级直连。"""
    from core.invocation_audit import ACTIVE
    from core.tools.search_result import retry_delay
    audit = ACTIVE.get()
    host = urlparse(url).hostname or "source"
    trace = []
    # No implicit redirect chain or urllib3 retries: each requests.get uses its
    # default zero-retry adapter. Redirects are reported, not silently followed.
    ticket = audit.begin("network","source",host) if audit else None
    started = time.monotonic()
    record = {"started_at": datetime.now(timezone.utc).isoformat(), "host": host}
    trace.append(record)
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout,
                                proxies=_source_proxies(), allow_redirects=False)
        record["http_status"] = response.status_code
        if audit:
            audit.finish(ticket, outcome="success" if response.status_code < 300 else "http_error",
                         http_status=response.status_code, retry_after=retry_delay(response.headers.get("Retry-After")))
        response.sra_call_info = trace
        if 300 <= response.status_code < 400:
            error = requests.HTTPError("来源返回重定向，请核对来源配置",response=response)
            error.sra_call_info = trace
            raise error
        return response
    except requests.RequestException as exc:
        record["error_class"] = type(exc).__name__
        exc.sra_call_info = trace
        if audit:
            audit.finish(ticket, outcome=type(exc).__name__)
        raise
    finally:
        record["elapsed_ms"] = round((time.monotonic()-started)*1000,3)
