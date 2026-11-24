"""Execution engine against the simulator. Async tests run via asyncio.run (no extra plugin)."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from smarthome.control.execution import CommandStatus, ConfirmationError, ControlService
from smarthome.control.policy import Actor, CommandRequest, Origin
from smarthome.demo_home import HOME_ID, INITIAL_STATE, build_demo_home
from smarthome.domain.capabilities import CapabilityKind, Role
from smarthome.domain.state import DisplayStatus, Source
from smarthome.gateway.simulator import SimBehavior, SimulatorGateway

OWNER = Actor(user_id="user-owner", home_id=HOME_ID, role=Role.OWNER)
MEMBER = Actor(user_id="user-member", home_id=HOME_ID, role=Role.MEMBER)
LIGHT_ON = CommandRequest(
    device_id="dev-living-light", capability=CapabilityKind.ON_OFF, value=True
)
UNLOCK = CommandRequest(
    device_id="dev-front-lock", capability=CapabilityKind.LOCK, value="unlocked"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def _setup(clock=None) -> tuple[ControlService, SimulatorGateway]:
    kwargs = {"clock": clock} if clock else {}
    sim = SimulatorGateway(latency_s=0.02, **kwargs)
    home, members, devices = build_demo_home()
    control = ControlService(
        [home], members, devices, {"simulator": sim}, verify_timeout_s=0.2, **kwargs
    )
    for device_id, values in INITIAL_STATE.items():
        await sim.report(device_id, values)
    return control, sim


def run(test: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(test())


def _light_cap(control: ControlService):
    view = control.view(control.devices["dev-living-light"])
    return next(c for c in view.capabilities if c.kind is CapabilityKind.ON_OFF)


def test_success_only_after_matching_report() -> None:
    async def body() -> None:
        control, sim = await _setup()
        record = await control.submit(MEMBER, LIGHT_ON)
        assert record.status is CommandStatus.DISPATCHED
        cap = _light_cap(control)
        assert cap.status is DisplayStatus.PENDING and cap.value is False
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.SUCCEEDED
        assert record.verified_value is True
        cap = _light_cap(control)
        assert cap.status is DisplayStatus.SIMULATED and cap.value is True
        assert cap.source is Source.SIMULATED

    run(body)


def test_silent_device_times_out_and_keeps_old_state() -> None:
    async def body() -> None:
        control, sim = await _setup()
        await sim.set_behavior("dev-living-light", SimBehavior.SILENT)
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.TIMED_OUT
        assert record.reasons[0].code == "NOT_VERIFIED"
        cap = _light_cap(control)
        assert cap.value is False and cap.status is DisplayStatus.SIMULATED

    run(body)


@pytest.mark.parametrize(
    ("behavior", "status", "code"),
    [
        (SimBehavior.ERROR, CommandStatus.FAILED, "GATEWAY_ERROR"),
        (SimBehavior.OFFLINE, CommandStatus.REJECTED, "DEVICE_OFFLINE"),
    ],
)
def test_gateway_failures(behavior: SimBehavior, status: CommandStatus, code: str) -> None:
    async def body() -> None:
        control, sim = await _setup()
        await sim.set_behavior("dev-living-light", behavior)
        record = await control.submit(MEMBER, LIGHT_ON)
        assert record.status is status
        assert record.reasons[0].code == code
        assert sim.sent == []

    run(body)


def test_idempotency_key_returns_same_command() -> None:
    async def body() -> None:
        control, sim = await _setup()
        first = await control.submit(MEMBER, LIGHT_ON, idempotency_key="k1")
        second = await control.submit(MEMBER, LIGHT_ON, idempotency_key="k1")
        assert first.id == second.id
        assert len(sim.sent) == 1

    run(body)


def test_second_command_while_in_flight_is_rejected_busy() -> None:
    async def body() -> None:
        control, sim = await _setup()
        await sim.set_behavior("dev-living-light", SimBehavior.SILENT)
        await control.submit(MEMBER, LIGHT_ON)
        second = await control.submit(MEMBER, LIGHT_ON)
        assert second.status is CommandStatus.REJECTED
        assert second.reasons[0].code == "DEVICE_BUSY"

    run(body)


def test_unlock_waits_for_owner_confirmation() -> None:
    async def body() -> None:
        control, sim = await _setup()
        record = await control.submit(MEMBER, UNLOCK)
        assert record.status is CommandStatus.AWAITING_CONFIRMATION
        assert sim.sent == []
        with pytest.raises(ConfirmationError) as exc:
            await control.confirm(MEMBER, record.id, approve=True)
        assert exc.value.code == "FORBIDDEN_ROLE"
        await control.confirm(OWNER, record.id, approve=True)
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.SUCCEEDED
        assert record.confirmed_by == "user-owner"

    run(body)


def test_owner_can_reject_confirmation() -> None:
    async def body() -> None:
        control, sim = await _setup()
        record = await control.submit(MEMBER, UNLOCK)
        await control.confirm(OWNER, record.id, approve=False)
        assert record.status is CommandStatus.CANCELLED
        assert sim.sent == []
        with pytest.raises(ConfirmationError):
            await control.confirm(OWNER, record.id, approve=True)

    run(body)


def test_confirmation_expires() -> None:
    async def body() -> None:
        clock = FakeClock()
        control, sim = await _setup(clock)
        record = await control.submit(MEMBER, UNLOCK)
        clock.now += timedelta(seconds=121)
        with pytest.raises(ConfirmationError):
            await control.confirm(OWNER, record.id, approve=True)
        assert record.status is CommandStatus.EXPIRED
        assert sim.sent == []

    run(body)


def test_confirmation_rechecks_policy() -> None:
    async def body() -> None:
        control, sim = await _setup()
        record = await control.submit(MEMBER, UNLOCK)
        await sim.set_behavior("dev-front-lock", SimBehavior.OFFLINE)
        await control.confirm(OWNER, record.id, approve=True)
        assert record.status is CommandStatus.REJECTED
        assert record.reasons[0].code == "DEVICE_OFFLINE"

    run(body)


def test_automation_cannot_unlock() -> None:
    async def body() -> None:
        control, _ = await _setup()
        auto = OWNER.model_copy(update={"origin": Origin.AUTOMATION})
        record = await control.submit(auto, UNLOCK)
        assert record.status is CommandStatus.REJECTED

    run(body)


def test_audit_records_full_lifecycle() -> None:
    async def body() -> None:
        control, _ = await _setup()
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        events = await control.list_audit(HOME_ID)
        actions = [e.action for e in events if e.command_id == record.id]
        assert actions[::-1] == ["command.requested", "command.dispatched", "command.succeeded"]

    run(body)
