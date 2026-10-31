"""Explicit local profiles: legacy demo and isolated user accounts.

Both bind loopback and disable live model/literature calls. No test papers are
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


def disable_live_calls():
    from core.llm import LLMRegistry
    from core.research_tasks import sources
    import requests

    def unavailable(*args, **kwargs):
        raise RuntimeError("本地展示未启用真实模型或文献网络调用；已有资料和离线教学仍可查看")

    LLMRegistry.from_config = classmethod(unavailable)
    sources.SOURCE_FUNCS = {name: unavailable for name in sources.SOURCE_FUNCS}
    requests.sessions.Session.request = unavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("demo", "accounts"), required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--allow-registration", action="store_true")
    args = parser.parse_args()
    port = args.port if args.port is not None else (8001 if args.mode == "demo" else 8077)
    configure(args.mode, port, args.data_root, args.allow_registration)
    disable_live_calls()
    from web.api import app
    import uvicorn
    print(f"Scientific Agent {args.mode}: http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1)


if __name__ == "__main__":
    main()
