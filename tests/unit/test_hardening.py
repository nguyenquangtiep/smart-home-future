"""Regression tests for the 2026-11 hardening (verification, safety, approval, idempotency)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from smarthome.control.commands import Verification
from smarthome.control.execution import (
    CommandStatus,
    ConfirmationError,
    ControlService,
    IdempotencyConflictError,
    NotFoundError,
)
from smarthome.control.policy import Actor, CommandRequest, Origin, Outcome, evaluate
from smarthome.demo_home import HOME_ID, INITIAL_STATE, build_demo_home
from smarthome.domain import capabilities as caps
from smarthome.domain.capabilities import CapabilityKind, Role, SafetyClass
from smarthome.domain.models import Device
from smarthome.domain.state import Connectivity, Source, StateStore, utc_now
from smarthome.gateway.base import StateReport
from smarthome.gateway.simulator import SimBehavior, SimulatorGateway

OWNER = Actor(user_id="user-owner", home_id=HOME_ID, role=Role.OWNER)
MEMBER = Actor(user_id="user-member", home_id=HOME_ID, role=Role.MEMBER)
OTHER_OWNER = Actor(user_id="user-owner", home_id="home-other", role=Role.OWNER)
LIGHT = "dev-living-light"
LIGHT_ON = CommandRequest(device_id=LIGHT, capability=CapabilityKind.ON_OFF, value=True)
UNLOCK = CommandRequest(
    device_id="dev-front-lock", capability=CapabilityKind.LOCK, value="unlocked"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


async def _setup(sim: SimulatorGateway | None = None, **kwargs) -> tuple[ControlService, object]:
    sim = sim or SimulatorGateway(latency_s=0.02)
    home, members, devices = build_demo_home()
    control = ControlService(
        [home], members, devices, {"simulator": sim}, verify_timeout_s=0.2, **kwargs
    )
    for device_id, values in INITIAL_STATE.items():
        await sim.report(device_id, values)
    return control, sim


def run(coro_fn) -> None:
    asyncio.run(coro_fn())


def _report(value: object, observed_at: datetime | None = None, corr: str | None = None):
    return StateReport(
        device_id=LIGHT,
        values={CapabilityKind.ON_OFF: value},
        source=Source.SIMULATED,
        observed_at=observed_at or utc_now(),
        correlation_id=corr,
    )


# ---- ISSUE-01: verification -----------------------------------------------------------------


def test_correlated_report_verifies_with_correlated_label() -> None:
    async def body() -> None:
        control, _ = await _setup()
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.SUCCEEDED
        assert record.verification is Verification.CORRELATED
        assert record.correlation_id == record.id

    run(body)


def test_without_correlation_fresh_report_verifies_as_state_match() -> None:
    async def body() -> None:
        control, _ = await _setup(SimulatorGateway(latency_s=0.02, correlate=False))
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.SUCCEEDED
        assert record.verification is Verification.STATE_MATCH

    run(body)


def test_report_with_foreign_correlation_does_not_verify() -> None:
    async def body() -> None:
        control, sim = await _setup()
        await sim.set_behavior(LIGHT, SimBehavior.SILENT)
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.on_report(_report(True, corr="some-other-command"))
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.TIMED_OUT

    run(body)


def test_non_matching_report_keeps_waiting_then_times_out() -> None:
    async def body() -> None:
        control, sim = await _setup()
        await sim.set_behavior(LIGHT, SimBehavior.SILENT)
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.on_report(_report(False))
        assert record.status is CommandStatus.DISPATCHED
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.TIMED_OUT
        assert record.verified_value is None

    run(body)


def test_duplicate_report_after_success_changes_nothing() -> None:
    async def body() -> None:
        control, _ = await _setup()
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        completed = record.completed_at
        await control.on_report(_report(True, corr=record.id))
        assert record.status is CommandStatus.SUCCEEDED
        assert record.completed_at == completed
        events = await control.list_audit(HOME_ID)
        assert sum(e.action == "command.succeeded" for e in events) == 1

    run(body)


def test_report_within_clock_skew_still_verifies() -> None:
    async def body() -> None:
        # The whole gateway runs 0.5 s behind the server: inside the 1 s allowance.
        lagging = SimulatorGateway(
            latency_s=0.02,
            correlate=False,
            clock=lambda: utc_now() - timedelta(seconds=0.5),
        )
        control, _ = await _setup(lagging)
        record = await control.submit(MEMBER, LIGHT_ON)
        await control.wait_until_terminal(record.id, 1)
        assert record.status is CommandStatus.SUCCEEDED
        assert record.verification is Verification.STATE_MATCH

    run(body)


class EarlyReportGateway(SimulatorGateway):
    """Reports with its OWN correlation id before send() returns it (Home Assistant style)."""

    async def send(self, device, kind, value, correlation_id):  # type: ignore[override]
        gateway_id = "ha-context-123"
        self.sent.append((device.id, kind, value))
        await self.report(device.id, {kind: value}, correlation_id=gateway_id)
        return gateway_id


def test_gateway_correlation_arriving_before_send_returns_is_adopted() -> None:
    async def body() -> None:
        control, _ = await _setup(EarlyReportGateway(latency_s=0.02))
        record = await control.submit(MEMBER, LIGHT_ON)
        assert record.status is CommandStatus.SUCCEEDED
        assert record.verification is Verification.CORRELATED
        assert record.correlation_id == "ha-context-123"

    run(body)


def test_out_of_order_connectivity_is_ignored() -> None:
    store = StateStore()
    t = datetime(2026, 11, 23, tzinfo=UTC)
    store.apply(LIGHT, {}, Source.REPORTED, t, Connectivity.ONLINE)
    store.apply(LIGHT, {}, Source.REPORTED, t - timedelta(seconds=5), Connectivity.OFFLINE)
    assert store.connectivity(LIGHT) is Connectivity.ONLINE


# ---- ISSUE-02: safety policy and approval ---------------------------------------------------


def _decide(cap: caps.Capability, value: object, origin: Origin = Origin.UI) -> Outcome:
    _, _, devices = build_demo_home()
    device: Device = devices[0].model_copy(update={"capabilities": (cap,)})
    store = StateStore()
    store.apply(device.id, {}, Source.REPORTED, utc_now(), Connectivity.ONLINE)
    actor = MEMBER.model_copy(update={"origin": origin})
    req = CommandRequest(device_id=device.id, capability=cap.kind, value=value)
    return evaluate(actor, device, req, store.view(device, utc_now())).outcome


def test_declared_safe_value_of_dangerous_capability_is_allowed() -> None:
    heater = caps.on_off(safety=SafetyClass.HIGH_POWER, safe_values=(False,))
    assert _decide(heater, False) is Outcome.ALLOW
    assert _decide(heater, True) is Outcome.REQUIRE_CONFIRMATION


def test_safe_values_are_type_strict() -> None:
    heater = caps.on_off(safety=SafetyClass.HIGH_POWER, safe_values=(False,))
    assert heater.requires_confirmation(0)  # 0 is not False


def test_automation_cannot_run_unconfirmable_dangerous_action() -> None:
    heater = caps.on_off(safety=SafetyClass.HIGH_POWER)
    assert _decide(heater, True, Origin.AUTOMATION) is Outcome.DENY


def test_confirm_values_ignored_for_dangerous_class_cannot_weaken_it() -> None:
    camera = caps.on_off(safety=SafetyClass.SECURITY, confirm_values=(False,))
    assert _decide(camera, True) is Outcome.REQUIRE_CONFIRMATION


def test_owner_of_other_home_cannot_confirm() -> None:
    async def body() -> None:
        control, sim = await _setup()
        record = await control.submit(MEMBER, UNLOCK)
        with pytest.raises(NotFoundError):
            await control.confirm(OTHER_OWNER, record.id, approve=True)
        assert record.status is CommandStatus.AWAITING_CONFIRMATION
        assert sim.sent == []

    run(body)


def test_approval_cannot_be_replayed() -> None:
    async def body() -> None:
        control, sim = await _setup()
        record = await control.submit(MEMBER, UNLOCK)
        await control.confirm(OWNER, record.id, approve=True)
        with pytest.raises(ConfirmationError) as exc:
            await control.confirm(OWNER, record.id, approve=True)
        assert exc.value.code == "NOT_AWAITING"
        assert len(sim.sent) == 1

    run(body)


def test_concurrent_approvals_dispatch_once() -> None:
    async def body() -> None:
        control, sim = await _setup()
        record = await control.submit(MEMBER, UNLOCK)
        results = await asyncio.gather(
            control.confirm(OWNER, record.id, approve=True),
            control.confirm(OWNER, record.id, approve=True),
            return_exceptions=True,
        )
        assert sum(isinstance(r, ConfirmationError) for r in results) == 1
        assert len(sim.sent) == 1

    run(body)


def test_approval_of_one_command_does_not_release_another() -> None:
    async def body() -> None:
        control, sim = await _setup()
        first = await control.submit(MEMBER, UNLOCK)
        heater = CommandRequest(
            device_id="dev-water-heater", capability=CapabilityKind.ON_OFF, value=True
        )
        second = await control.submit(MEMBER, heater)
        await control.confirm(OWNER, first.id, approve=True)
        assert second.status is CommandStatus.AWAITING_CONFIRMATION
        assert [s[0] for s in sim.sent] == ["dev-front-lock"]

    run(body)


# ---- ISSUE-03: idempotency ------------------------------------------------------------------


def test_same_key_same_request_is_replayed() -> None:
    async def body() -> None:
        control, sim = await _setup()
        first = await control.submit_command(MEMBER, LIGHT_ON, "k1")
        second = await control.submit_command(MEMBER, LIGHT_ON, "k1")
        assert not first.replayed and second.replayed
        assert second.record.id == first.record.id
        assert len(sim.sent) == 1

    run(body)


def test_same_key_different_value_or_origin_conflicts() -> None:
    async def body() -> None:
        control, sim = await _setup()
        await control.submit(MEMBER, LIGHT_ON, "k1")
        off = LIGHT_ON.model_copy(update={"value": False})
        with pytest.raises(IdempotencyConflictError):
            await control.submit(MEMBER, off, "k1")
        agent = MEMBER.model_copy(update={"origin": Origin.AGENT})
        with pytest.raises(IdempotencyConflictError):
            await control.submit(agent, LIGHT_ON, "k1")
        assert len(sim.sent) == 1

    run(body)


def test_true_and_one_do_not_share_a_fingerprint() -> None:
    async def body() -> None:
        control, _ = await _setup()
        await control.submit(MEMBER, LIGHT_ON, "k1")
        numeric = CommandRequest.model_construct(
            device_id=LIGHT, capability=CapabilityKind.ON_OFF, value=1
        )
        with pytest.raises(IdempotencyConflictError):
            await control.submit(MEMBER, numeric, "k1")

    run(body)


def test_keys_are_scoped_per_actor() -> None:
    async def body() -> None:
        control, sim = await _setup()
        a = await control.submit(MEMBER, LIGHT_ON, "same")
        await control.wait_until_terminal(a.id, 1)
        b = await control.submit(OWNER, LIGHT_ON, "same")
        assert a.id != b.id

    run(body)


def test_concurrent_requests_with_same_key_create_one_command() -> None:
    async def body() -> None:
        control, sim = await _setup()
        results = await asyncio.gather(
            *(control.submit_command(MEMBER, LIGHT_ON, "race") for _ in range(5))
        )
        assert len({r.record.id for r in results}) == 1
        assert sum(not r.replayed for r in results) == 1
        assert len(sim.sent) == 1

    run(body)


def test_retrying_dangerous_command_does_not_create_a_second_approval() -> None:
    async def body() -> None:
        control, _ = await _setup()
        first = await control.submit(MEMBER, UNLOCK, "unlock-1")
        retry = await control.submit(MEMBER, UNLOCK, "unlock-1")
        assert retry.id == first.id
        assert retry.status is CommandStatus.AWAITING_CONFIRMATION

    run(body)


def test_expired_key_is_rebound_to_a_new_command() -> None:
    async def body() -> None:
        clock = FakeClock()
        control, _ = await _setup(
            SimulatorGateway(latency_s=0.02, clock=clock), clock=clock, idempotency_ttl_s=60
        )
        first = await control.submit(MEMBER, LIGHT_ON, "k")
        await control.wait_until_terminal(first.id, 1)
        clock.now += timedelta(seconds=61)
        second = await control.submit(MEMBER, LIGHT_ON.model_copy(update={"value": False}), "k")
        assert second.id != first.id

    run(body)
