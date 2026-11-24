"""Explicit stopped-instance backup / verification / restore; no implicit paths."""
import argparse
import json
from pathlib import Path
import sqlite3

from core.local_backup import BackupError, backup, verify, restore, precheck, layout, require
from core.instance_state import data_lease


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("precheck", "backup"):
        p = commands.add_parser(name)
        p.add_argument("--root", type=Path, required=True)
        p.add_argument("--mode", choices=("accounts", "demo"), required=True)
        p.add_argument("--accounts-db", help="Relative to root; never auto-merge account stores")
        p.add_argument("--confirm-stopped", action="store_true")
        if name == "backup": p.add_argument("--destination", type=Path, required=True)
    p = commands.add_parser("verify"); p.add_argument("--backup", type=Path, required=True)
    p = commands.add_parser("restore"); p.add_argument("--backup", type=Path, required=True); p.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = backup(args.root, args.destination, args.mode, args.accounts_db, confirm_stopped=args.confirm_stopped)
        elif args.command == "precheck":
            require(args.confirm_stopped, "先正常停止实例并传入 --confirm-stopped；不支持热备")
            root, spec = layout(args.root, args.mode, args.accounts_db)
            with data_lease(root, root / "projects", root / spec["accounts_db"] if spec["accounts_db"] else None):
                _, _, records, summary = precheck(root, args.mode, args.accounts_db, confirm_stopped=args.confirm_stopped)
                result = {"ok": True, "files": len(records), "projects": summary["projects"], "unowned_projects": summary["unowned_projects"]}
        elif args.command == "verify":
            manifest = verify(args.backup)
            result = {"ok": True, "format": manifest["format"], "files": len(manifest["files"]), "mode": manifest["layout"]["mode"]}
        else:
            result = restore(args.backup, args.destination)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError, TypeError) as error:
        # Expected operational errors have deliberately non-sensitive messages.
        message = str(error) if isinstance(error, (BackupError, RuntimeError)) else "文件或数据库检查失败；未发布可用备份／实例，请核对目录、权限与完整性"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
