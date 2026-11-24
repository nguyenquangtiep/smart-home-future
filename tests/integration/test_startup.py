"""S1-04: startup from the database, seed-demo, restart, invalid data (AC-06-9, 10, 11)."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from smarthome.api.main import create_app
from smarthome.bootstrap import StartupError
from smarthome.control.commands import CommandRecord, CommandStatus
from smarthome.control.policy import Origin
from smarthome.core.config import Settings, get_settings
from smarthome.demo_home import HOME_ID
from smarthome.domain.capabilities import CapabilityKind
from smarthome.persistence import __main__ as cli
from smarthome.persistence.base import InstanceLockError
from smarthome.persistence.config_store import PostgresHomeConfigStore
from smarthome.persistence.postgres import PostgresCommandStore, connect
from smarthome.persistence.seed import SeedRefusedError, seed_demo

OWNER = {"X-Dev-User": "user-owner"}
DEVICES = f"/api/v1/homes/{HOME_ID}/devices"


def settings_for(dsn: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "dev_identity_enabled": True,
        "database_url": dsn,
        "sim_latency_s": 0.02,
        "verify_timeout_s": 0.3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def seed(dsn: str) -> str:
    return asyncio.run(seed_demo(settings_for(dsn)))


def test_seed_demo_is_refused_outside_local_and_test() -> None:
    strict = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql://u:p@127.0.0.1:1/x",
        allowed_origins=("https://home.example",),
    )
    with pytest.raises(SeedRefusedError, match="production"):
        asyncio.run(seed_demo(strict))


def test_seed_demo_needs_a_database() -> None:
    with pytest.raises(SeedRefusedError, match="DATABASE_URL"):
        asyncio.run(seed_demo(Settings(_env_file=None, environment="local")))


def test_seed_is_idempotent(pg_dsn: str) -> None:
    assert seed(pg_dsn) == "created"
    first = _load(pg_dsn)
    assert seed(pg_dsn) == "exists"
    second = _load(pg_dsn)
    assert first == second
    assert first[0].version == 1 and len(first[0].devices) == 10


def _load(dsn: str):  # noqa: ANN202
    async def go():  # noqa: ANN202
        store = await PostgresHomeConfigStore.open(dsn)
        try:
            return await store.load_all()
        finally:
            await store.close()

    return asyncio.run(go())


def test_seed_is_refused_while_the_application_runs(pg_dsn: str) -> None:
    seed(pg_dsn)
    with TestClient(create_app(settings_for(pg_dsn))), pytest.raises(InstanceLockError):
        seed(pg_dsn)
    assert seed(pg_dsn) == "exists"  # lock released after shutdown


def test_empty_database_refuses_to_start_instead_of_using_the_demo_home(pg_dsn: str) -> None:
    """AC-06-9: no silent fallback to simulated data."""
    with (
        pytest.raises(StartupError, match="no home is configured"),
        TestClient(create_app(settings_for(pg_dsn))),
    ):
        pass


def test_application_serves_the_home_stored_in_the_database(pg_dsn: str) -> None:
    seed(pg_dsn)
    with TestClient(create_app(settings_for(pg_dsn))) as client:
        resp = client.get(DEVICES, headers=OWNER)
        assert resp.status_code == 200
        assert len(resp.json()) == 10
        home = client.get(f"/api/v1/homes/{HOME_ID}", headers=OWNER).json()["home"]
        assert home["simulated"] is True


def test_device_removed_in_the_database_is_not_served(pg_dsn: str) -> None:
    seed(pg_dsn)
    with connect(pg_dsn) as conn:
        conn.execute("DELETE FROM device_placements WHERE device_id = 'dev-living-tv'")
        conn.execute("UPDATE devices SET removed_at = now() WHERE id = 'dev-living-tv'")
    with TestClient(create_app(settings_for(pg_dsn))) as client:
        ids = {d["id"] for d in client.get(DEVICES, headers=OWNER).json()}
        assert "dev-living-tv" not in ids and len(ids) == 9


def test_restart_keeps_the_configuration(pg_dsn: str) -> None:
    """AC-06-11 (configuration part)."""
    seed(pg_dsn)
    before = _load(pg_dsn)
    for _ in range(2):
        with TestClient(create_app(settings_for(pg_dsn))) as client:
            assert client.get(DEVICES, headers=OWNER).status_code == 200
    assert _load(pg_dsn) == before


def test_restart_interrupts_unfinished_commands_and_never_resends_them(pg_dsn: str) -> None:
    """AC-06-11 (command part): a DISPATCHED command from the previous run is closed out as
    INTERRUPTED and nothing is sent to any gateway."""
    seed(pg_dsn)
    now = datetime.now(UTC)

    async def leave_one_behind() -> None:
        store = await PostgresCommandStore.open(pg_dsn)
        record = CommandRecord(
            id="c-left-behind",
            home_id=HOME_ID,
            device_id="dev-living-light",
            capability=CapabilityKind.ON_OFF,
            value=True,
            actor_id="user-member",
            origin=Origin.UI,
            status=CommandStatus.DISPATCHED,
            created_at=now,
            updated_at=now,
            dispatched_at=now,
        )
        await store.create(record, None, None, now)
        await store.close()

    asyncio.run(leave_one_behind())
    with TestClient(create_app(settings_for(pg_dsn))) as client:
        control = client.app.state.control  # type: ignore[attr-defined]
        body = client.get(f"/api/v1/homes/{HOME_ID}/commands/c-left-behind", headers=OWNER).json()
        assert body["status"] == "interrupted"
        assert body["reasons"][0]["code"] == "SERVER_RESTARTED"
        assert all(gw.sent == [] for gw in control.gateways.values())


def test_unfinished_command_of_a_removed_device_is_reconciled_as_unknown(pg_dsn: str) -> None:
    """AC-06-11: the device is gone from the configuration, so its state cannot be compared."""
    seed(pg_dsn)
    now = datetime.now(UTC)

    async def leave_one_behind() -> None:
        store = await PostgresCommandStore.open(pg_dsn)
        record = CommandRecord(
            id="c-removed-device",
            home_id=HOME_ID,
            device_id="dev-living-tv",
            capability=CapabilityKind.ON_OFF,
            value=True,
            actor_id="user-member",
            origin=Origin.UI,
            status=CommandStatus.DISPATCHING,
            created_at=now,
            updated_at=now,
        )
        await store.create(record, None, None, now)
        await store.close()

    asyncio.run(leave_one_behind())
    with connect(pg_dsn) as conn:
        conn.execute("DELETE FROM device_placements WHERE device_id = 'dev-living-tv'")
        conn.execute("UPDATE devices SET removed_at = now() WHERE id = 'dev-living-tv'")
    with TestClient(create_app(settings_for(pg_dsn))) as client:
        body = client.get(
            f"/api/v1/homes/{HOME_ID}/commands/c-removed-device", headers=OWNER
        ).json()
        assert body["status"] == "interrupted"
        codes = [r["code"] for r in body["reasons"]]
        assert codes == ["SERVER_RESTARTED", "STATE_UNKNOWN_AFTER_RESTART"]


def test_corrupt_capability_json_refuses_startup_and_names_the_device(pg_dsn: str) -> None:
    """AC-06-9."""
    seed(pg_dsn)
    with connect(pg_dsn) as conn:
        conn.execute(
            'UPDATE devices SET capabilities = \'[{"kind": "bogus"}]\''
            " WHERE id = 'dev-front-lock'"
        )
    with (
        pytest.raises(StartupError, match="dev-front-lock"),
        TestClient(create_app(settings_for(pg_dsn))),
    ):
        pass


def test_placement_outside_the_room_refuses_startup(pg_dsn: str) -> None:
    seed(pg_dsn)
    with connect(pg_dsn) as conn:
        conn.execute("UPDATE device_placements SET pos_x = 99 WHERE device_id = 'dev-living-tv'")
    with (
        pytest.raises(StartupError, match="PLACEMENT_OUTSIDE_ROOM"),
        TestClient(create_app(settings_for(pg_dsn))),
    ):
        pass


def test_unimplemented_gateway_kind_refuses_startup(pg_dsn: str) -> None:
    seed(pg_dsn)
    # Turn the demo into a "real" home with a Home Assistant gateway: not supported yet.
    with connect(pg_dsn) as conn:
        conn.execute(
            "UPDATE gateway_connections SET kind = 'home_assistant',"
            " url = 'http://ha.local', secret_ref = 'HA_TOKEN'"
        )
        conn.execute("UPDATE homes SET simulated = false")
    with (
        pytest.raises(StartupError, match="not implemented"),
        TestClient(create_app(settings_for(pg_dsn))),
    ):
        pass


@pytest.fixture
def cli_env(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("SMARTHOME_DATABASE_URL", pg_dsn)
    monkeypatch.setenv("SMARTHOME_ENVIRONMENT", "test")
    get_settings.cache_clear()
    yield pg_dsn
    get_settings.cache_clear()


def test_cli_seed_demo_exit_codes(cli_env: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["seed-demo"]) == 0
    assert "created" in capsys.readouterr().out
    assert cli.main(["seed-demo"]) == 0
    assert "exists" in capsys.readouterr().out


def test_cli_seed_demo_refuses_while_the_app_runs(
    cli_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["seed-demo"])
    with TestClient(create_app(settings_for(cli_env))):
        assert cli.main(["seed-demo"]) == 2
    assert "application is running" in capsys.readouterr().err


def test_cli_seed_demo_refuses_in_production(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMARTHOME_DATABASE_URL", pg_dsn)
    monkeypatch.setenv("SMARTHOME_ENVIRONMENT", "production")
    monkeypatch.setenv("SMARTHOME_ALLOWED_ORIGINS", "https://home.example")
    get_settings.cache_clear()
    try:
        assert cli.main(["seed-demo"]) == 2
    finally:
        get_settings.cache_clear()
    assert "only allowed in local/test" in capsys.readouterr().err
