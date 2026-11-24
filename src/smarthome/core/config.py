"""Application settings loaded from environment variables (prefix ``SMARTHOME_``) or ``.env``."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SMARTHOME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Smart Home Future API"
    environment: str = "local"
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

    # Development-only identity via the X-Dev-User header, until real auth (task P0-AUTH).
    # Refused at startup when environment is "production".
    dev_identity_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
