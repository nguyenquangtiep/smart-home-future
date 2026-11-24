"""Store contract, migrations, restart/recovery and cross-process concurrency.

Every contract test runs on the memory store and, when SMARTHOME_TEST_DATABASE_URL is set, on a
fresh Postgres database. "Restart" closes the store and opens a new one on the same data.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from smarthome.control.audit import AuditEventIn
from smarthome.control.commands import CommandRecord, CommandStatus
from smarthome.control.execution import ControlService
from smarthome.control.policy import Actor, CommandRequest, Origin
from smarthome.demo_home import HOME_ID, INITIAL_STATE, build_demo_home
from smarthome.domain.capabilities import CapabilityKind, Role
from smarthome.gateway.simulator import SimBehavior, SimulatorGateway
from smarthome.persistence import migrate
from smarthome.persistence.base import CommandStore, StoreConflictError
from smarthome.persistence.memory import MemoryCommandStore
from smarthome.persistence.postgres import PostgresCommandStore, SchemaVersionError, connect

OWNER = Actor(user_id="user-owner", home_id=HOME_ID, role=Role.OWNER)
MEMBER = Actor(user_id="user-member", home_id=HOME_ID, role=Role.MEMBER)
LIGHT_ON = CommandRequest(
    device_id="dev-living-light", capability=CapabilityKind.ON_OFF, value=True
)
UNLOCK = CommandRequest(
    device_id="dev-front-lock", capability=CapabilityKind.LOCK, value="unlocked"
)
T0 = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


class Harness:
    def __init__(self, kind: str, dsn: str | None) -> None:
        self.kind = kind
        self.dsn = dsn
        self._memory = MemoryCommandStore()

    async def open(self) -> CommandStore:
        if self.kind == "memory":
            return self._memory
        assert self.dsn is not None
        return await PostgresCommandStore.open(self.dsn)

    async def restart(self, store: CommandStore) -> CommandStore:
        if self.kind == "postgres":
            await store.close()
        return await self.open()


@pytest.fixture(params=["memory", "postgres"])
def harness(request: pytest.FixtureRequest) -> Harness:
    if request.param == "postgres":
        return Harness("postgres", request.getfixturevalue("pg_dsn"))
    return Harness("memory", None)


def _record(**overrides: object) -> CommandRecord:
    values: dict[str, object] = {
        "id": "c-1",
        "home_id": HOME_ID,
        "device_id": "dev-living-light",
        "capability": CapabilityKind.ON_OFF,
        "value": True,
        "actor_id": "user-member",
        "origin": Origin.UI,
        "status": CommandStatus.REQUESTED,
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return CommandRecord(**values)


async def _service(store: CommandStore, sim: SimulatorGateway | None = None) -> ControlService:
    sim = sim or SimulatorGateway(latency_s=0.02)
    home, members, devices = build_demo_home()
    return ControlService(
        [home], members, devices, {"simulator": sim}, store=store, verify_timeout_s=0.3
    )


async def _initial_sync(control: ControlService) -> None:
    sim = control.gateways["simulator"]
    assert isinstance(sim, SimulatorGateway)
    for device_id, values in INITIAL_STATE.items():
        await sim.report(device_id, values)


# ---- store contract -------------------------------------------------------------------------


def test_round_trip_preserves_value_types(harness: Harness) -> None:
    async def body() -> None:
        store = await harness.open()
        for i, value in enumerate([True, 26.5, "locked", False]):
            await store.create(_record(id=f"c-{i}", value=value), None, None, T0)
        store = await harness.restart(store)
        got = [await store.get(f"c-{i}") for i in range(4)]
        assert [g.value for g in got] == [True, 26.5, "locked", False]
        assert type(got[0].value) is bool and type(got[1].value) is float
        await store.close()

    asyncio.run(body())


def test_idempotency_reservation_hit_and_expiry(harness: Harness) -> None:
    async def body() -> None:
        store = await harness.open()
        first = _record(id="c-1", idempotency_key="k")
        assert await store.create(first, "fp1", T0 + timedelta(hours=1), T0) is None
        hit = await store.create(_record(id="c-2", idempotency_key="k"), "fp2", T0, T0)
        assert hit is not None and hit.record.id == "c-1" and hit.fingerprint == "fp1"
        assert await store.get("c-2") is None
        later = T0 + timedelta(hours=2)
        rebound = _record(id="c-3", idempotency_key="k")
        assert await store.create(rebound, "fp3", later + timedelta(hours=1), later) is None
        assert (await store.get("c-3")) is not None
        await store.close()

    asyncio.run(body())


def test_second_in_flight_command_for_same_capability_conflicts(harness: Harness) -> None:
    async def body() -> None:
        store = await harness.open()
        a, b = _record(id="a"), _record(id="b")
        await store.create(a, None, None, T0)
        await store.create(b, None, None, T0)
        a.status = CommandStatus.DISPATCHING
        await store.save(a)
        b.status = CommandStatus.DISPATCHING
        with pytest.raises(StoreConflictError):
            await store.save(b)
        await store.close()

    asyncio.run(body())


def test_claim_is_single_use_and_respects_expiry(harness: Harness) -> None:
    async def body() -> None:
        store = await harness.open()
        rec = _record(
            status=CommandStatus.AWAITING_CONFIRMATION,
            confirmation_expires_at=T0 + timedelta(minutes=2),
        )
        await store.create(rec, None, None, T0)
        args = ("c-1", CommandStatus.AWAITING_CONFIRMATION, CommandStatus.REQUESTED, "user-owner")
        late = T0 + timedelta(minutes=3)
        assert not await store.claim(*args, late)
        assert await store.claim(*args, T0)
        assert not await store.claim(*args, T0)
        assert (await store.get("c-1")).confirmed_by == "user-owner"
        await store.close()

    asyncio.run(body())


# ---- restart / recovery ---------------------------------------------------------------------


def test_restart_keeps_history_interrupts_in_flight_and_never_resends(harness: Harness) -> None:
    async def body() -> None:
        store = await harness.open()
        sim_a = SimulatorGateway(latency_s=0.02)
        control_a = await _service(store, sim_a)
        await _initial_sync(control_a)
        done = await control_a.submit(MEMBER, LIGHT_ON)
        await control_a.wait_until_terminal(done.id, 1)
        await sim_a.set_behavior("dev-living-ac", SimBehavior.SILENT)
        ac = CommandRequest(
            device_id="dev-living-ac", capability=CapabilityKind.TARGET_TEMPERATURE, value=26
        )
        in_flight = await control_a.submit(MEMBER, ac)
        waiting = await control_a.submit(MEMBER, UNLOCK)
        assert in_flight.status is CommandStatus.DISPATCHED

        # --- process "crashes" here: no timeout task completes ---
        store = await harness.restart(store)
        sim_b = SimulatorGateway(latency_s=0.02)
        control_b = await _service(store, sim_b)
        recovered = await control_b.recover()
        await _initial_sync(control_b)
        await control_b.reconcile_interrupted()

        assert [r.id for r in recovered] == [in_flight.id]
        after = await control_b.get_command(HOME_ID, in_flight.id)
        assert after.status is CommandStatus.INTERRUPTED
        codes = [r.code for r in after.reasons]
        assert codes[0] == "SERVER_RESTARTED"
        assert codes[1] == "STATE_DIFFERS_AFTER_RESTART"  # AC still reports 28, not 26
        assert sim_b.sent == []  # nothing was re-dispatched
        assert (await control_b.get_command(HOME_ID, done.id)).status is CommandStatus.SUCCEEDED
        still_waiting = await control_b.get_command(HOME_ID, waiting.id)
        assert still_waiting.status is CommandStatus.AWAITING_CONFIRMATION
        actions = [e.action for e in await control_b.list_audit(HOME_ID, limit=500)]
        assert "command.dispatched" in actions  # written by process A
        assert "command.interrupted" in actions and "command.reconciled" in actions

        # Approval still works after restart, and re-runs the policy.
        await control_b.confirm(OWNER, waiting.id, approve=True)
        final = await control_b.wait_until_terminal(waiting.id, 1)
        assert final.status is CommandStatus.SUCCEEDED
        await store.close()

    asyncio.run(body())


# ---- Postgres only --------------------------------------------------------------------------


def test_migrations_upgrade_downgrade_and_reupgrade(empty_pg_dsn: str) -> None:
    with connect(empty_pg_dsn) as conn:
        assert migrate.current_version(conn) == 0
        everything = list(range(1, migrate.latest_version() + 1))
        assert migrate.upgrade(conn) == everything
        assert migrate.current_version(conn) == migrate.latest_version()
        assert migrate.upgrade(conn) == []  # idempotent
        assert migrate.downgrade(conn, 0) == everything[::-1]
        exists = conn.execute("SELECT to_regclass('commands') IS NOT NULL AS e").fetchone()
        assert exists["e"] is False
        assert migrate.upgrade(conn) == everything


def test_app_refuses_unmigrated_database(empty_pg_dsn: str) -> None:
    with pytest.raises(SchemaVersionError):
        asyncio.run(PostgresCommandStore.open(empty_pg_dsn))


def test_audit_is_append_only_except_explicit_purge(pg_dsn: str) -> None:
    async def body() -> None:
        store = await PostgresCommandStore.open(pg_dsn)
        old = T0 - timedelta(days=400)
        await store.append_audit(
            AuditEventIn(at=old, home_id=HOME_ID, actor_id="u", origin="ui", action="x")
        )
        await store.append_audit(
            AuditEventIn(at=T0, home_id=HOME_ID, actor_id="u", origin="ui", action="y")
        )
        await store.close()
        with connect(pg_dsn) as conn:
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE audit_events SET action = 'tampered'")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("DELETE FROM audit_events")
        store = await PostgresCommandStore.open(pg_dsn)
        assert await store.purge(T0, T0 - timedelta(days=365)) == (0, 1)
        assert [e.action for e in await store.list_audit(HOME_ID)] == ["y"]
        await store.close()

    asyncio.run(body())


def test_two_processes_same_idempotency_key_dispatch_once(pg_dsn: str) -> None:
    async def body() -> None:
        store_a = await PostgresCommandStore.open(pg_dsn)
        store_b = await PostgresCommandStore.open(pg_dsn)
        sim_a, sim_b = SimulatorGateway(latency_s=0.02), SimulatorGateway(latency_s=0.02)
        a, b = await _service(store_a, sim_a), await _service(store_b, sim_b)
        await _initial_sync(a)
        await _initial_sync(b)
        results = await asyncio.gather(
            a.submit_command(MEMBER, LIGHT_ON, "shared"),
            b.submit_command(MEMBER, LIGHT_ON, "shared"),
        )
        assert results[0].record.id == results[1].record.id
        assert len(sim_a.sent) + len(sim_b.sent) == 1
        await store_a.close()
        await store_b.close()

    asyncio.run(body())


def test_two_processes_confirming_same_command_dispatch_once(pg_dsn: str) -> None:
    async def body() -> None:
        store_a = await PostgresCommandStore.open(pg_dsn)
        store_b = await PostgresCommandStore.open(pg_dsn)
        sim_a, sim_b = SimulatorGateway(latency_s=0.02), SimulatorGateway(latency_s=0.02)
        a, b = await _service(store_a, sim_a), await _service(store_b, sim_b)
        await _initial_sync(a)
        await _initial_sync(b)
        waiting = await a.submit(MEMBER, UNLOCK)
        results = await asyncio.gather(
            a.confirm(OWNER, waiting.id, approve=True),
            b.confirm(OWNER, waiting.id, approve=True),
            return_exceptions=True,
        )
        assert sum(isinstance(r, Exception) for r in results) == 1
        assert len(sim_a.sent) + len(sim_b.sent) == 1
        await store_a.close()
        await store_b.close()

    asyncio.run(body())


# ---- round 2 reproductions (Postgres) -------------------------------------------------------


def test_gap_c_store_recovers_after_connection_loss(pg_dsn: str) -> None:
    """GAP-C: after the server drops the connection, the store must reconnect on the next call
    instead of failing forever."""

    async def body() -> None:
        store = await PostgresCommandStore.open(pg_dsn)
        await store.create(_record(), None, None, T0)
        with connect(pg_dsn) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        with contextlib.suppress(psycopg.OperationalError):
            await store.get("c-1")  # may fail: the loss is only noticed on use
        assert (await store.get("c-1")) is not None
        await store.close()

    asyncio.run(body())


def test_gap_d_second_instance_on_same_database_is_refused(pg_dsn: str) -> None:
    """GAP-D: a second backend starting on the same database would run recovery and mark the
    first instance's live commands INTERRUPTED. It must refuse to start instead."""
    from fastapi.testclient import TestClient

    from smarthome.api.main import create_app
    from smarthome.core.config import Settings
    from smarthome.persistence.seed import seed_demo

    settings = Settings(
        _env_file=None,
        environment="test",
        dev_identity_enabled=True,
        sim_latency_s=0.02,
        verify_timeout_s=10,
        database_url=pg_dsn,
    )
    asyncio.run(seed_demo(settings))
    base = f"/api/v1/homes/{HOME_ID}"
    headers = {"X-Dev-User": "user-owner"}
    with TestClient(create_app(settings)) as first:
        first.put(
            f"{base}/simulator/devices/dev-living-ac/behavior",
            json={"behavior": "silent"},
            headers=headers,
        )
        live = first.post(
            f"{base}/commands",
            json={"device_id": "dev-living-ac", "capability": "target_temperature", "value": 26},
            headers=headers,
        ).json()
        assert live["status"] == "dispatched"
        second = TestClient(create_app(settings))
        with pytest.raises(RuntimeError, match="(?i)another instance"):
            second.__enter__()
        still = first.get(f"{base}/commands/{live['id']}", headers=headers).json()
        assert still["status"] == "dispatched"
