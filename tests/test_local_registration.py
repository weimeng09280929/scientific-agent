"""Local registration and dual launcher boundaries; isolated account fixtures."""
from contextlib import closing
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.test_local_account_isolation import isolated


def payload(secret, email="new-user@example.com"):
    return {"email":email,"password":secret.get_secret_value(),"confirm_password":secret.get_secret_value()}


def test_register_eight_character_password_then_login(isolated, monkeypatch):
    from web import api
    root, store, a, _, _, _ = isolated
    monkeypatch.setenv("SRA_ALLOW_LOCAL_REGISTRATION","1")
    secret=SecretStr("Az9!xY2?")
    assert a.get("/api/auth/options").json()["registration_enabled"] is True
    assert a.get("/api/auth/options").json()["min_password_length"] == 8
    result=a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(secret," New-User@Example.COM "))
    assert result.status_code == 201 and result.json()["email_verified"] is False
    assert "sra_session" not in a.cookies  # Registration does not switch any login session.
    assert a.get("/api/projects").status_code == 401
    login=a.post("/api/auth/login",headers={"X-SRA-Login":"1"},json={"email":"new-user@example.com","password":secret.get_secret_value()})
    assert login.status_code == 200
    assert login.json()["user"]["email_verified"] is False
    assert a.get("/api/projects").json()["projects"] == []
    with closing(sqlite3.connect(store.path)) as conn:
        hashed,verified=conn.execute("SELECT password_hash,email_verified FROM users WHERE email=?",("new-user@example.com",)).fetchone()
        assert hashed.startswith("$argon2id$") and verified == 0
        assert secret.get_secret_value() not in hashed
    duplicate=a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(secret,"NEW-USER@example.com"))
    assert duplicate.status_code == 409
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("SELECT count(*) FROM users WHERE email=?",("new-user@example.com",)).fetchone()[0] == 1


def test_registration_is_opt_in_and_never_remote(isolated, monkeypatch):
    from web import api
    _, _, a, _, _, _ = isolated
    secret=SecretStr("Az9!xY2?")
    assert a.get("/api/auth/options").json()["registration_enabled"] is False
    assert a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(secret)).status_code == 404
    monkeypatch.setenv("SRA_ALLOW_LOCAL_REGISTRATION","1")
    monkeypatch.delenv("SRA_AUTH_INSECURE_HTTP")
    with TestClient(api.app,base_url="https://public.example",client=("192.0.2.55",12000)) as remote:
        assert remote.get("/api/auth/options").json()["registration_enabled"] is False
        assert remote.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(secret)).status_code == 404


def test_registration_csrf_input_validation_and_no_password_echo(isolated, monkeypatch):
    _, _, a, _, _, _ = isolated
    monkeypatch.setenv("SRA_ALLOW_LOCAL_REGISTRATION","1")
    secret=SecretStr("Az9!xY2?")
    assert a.post("/api/auth/register",json=payload(secret)).status_code == 403
    assert a.post("/api/auth/register",headers={"X-SRA-Login":"1","Origin":"http://other.invalid"},json=payload(secret)).status_code == 403
    short=SecretStr("Z9!aB2?")
    response=a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(short))
    assert response.status_code == 422 and short.get_secret_value() not in response.text
    invalid=payload(secret); invalid["confirm_password"]="Different8!"
    assert a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=invalid).status_code == 422
    weak=SecretStr("Password123")
    assert a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(weak)).status_code == 422
    extra=payload(secret);extra["user_id"]="client-chosen-owner"
    response=a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=extra)
    assert response.status_code == 422 and secret.get_secret_value() not in response.text


def test_registration_rate_limit_is_persistent(isolated, monkeypatch):
    from core.local_accounts import AccountStore
    _, store, a, _, _, _ = isolated
    monkeypatch.setenv("SRA_ALLOW_LOCAL_REGISTRATION","1")
    weak=SecretStr("Password123")
    for _ in range(5):
        assert a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(weak)).status_code == 422
    response=a.post("/api/auth/register",headers={"X-SRA-Login":"1"},json=payload(weak))
    assert response.status_code == 429 and response.headers["retry-after"] == "900"
    assert AccountStore(store.path).allow_registration_attempt("testclient") is False


def test_password_character_support_and_conservative_denylist():
    from core.local_accounts import validate_password
    for password in ("Az9!xY2?", "larkmoon", "大写A数字9符号!", "Ab7!"*64):
        validate_password(password)
    for password in ("aB2!xY7", "Password123", "12345678", "qwerty123", "z"*16, "Ab7!"*65):
        with pytest.raises(ValueError):validate_password(password)


def test_launcher_profiles_keep_data_separate(tmp_path, monkeypatch):
    import os
    from tools.serve_local import configure, REPOSITORY
    for key in ("SRA_AUTH_MODE","SRA_DATA_ROOT","SRA_PROJECTS_DIR","SRA_ACCOUNTS_DB","SRA_AUTH_INSECURE_HTTP",
                "SRA_ALLOW_LOCAL_REGISTRATION","SRA_WEB_HOST","SRA_WEB_PORT","SRA_WEB_SKIP_GUARD","SRA_WEB_SKIP_PROJECT_SCAN","SRA_DRY_RUN"):
        monkeypatch.setenv(key,os.environ.get(key,""))
    legacy=tmp_path/"legacy"; accounts=tmp_path/"accounts"
    configure("demo",8001,legacy,False)
    assert os.environ["SRA_AUTH_MODE"] == "local"
    assert os.environ["SRA_PROJECTS_DIR"] == str(legacy.resolve()/"projects")
    configure("accounts",8077,accounts,True)
    assert os.environ["SRA_AUTH_MODE"] == "accounts"
    assert os.environ["SRA_ALLOW_LOCAL_REGISTRATION"] == "1"
    assert os.environ["SRA_PROJECTS_DIR"] != str(legacy.resolve()/"projects")
    assert (accounts/"accounts.db").is_file()
    assert not (legacy/"accounts.db").exists()
    with pytest.raises(ValueError):configure("accounts",8001,accounts,True)
    with pytest.raises(ValueError):configure("accounts",8077,REPOSITORY,True)
