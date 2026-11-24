"""S1-01: the environment gate is a fail-closed allowlist (ADR 0005, D-07-9; closes DR-H03)."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from smarthome.api.main import create_app
from smarthome.core.config import ConfigError, Settings

DSN = "postgresql://user:pw@127.0.0.1:1/none"
STRICT = {
    "database_url": DSN,
    "allowed_origins": ("https://home.example",),
}


def make(**values: object) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("env", ["local", "test"])
def test_permissive_environments_need_nothing(env: str) -> None:
    settings = make(environment=env)
    assert settings.permissive
    assert settings.database_url is None


@pytest.mark.parametrize("env", ["", "prod", "docker", "Production", "dev", "stage", "LOCAL"])
def test_unknown_environment_is_rejected(env: str) -> None:
    with pytest.raises(ValidationError):
        make(environment=env)


@pytest.mark.parametrize("env", ["staging", "production"])
class TestStrictEnvironments:
    def test_database_is_required(self, env: str) -> None:
        with pytest.raises(ConfigError, match="DATABASE_URL"):
            make(environment=env, allowed_origins=("https://home.example",))

    def test_dev_identity_is_forbidden(self, env: str) -> None:
        with pytest.raises(ConfigError, match="DEV_IDENTITY"):
            make(environment=env, dev_identity_enabled=True, **STRICT)

    def test_insecure_cookie_is_forbidden(self, env: str) -> None:
        with pytest.raises(ConfigError, match="COOKIE_SECURE"):
            make(environment=env, cookie_secure=False, **STRICT)

    def test_allowed_origins_are_required(self, env: str) -> None:
        with pytest.raises(ConfigError, match="ALLOWED_ORIGINS"):
            make(environment=env, database_url=DSN)

    def test_valid_strict_settings_are_not_permissive(self, env: str) -> None:
        assert not make(environment=env, **STRICT).permissive


def test_environment_is_read_from_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTHOME_ENVIRONMENT", "docker")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("SMARTHOME_ENVIRONMENT", "production")
    with pytest.raises(ConfigError):
        Settings(_env_file=None)


def test_origin_and_proxy_lists_accept_comma_separated_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMARTHOME_ALLOWED_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("SMARTHOME_TRUSTED_PROXY_IPS", '["10.0.0.1", "10.0.0.0/8"]')
    settings = Settings(_env_file=None)
    assert settings.allowed_origins == ("https://a.example", "https://b.example")
    assert settings.trusted_proxy_ips == ("10.0.0.1", "10.0.0.0/8")


@pytest.mark.parametrize(
    ("env", "expected"),
    [("local", 200), ("test", 200)],
)
def test_docs_and_simulator_route_exist_only_in_permissive_environments(
    env: str, expected: int
) -> None:
    with TestClient(create_app(make(environment=env, dev_identity_enabled=True))) as client:
        assert client.get("/docs").status_code == expected
        assert client.get("/openapi.json").status_code == expected
        paths = client.get("/openapi.json").json()["paths"]
    assert any("/simulator/" in p for p in paths)


def test_strict_app_hides_docs_and_simulator_route() -> None:
    settings = make(environment="staging", **STRICT)
    app = create_app(settings)  # no lifespan: no database is contacted
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any("/simulator/" in p for p in paths)
    for hidden in ("/docs", "/redoc", "/openapi.json"):
        assert hidden not in paths
    assert app.openapi_url is None
