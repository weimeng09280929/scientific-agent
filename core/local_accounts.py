"""Local, explicitly initialized account registry; never reads project databases."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

SESSION_SECONDS = 12 * 60 * 60
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256
# Small offline denylist, not a claim to cover all compromised passwords.
_COMMON_PASSWORDS = frozenset({
    "password", "password1", "password123", "password1234", "p@ssw0rd", "p@ssw0rd1",
    "12345678", "123456789", "1234567890", "87654321", "abcdefgh", "abcdefghij",
    "qwertyui", "qwertyuiop", "qwerty123", "qwerty12345", "admin123", "admin1234",
    "letmein1", "letmein123", "iloveyou", "welcome1", "welcome123", "changeme",
    "abc12345", "abcd1234", "qwer1234", "1qaz2wsx", "zaq12wsx", "football",
    "sunshine", "princess", "computer", "trustno1", "passw0rd",
})
_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_DUMMY_HASH = _HASHER.hash("unregistered-account-dummy-password")


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or value.count("@") != 1 or any(c.isspace() for c in value):
        raise ValueError("邮箱格式不正确")
    local, domain = value.split("@")
    if not local or not domain or "." not in domain or len(local) > 64:
        raise ValueError("邮箱格式不正确")
    return value


def validate_password(value: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH or len(value.encode("utf-8")) > 1024:
        raise ValueError("密码长度须为 8–256 个字符且不超过 1024 字节")
    if value.strip().casefold() in _COMMON_PASSWORDS or len(set(value)) == 1:
        raise ValueError("密码过于常见或容易猜测，请换一个密码；建议组合大小写字母、数字和特殊符号")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self):
        if not self.path.is_file():
            raise RuntimeError("账号库未初始化；请先运行本地管理命令")
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                  password_hash TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0,
                  email_verified INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                  csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL,
                  revoked_at INTEGER);
                CREATE TABLE IF NOT EXISTS project_owners (
                  project_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                  created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS project_owners_user ON project_owners(user_id);
                CREATE TABLE IF NOT EXISTS login_attempts (
                  bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, first_at INTEGER NOT NULL,
                  blocked_until INTEGER NOT NULL DEFAULT 0);
                PRAGMA user_version=1;
            """)

    def check_schema(self):
        with closing(self._connect()) as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("账号库版本不受支持，请先执行显式升级")
            for table in ("users", "sessions", "project_owners", "login_attempts"):
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    raise RuntimeError("账号库不完整，请检查初始化")

    def create_user(self, email: str, password: str) -> str:
        email = normalize_email(email)
        validate_password(password)
        uid = uuid.uuid4().hex
        hashed = _HASHER.hash(password)
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
                         (uid, email, hashed, int(time.time())))
        return uid

    def allow_registration_attempt(self, remote: str) -> bool:
        """Reserve a bounded attempt before hashing, across service restarts."""
        bucket = "register-ip:" + _digest(remote)
        now = int(time.time())
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
            if row and row["blocked_until"] > now:
                return False
            count = row["count"] + 1 if row and now - row["first_at"] < 900 else 1
            first = row["first_at"] if row and now - row["first_at"] < 900 else now
            conn.execute("INSERT INTO login_attempts VALUES(?,?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=excluded.count,first_at=excluded.first_at,blocked_until=excluded.blocked_until",
                         (bucket,count,first,now+900 if count >= 5 else 0))
            return True

    def disable_user(self, email: str):
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT id FROM users WHERE email=?", (normalize_email(email),)).fetchone()
            if row is None:
                raise ValueError("账号不存在")
            conn.execute("UPDATE users SET disabled=1 WHERE id=?", (row["id"],))
            conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                         (int(time.time()), row["id"]))

    def login(self, email: str, password: str, remote: str) -> tuple[str, str, dict] | None:
        try:
            email = normalize_email(email)
        except ValueError:
            email = "invalid"
        now = int(time.time())
        # Bound both a source and an account name, including nonexistent names.
        buckets = ("ip:" + _digest(remote), "mail:" + _digest(email))
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for bucket in buckets:
                row = conn.execute("SELECT count,first_at,blocked_until FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
                if row and row["blocked_until"] > now:
                    return None
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            try:
                valid = _HASHER.verify(row["password_hash"] if row else _DUMMY_HASH, password)
            except (VerifyMismatchError, VerificationError):
                valid = False
            if not valid or row is None or row["disabled"]:
                for bucket in buckets:
                    prior = conn.execute("SELECT count,first_at FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
                    count = (prior["count"] + 1) if prior and now - prior["first_at"] < 900 else 1
                    first = prior["first_at"] if prior and now - prior["first_at"] < 900 else now
                    conn.execute("INSERT INTO login_attempts VALUES(?,?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=excluded.count,first_at=excluded.first_at,blocked_until=excluded.blocked_until",
                                 (bucket, count, first, now + 900 if count >= 5 else 0))
                return None
            for bucket in buckets:
                conn.execute("DELETE FROM login_attempts WHERE bucket=?", (bucket,))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?,NULL)",
                         (_digest(token), row["id"], csrf, now + SESSION_SECONDS))
            return token, csrf, {"id": row["id"], "email": row["email"], "email_verified": False}

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT u.id,u.email,u.disabled,s.csrf_token,s.expires_at,s.revoked_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (_digest(token),)).fetchone()
            if row is None or row["disabled"] or row["revoked_at"] is not None or row["expires_at"] <= int(time.time()):
                return None
            return dict(row)

    def logout(self, token: str | None):
        if token:
            with closing(self._connect()) as conn, conn:
                conn.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                             (int(time.time()), _digest(token)))

    def claim_new(self, project_id: str, user_id: str):
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO project_owners VALUES(?,?,?)", (project_id, user_id, int(time.time())))
            row = conn.execute("SELECT user_id FROM project_owners WHERE project_id=?", (project_id,)).fetchone()
            if row is None or row["user_id"] != user_id:
                raise PermissionError("项目不存在")

    def owns(self, project_id: str, user_id: str) -> bool:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM project_owners WHERE project_id=? AND user_id=?", (project_id, user_id)).fetchone() is not None

    def project_ids(self, user_id: str) -> set[str]:
        with closing(self._connect()) as conn:
            return {r[0] for r in conn.execute("SELECT project_id FROM project_owners WHERE user_id=?", (user_id,))}
