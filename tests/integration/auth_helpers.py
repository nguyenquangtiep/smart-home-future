"""Shared helpers for the authentication tests (not a test module)."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from smarthome.auth import passwords
from smarthome.auth.service import AuthService
from smarthome.auth.store import PostgresAuthStore
from smarthome.core.config import Settings
from smarthome.demo_home import HOME_ID
from smarthome.persistence.postgres import connect
from smarthome.persistence.seed import seed_demo

OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct-horse-battery-staple"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def settings_for(dsn: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": dsn,
        "sim_latency_s": 0.02,
        "verify_timeout_s": 2.0,
        "cookie_secure": True,
        "allowed_origins": ("https://home.example",),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@dataclass
class Stack:
    dsn: str
    settings: Settings
    clock: FakeClock

    def conn(self) -> Any:
        return connect(self.dsn)

    def rows(self, sql: str, *params: object) -> list[dict[str, Any]]:
        with connect(self.dsn) as conn:
            return conn.execute(sql, params or None).fetchall()  # type: ignore[arg-type]

    def new_store(self) -> PostgresAuthStore:
        return run(PostgresAuthStore.open(self.dsn))

    def new_service(self, store: PostgresAuthStore | None = None) -> AuthService:
        return AuthService(store or self.new_store(), self.settings, self.clock)

    def create_user(
        self,
        email: str = OWNER_EMAIL,
        password: str = OWNER_PASSWORD,
        role: str = "owner",
        home_id: str = HOME_ID,
    ) -> str:
        """Create a user with a membership directly (what the CLI and members API do)."""
        store = self.new_store()
        hashed = run(passwords.hash_password(password))
        user_id = run(store.create_owner(home_id, email, email.split("@")[0], hashed, self.clock()))
        if role != "owner":
            with connect(self.dsn) as conn:
                conn.execute("UPDATE memberships SET role = %s WHERE user_id = %s", (role, user_id))
        run(store.close())
        return user_id


def make_stack(dsn: str, **setting_overrides: object) -> Stack:
    settings = settings_for(dsn, **setting_overrides)
    run(seed_demo(settings))
    return Stack(dsn, settings, FakeClock())


@pytest.fixture
def stack(pg_dsn: str) -> Iterator[Stack]:
    yield make_stack(pg_dsn)


# ---- API-level helpers ----------------------------------------------------------------------

ORIGIN = "https://home.example"
SECOND_HOME = "home-b"


def add_second_home(dsn: str) -> None:
    """A second home with the same room ids and its own gateway and devices."""
    from smarthome.demo_home import build_demo_config
    from smarthome.domain.models import GatewayConnection
    from smarthome.persistence.config_store import PostgresHomeConfigStore

    config = build_demo_config()[0]
    gateway = GatewayConnection(
        id=f"gw-{SECOND_HOME}", home_id=SECOND_HOME, kind="simulator", name="B"
    )
    devices = tuple(
        d.model_copy(
            update={"id": f"{SECOND_HOME}-{d.id}", "home_id": SECOND_HOME, "gateway": gateway.id}
        )
        for d in config.devices
    )
    other = config.model_copy(
        update={
            "home": config.home.model_copy(update={"id": SECOND_HOME, "name": "B"}),
            "gateways": (gateway,),
            "devices": devices,
        }
    )

    async def go() -> None:
        store = await PostgresHomeConfigStore.open(dsn)
        await store.replace_home(other, None)
        await store.close()

    run(go())
