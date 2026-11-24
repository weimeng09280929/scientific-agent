"""Offline account administration. Passwords are prompted, never CLI arguments."""
import argparse
import getpass
import sqlite3
import warnings
from pathlib import Path

from core.local_accounts import AccountStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True, help="独立账号 SQLite 文件")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init")
    for action in ("create", "disable"):
        sub.add_parser(action).add_argument("email")
    args = parser.parse_args()
    store = AccountStore(args.db)
    if args.action == "init":
        store.initialize()
        print("账号库已初始化")
    elif args.action == "create":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("密码：")
                confirmation = getpass.getpass("再输入一次：")
        except getpass.GetPassWarning:
            raise SystemExit("需要可安全隐藏输入的交互终端；未读取密码")
        if password != confirmation:
            raise SystemExit("两次输入不一致")
        try:
            store.create_user(args.email, password)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise SystemExit("账号创建失败：邮箱已存在或输入不符合要求") from exc
        print("账号已创建；邮箱尚未验证")
    elif args.action == "disable":
        store.disable_user(args.email)
        print("账号已禁用，现有会话已撤销")


if __name__ == "__main__":
    main()
