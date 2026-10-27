"""Configuration guidance must remain offline and never expose deployment secrets."""

import json

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import GlobalConfig, LLMConfig
from core.llm.readiness import WORKSPACE_TASKS, runtime_status
from web.runtime_status_api import build_runtime_status_router


@pytest.fixture
def configuration(tmp_path):
    path = tmp_path / "private-location-tasks.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "tasks": {
                    name: {"provider": "deepseek", "model": "example-model"}
                    for name, _ in WORKSPACE_TASKS
                },
                "embedding": {"provider": "openai", "model": "optional-embedding"},
            }
        ),
        encoding="utf-8",
    )
    return GlobalConfig(llm=LLMConfig(tasks_config_path=path), dry_run=False)


def test_default_rules_work_without_any_model_configuration(configuration):
    configuration.dry_run = True
    result = runtime_status(configuration, {})
    assert result["status"] == "rules_mode"
    assert result["mode"] == "rules"
    assert result["connection_status"] == "not_checked"
    assert result["offline_experience"]["available"] is True
    assert all(c["status"] == "missing_credentials" for c in result["checks"])


def test_configured_is_explicitly_unverified_and_optional_services_do_not_block(configuration):
    result = runtime_status(configuration, {"DEEPSEEK_API_KEY": "secret-key"})
    assert result["status"] == "configured_unverified"
    assert result["model_calls_made"] is False
    assert len(result["checks"]) == 7
    assert all(c["status"] == "configured_unverified" for c in result["checks"])
    assert "embedding" not in str(result)
    assert "experiment_code_generate" not in str(result)
    assert "writing_outline" not in str(result)


@pytest.mark.parametrize("environ", [{}, {"DEEPSEEK_API_KEY": " \n "}, {"OPENAI_API_KEY": "other-key"}])
def test_requires_credentials_for_the_routed_provider(configuration, environ):
    result = runtime_status(configuration, environ)
    assert result["status"] == "needs_configuration"
    assert result["mode"] == "model"
    assert {c["status"] for c in result["checks"]} == {"missing_credentials"}
    assert {a["kind"] for a in result["actions"]} >= {"configure_model", "try_teaching"}


def test_one_missing_current_task_is_actionable(configuration):
    path = configuration.llm.tasks_config_path
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data["tasks"]["research_rank"]
    data["tasks"]["writing_outline"] = {"provider": "unavailable", "model": "unused"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = runtime_status(configuration, {"DEEPSEEK_API_KEY": "secret"})
    assert result["status"] == "needs_configuration"
    failures = [c for c in result["checks"] if c["status"] != "configured_unverified"]
    assert [c["task_type"] for c in failures] == ["research_rank"]
    assert failures[0]["status"] == "missing_route"


@pytest.mark.parametrize("contents", ["[]", "tasks: [bad]", "tasks: {research_plan: {}}", "tasks: {", ""])
def test_invalid_configuration_is_safe_and_non_fatal(configuration, contents):
    configuration.llm.tasks_config_path.write_text(contents, encoding="utf-8")
    result = runtime_status(configuration, {})
    assert result["status"] == "needs_configuration"
    assert all(c["status"] == "invalid_route" for c in result["checks"])
    assert str(configuration.llm.tasks_config_path) not in json.dumps(result)


@pytest.mark.parametrize("provider,model,expected", [("unregistered", "m", "unsupported_provider"), ("deepseek", "", "invalid_route"), (["deepseek"], "m", "invalid_route")])
def test_invalid_required_route_is_actionable(configuration, provider, model, expected):
    path = configuration.llm.tasks_config_path
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["tasks"]["research_plan"] = {"provider": provider, "model": model}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    result = runtime_status(configuration, {"DEEPSEEK_API_KEY": "secret"})
    assert result["status"] == "needs_configuration"
    assert next(c for c in result["checks"] if c["task_type"] == "research_plan")["status"] == expected


@pytest.mark.parametrize("callable_config", [True, False])
def test_api_reads_only_active_configuration_without_clients_or_network(configuration, monkeypatch, callable_config):
    import socket
    from core.llm import LLMRegistry

    def forbidden(*args, **kwargs):
        raise AssertionError("configuration guidance must not create clients or access network")

    monkeypatch.setattr(LLMRegistry, "from_config", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-never-return")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://secret:password@example.org/private-token")
    app = FastAPI()
    app.include_router(build_runtime_status_router((lambda: configuration) if callable_config else configuration))
    with TestClient(app) as client:
        # Windows creates a loopback self-pipe while starting the test event loop.
        monkeypatch.setattr(socket.socket, "connect", forbidden)
        response = client.get("/api/runtime-status")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["schema_version"] == "runtime-status.v1"
        assert response.json()["status"] == "configured_unverified"
        for hidden in ("secret-never-return", "password", "example.org", "private-token", str(configuration.llm.tasks_config_path)):
            assert hidden not in response.text
        configuration.dry_run = True
        assert client.get("/api/runtime-status").json()["status"] == "rules_mode"
        assert client.post("/api/runtime-status").status_code == 405


def test_missing_file_keeps_offline_path(configuration):
    configuration.llm.tasks_config_path.unlink()
    result = runtime_status(configuration, {})
    assert result["status"] == "needs_configuration"
    assert result["offline_experience"]["available"] is True


@pytest.mark.parametrize(
    "provider,setting",
    [
        ("openai", "openai_api_key_env"),
        ("anthropic", "anthropic_api_key_env"),
        ("minimax", "minimax_api_key_env"),
        ("mimo", "mimo_api_key_env"),
        ("deepseek", "deepseek_api_key_env"),
        ("spark", "spark_api_key_env"),
        ("siliconflow", "siliconflow_api_key_env"),
        ("local", "local_model_base_url_env"),
    ],
)
def test_uses_configured_credential_name_for_each_registered_provider(configuration, provider, setting):
    path = configuration.llm.tasks_config_path
    path.write_text(
        yaml.safe_dump({"tasks": {name: {"provider": provider, "model": "m"} for name, _ in WORKSPACE_TASKS}}),
        encoding="utf-8",
    )
    setattr(configuration.llm, setting, "PRIVATE_CREDENTIAL_NAME")
    before = path.read_bytes()
    environ = {"PRIVATE_CREDENTIAL_NAME": "private-value"}
    result = runtime_status(configuration, environ)
    assert result["status"] == "configured_unverified"
    assert "PRIVATE_CREDENTIAL_NAME" not in str(result)
    assert "private-value" not in str(result)
    assert environ == {"PRIVATE_CREDENTIAL_NAME": "private-value"}
    assert path.read_bytes() == before


def test_broken_model_configuration_does_not_disable_rule_guidance(configuration):
    configuration.dry_run = True
    configuration.llm.tasks_config_path.unlink()
    result = runtime_status(configuration, {})
    assert result["status"] == "rules_mode"
    assert result["mode"] == "rules"
    assert all(c["status"] == "invalid_route" for c in result["checks"])
