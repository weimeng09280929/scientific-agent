"""Explicit local profiles: legacy demo and isolated user accounts.

Both bind loopback; live models require explicit opt-in for the personal demo.
Literature calls require a separate explicit opt-in. No test papers are
injected into the user's historical projects.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def account_data_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "ScientificAgent" / "account-workspace"


def configure(mode: str, port: int, data_root: Path | None, registration: bool):
    if mode not in {"demo", "accounts"} or not 1 <= port <= 65535:
        raise ValueError("启动模式或端口不合法")
    root = (data_root or (REPOSITORY if mode == "demo" else account_data_root())).resolve()
    if mode == "accounts" and (port == 8001 or root == REPOSITORY.resolve()):
        raise ValueError("8001 和原项目目录保留给免登录演示；账号版请使用独立端口和目录")
    registry = root / "accounts.db"
    restored = (root / ".sra-restore.json").exists()
    if (root / "INCOMPLETE").exists() or root.name.startswith((".sra-b-", ".sra-r-")):
        raise ValueError("该目录的恢复未完成，不能启动")
    if restored:
        from core.local_backup import read_json, relative
        receipt = read_json(root / ".sra-restore.json")
        if receipt.get("version") != 1 or receipt.get("layout", {}).get("mode") != mode:
            raise ValueError("恢复目录模式与启动模式不匹配，不能混用账号与演示数据")
        if mode == "accounts":
            registry = root.joinpath(*relative(receipt["layout"]["accounts_db"]).parts)
            if registry.parent not in (root, root / "projects") or not registry.is_file():
                raise ValueError("恢复的账号库缺失或布局不兼容，未初始化替代账号库")
    os.environ.update({
        "SRA_AUTH_MODE": "local" if mode == "demo" else "accounts",
        "SRA_DATA_ROOT": str(root), "SRA_PROJECTS_DIR": str(root / "projects"),
        "SRA_ACCOUNTS_DB": str(registry),
        "SRA_AUTH_INSECURE_HTTP": "1",
        "SRA_ALLOW_LOCAL_REGISTRATION": "1" if mode == "accounts" and registration else "0",
        "SRA_WEB_HOST": "127.0.0.1", "SRA_WEB_PORT": str(port),
        "SRA_WEB_SKIP_GUARD": "1", "SRA_WEB_SKIP_PROJECT_SCAN": "0", "SRA_DRY_RUN": "true",
    })
    if mode == "accounts":
        from core.local_accounts import AccountStore
        store = AccountStore(registry)
        if not store.path.exists():
            store.initialize()  # Explicit local launcher initializes only the app registry.
        store.check_schema()
    return root


class LocalCallsDisabled(RuntimeError):
    reason = "local_calls_disabled"
    retry_after_seconds = None


def disable_live_calls(*, allow_model=False, allow_sources=False):
    from core.llm import LLMRegistry
    from core.research_tasks import sources
    import requests

    def unavailable(*args, **kwargs):
        raise LocalCallsDisabled("本地入口未启用此网络功能；请使用启用真实调用的启动方式")

    if not allow_model:
        LLMRegistry.from_config = classmethod(unavailable)
    if not allow_sources:
        sources.SOURCE_FUNCS = {name: unavailable for name in sources.SOURCE_FUNCS}
        requests.sessions.Session.request = unavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("demo", "accounts"), required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--allow-registration", action="store_true")
    parser.add_argument("--allow-live-model", action="store_true", help="Explicitly enable configured paid models for the personal demo; no extra experience cap")
    parser.add_argument("--allow-live-sources", action="store_true", help="Enable real literature sources for the personal demo")
    args = parser.parse_args()
    if (args.allow_live_model or args.allow_live_sources) and args.mode != "demo":
        parser.error("真实账号体验请使用单独的费用保护入口；此选项仅用于自用 demo")
    if args.allow_live_model:
        from runtime.cli import _load_env
        _load_env()
    port = args.port if args.port is not None else (8001 if args.mode == "demo" else 8077)
    configure(args.mode, port, args.data_root, args.allow_registration)
    if args.allow_live_model:
        os.environ["SRA_DRY_RUN"] = "false"
    disable_live_calls(allow_model=args.allow_live_model, allow_sources=args.allow_live_sources)
    from web.api import app
    import uvicorn
    print(f"Scientific Agent {args.mode}: http://127.0.0.1:{port}", flush=True)
    if args.allow_live_model:
        print("Configured live model enabled; no added experience cost cap; original request and concurrency guards retained", flush=True)
    if args.allow_live_sources:
        print("Live literature sources enabled; original source limits and explicit task confirmation retained", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1)


if __name__ == "__main__":
    main()
