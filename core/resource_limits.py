"""Single-process, nonblocking admission. Leases live until actual work exits."""
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from functools import wraps
import os
import threading


ACTOR = ContextVar("sra_resource_actor", default="local-demo")


@dataclass(frozen=True)
class ResourceLimits:
    heavy_user: int = 1
    heavy_global: int = 3
    receive_user: int = 2
    receive_global: int = 2
    file_bytes: int = 8 * 1024 * 1024
    body_bytes: int = 9 * 1024 * 1024
    field_bytes: int = 4096
    header_bytes: int = 8192
    spool_bytes: int = 64 * 1024
    chunk_bytes: int = 64 * 1024
    receive_seconds: int = 120

    @classmethod
    def from_env(cls):
        values = {name: int(os.environ.get("SRA_RESOURCE_" + name.upper(), default))
                  for name, default in cls().__dict__.items()}
        if any(value < 1 for value in values.values()) or values["body_bytes"] <= values["file_bytes"]:
            raise ValueError("资源限制必须为正整数，请求体上限必须大于文件上限")
        return cls(**values)


class CapacityError(Exception):
    def __init__(self, scope):
        self.scope = scope
        self.code = "account_busy" if scope == "user" else "system_busy"
        super().__init__(
            "本账号已有操作占用处理名额，请等待完成后再提交；原输入已保留。"
            if scope == "user" else
            "系统当前繁忙，请稍后手动重试；原输入已保留，本次未开始生成。")


class Lease:
    def __init__(self, governor, category, actor, key=None):
        self.governor, self.category, self.actor = governor, category, actor
        self.key = key
        self.closed = False

    def close(self):
        with self.governor.lock:
            if not self.closed:
                self.closed = True
                if self.key:
                    self.governor.operations.discard(self.key)
                self.governor.counts[self.category][self.actor] -= 1
                if not self.governor.counts[self.category][self.actor]:
                    del self.governor.counts[self.category][self.actor]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def worker(self, fn):
        context = copy_context()
        def run():
            try:
                return context.run(fn)
            finally:
                self.close()
        return run


class ResourceGovernor:
    def __init__(self, limits=None):
        self.limits = limits or ResourceLimits.from_env()
        self.lock = threading.Lock()
        self.counts = {"heavy": {}, "receive": {}}
        self.operations = set()

    def is_active(self, key):
        with self.lock:
            return key in self.operations

    def acquire(self, category="heavy", *, key=None):
        actor = ACTOR.get()
        with self.lock:
            counts = self.counts[category]
            if key and key in self.operations:
                raise CapacityError("user")
            if counts.get(actor, 0) >= getattr(self.limits, category + "_user"):
                raise CapacityError("user")
            if sum(counts.values()) >= getattr(self.limits, category + "_global"):
                raise CapacityError("global")
            counts[actor] = counts.get(actor, 0) + 1
            if key:
                self.operations.add(key)
        return Lease(self, category, actor, key)

    def snapshot(self):
        # Tests only; never expose other actors or active operation names by API.
        with self.lock:
            return {k: dict(v) for k, v in self.counts.items()}


RESOURCES = ResourceGovernor()


def heavy_operation(fn):
    @wraps(fn)
    def limited(*args, **kwargs):
        with RESOURCES.acquire():
            return fn(*args, **kwargs)
    return limited
