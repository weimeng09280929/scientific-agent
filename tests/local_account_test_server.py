"""Isolated account acceptance server; never points to the repository projects dir."""
import os
import tempfile
from pathlib import Path
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--allow-registration", action="store_true")
    args = parser.parse_args()
    from contextlib import nullcontext
    context = nullcontext(str(args.root.resolve())) if args.root else tempfile.TemporaryDirectory(prefix="sra-browser-auth-")
    with context as directory:
        root = Path(directory)
        os.environ.update({
            "SRA_AUTH_MODE": "accounts", "SRA_AUTH_INSECURE_HTTP": "1",
            "SRA_PROJECTS_DIR": str(root / "projects"),
            "SRA_DATA_ROOT": str(root),
            "SRA_ACCOUNTS_DB": str(root / "accounts.db"),
            "SRA_WEB_SKIP_GUARD": "1", "SRA_WEB_SKIP_PROJECT_SCAN": "1",
            "SRA_DRY_RUN": "true", "SRA_WEB_PORT": "8077",
            "SRA_ALLOW_LOCAL_REGISTRATION": "1" if args.allow_registration else "0",
        })
        from core.local_accounts import AccountStore
        store = AccountStore(root / "accounts.db")
        store.initialize()
        # Reuse the real management entry point with an offline getpass input
        # substitute; no password appears in argv or logs.
        from unittest.mock import patch
        from contextlib import closing
        from tools.local_accounts import main as manage
        for suffix in ("a", "b"):
            with closing(store._connect()) as conn:
                exists=conn.execute("SELECT 1 FROM users WHERE email=?",("tester-"+suffix+"@example.com",)).fetchone()
            if not exists:
                with patch("sys.argv", ["local_accounts", "--db", str(store.path), "create", "tester-"+suffix+"@example.com"]), patch("getpass.getpass", return_value="browser acceptance "+suffix.upper()):
                    manage()
        from core.config import get_config
        from tests.local_account_fixture import install
        state = install(get_config())
        import uvicorn
        from web.api import app
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))

        @app.get("/__account__/state")
        def fixture_state():
            return {**state, "data_root":str(root)}

        @app.post("/__account__/shutdown")
        def shutdown():
            server.should_exit = True
            return {"stopping":True}

        server.run()


if __name__ == "__main__":
    main()
