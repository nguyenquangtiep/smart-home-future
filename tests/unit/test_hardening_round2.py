"""Round 2 reproductions (written before the fixes).

GAP-A (ISSUE-01): a gateway ack timeout is not proof the command failed; it may have been
        delivered. It must end as unverified or verified, never as FAILED.
GAP-B (ISSUE-04): a storage error after the gateway accepted a command must not leave the
        capability stuck "busy" or lose the in-memory outcome.
GAP-E (ISSUE-04): audit sequence numbers must stay unique after a purge (memory store).
"""

import asyncio
from datetime import UTC, datetime, timedelta

from smarthome.control.audit import AuditEventIn
from smarthome.control.execution import CommandStatus, ControlService
from smarthome.control.policy import Actor, CommandRequest
from smarthome.demo_home import HOME_ID, INITIAL_STATE, build_demo_home
from smarthome.domain.capabilities import CapabilityKind, Role
from smarthome.gateway.simulator import SimulatorGateway
from smarthome.persistence.memory import MemoryCommandStore

MEMBER = Actor(user_id="user-member", home_id=HOME_ID, role=Role.MEMBER)
LIGHT_ON = CommandRequest(
    device_id="dev-living-light", capability=CapabilityKind.ON_OFF, value=True
)
LIGHT_OFF = LIGHT_ON.model_copy(update={"value": False})


async def _setup(sim, store=None, **kwargs) -> ControlService:
    home, members, devices = build_demo_home()
    control = ControlService([home], members, devices, {"simulator": sim}, store=store, **kwargs)
    for device_id, values in INITIAL_STATE.items():
        await sim.report(device_id, values)
    return control


class SlowAckGateway(SimulatorGateway):
    """Delivers the command (device reports) but its ack arrives after the dispatch timeout."""

    def __init__(self, deliver: bool) -> None:
        super().__init__(latency_s=0.02)
        self.deliver = deliver

    async def send(self, device, kind, value, correlation_id):  # type: ignore[override]
        self.sent.append((device.id, kind, value))
        if self.deliver:
            task = asyncio.create_task(self._delayed_report(device.id, kind, value, correlation_id))
            self._tasks.add(task)
        await asyncio.sleep(1.0)  # ack never arrives within dispatch_timeout_s
        return correlation_id


def test_gap_a_ack_timeout_but_delivered_is_verified_not_failed() -> None:
    async def body() -> None:
        control = await _setup(
            SlowAckGateway(deliver=True), dispatch_timeout_s=0.1, verify_timeout_s=0.5
        )
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 2)
        assert record.status is CommandStatus.SUCCEEDED

    asyncio.run(body())


def test_gap_a_ack_timeout_without_report_is_unverified_not_failed() -> None:
    async def body() -> None:
        control = await _setup(
            SlowAckGateway(deliver=False), dispatch_timeout_s=0.1, verify_timeout_s=0.2
        )
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 2)
        assert record.status is CommandStatus.TIMED_OUT
        assert any(r.code == "GATEWAY_ACK_TIMEOUT" for r in record.reasons)

    asyncio.run(body())


class FailOnceStore(MemoryCommandStore):
    """Fails the first save of a DISPATCHED record, as if the database dropped right then."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def save(self, record):  # type: ignore[override]
        if record.status is CommandStatus.DISPATCHED and not self.failed:
            self.failed = True
            raise OSError("database connection lost")
        await super().save(record)


def test_gap_b_storage_error_after_send_does_not_wedge_the_capability() -> None:
    async def body() -> None:
        store = FailOnceStore()
        control = await _setup(SimulatorGateway(latency_s=0.02), store, verify_timeout_s=0.3)
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        assert store.failed
        assert record.status is CommandStatus.SUCCEEDED
        # The durable row eventually reflects the outcome, and the capability is usable.
        assert (await store.get(record.id)).status is CommandStatus.SUCCEEDED
        nxt = await control.submit(MEMBER, LIGHT_OFF)
        await control.wait_until_terminal(nxt.id, 1)
        assert nxt.status is CommandStatus.SUCCEEDED

    asyncio.run(body())


def test_gap_e_memory_audit_seq_unique_after_purge() -> None:
    async def body() -> None:
        store = MemoryCommandStore()
        t = datetime(2026, 11, 23, tzinfo=UTC)

        def ev(at: datetime) -> AuditEventIn:
            return AuditEventIn(at=at, home_id=HOME_ID, actor_id="u", origin="ui", action="x")

        await store.append_audit(ev(t - timedelta(days=500)))
        await store.append_audit(ev(t))
        await store.purge(t - timedelta(days=90), t - timedelta(days=365))
        third = await store.append_audit(ev(t))
        seqs = [e.seq for e in await store.list_audit(HOME_ID)]
        assert len(seqs) == len(set(seqs))
        assert third.seq == 3

    asyncio.run(body())


class FailDispatchingSaveStore(MemoryCommandStore):
    def __init__(self) -> None:
        super().__init__()
        self.down = True

    async def save(self, record):  # type: ignore[override]
        if record.status is CommandStatus.DISPATCHING and self.down:
            raise OSError("database unavailable")
        await super().save(record)


def test_store_failure_before_dispatch_sends_nothing_and_does_not_wedge() -> None:
    import pytest

    from smarthome.control.execution import StoreUnavailableError

    async def body() -> None:
        store = FailDispatchingSaveStore()
        sim = SimulatorGateway(latency_s=0.02)
        control = await _setup(sim, store, verify_timeout_s=0.3)
        with pytest.raises(StoreUnavailableError):
            await control.submit(MEMBER, LIGHT_ON)
        assert sim.sent == []
        store.down = False
        retry = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(retry.id, 1)
        assert retry.status is CommandStatus.SUCCEEDED

    asyncio.run(body())


class BrokenAuditStore(MemoryCommandStore):
    async def append_audit(self, event):  # type: ignore[override]
        raise OSError("audit table unavailable")


def test_audit_write_failure_does_not_break_control() -> None:
    async def body() -> None:
        control = await _setup(
            SimulatorGateway(latency_s=0.02), BrokenAuditStore(), verify_timeout_s=0.3
        )
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.SUCCEEDED

    asyncio.run(body())
