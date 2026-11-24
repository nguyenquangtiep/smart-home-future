"""Application settings loaded from environment variables (prefix ``SMARTHOME_``) or ``.env``."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


Environment = Literal["local", "test", "staging", "production"]
# Environments where in-memory storage, the dev identity, seed-demo, OpenAPI docs and the
# simulator control route are allowed. Everything else is the strict (fail-closed) mode.
PERMISSIVE_ENVIRONMENTS: frozenset[str] = frozenset({"local", "test"})


class ConfigError(RuntimeError):
    """The configuration is unsafe or inconsistent; the application must not start."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SMARTHOME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Smart Home Future API"
    environment: Environment = "local"
    log_level: str = "INFO"
    # Reserved for a future LLM-backed node. The sample flow never needs it.
    llm_api_key: SecretStr | None = None

    # Device control. Only the built-in simulator exists today; "home_assistant" is task P0-HA.
    device_gateway: Literal["simulator"] = "simulator"
    sim_latency_s: float = 0.3
    verify_timeout_s: float = 5.0
    dispatch_timeout_s: float = 3.0
    confirmation_ttl_s: float = 120.0
    idempotency_ttl_s: float = 86_400.0
    # Allowed gateway/server clock difference when filtering stale, uncorrelated reports.
    verification_clock_skew_s: float = 1.0

    # Durable storage. Unset = in-memory (development only; refused in production).
    # Example: postgresql://smarthome:change-me@127.0.0.1:5432/smarthome
    database_url: SecretStr | None = None
    command_retention_days: int = 90
    audit_retention_days: int = 365

    @field_validator("database_url", "llm_api_key", mode="before")
    @classmethod
    def _empty_secret_is_unset(cls, value: object) -> object:
        # "SMARTHOME_DATABASE_URL=" copied from .env.example must mean "not configured", not
        # "connect with an empty DSN" (which would fall back to libpq defaults).
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Development-only identity via the X-Dev-User header. Only allowed in local/test.
    dev_identity_enabled: bool = False

    # Sessions and login (ADR 0005).
    cookie_secure: bool = True
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = ()
    trusted_proxy_ips: Annotated[tuple[str, ...], NoDecode] = ()
    session_idle_days: int = 7
    session_max_days: int = 30
    login_max_failures_email: int = 5
    login_max_failures_ip: int = 30
    login_window_s: float = 900.0

    @field_validator("allowed_origins", "trusted_proxy_ips", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return tuple(json.loads(text))
            return tuple(part.strip() for part in text.split(",") if part.strip())
        return value

    @property
    def permissive(self) -> bool:
        return self.environment in PERMISSIVE_ENVIRONMENTS

    @model_validator(mode="after")
    def _check_environment(self) -> "Settings":
        if not self.permissive:
            if self.database_url is None:
                raise ConfigError(
                    f"SMARTHOME_DATABASE_URL is required when environment is {self.environment!r}"
                )
            if self.dev_identity_enabled:
                raise ConfigError(
                    "SMARTHOME_DEV_IDENTITY_ENABLED is only allowed in local/test environments"
                )
            if not self.cookie_secure:
                raise ConfigError("SMARTHOME_COOKIE_SECURE=false is only allowed in local/test")
            if not self.allowed_origins:
                raise ConfigError(
                    f"SMARTHOME_ALLOWED_ORIGINS is required when environment is {self.environment!r}"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
