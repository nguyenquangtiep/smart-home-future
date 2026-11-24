"""Reproductions of the hardening issues (ISSUE-01..03). Written before the fixes (RED)."""

import asyncio
from datetime import timedelta

import pytest

from smarthome.control.execution import CommandStatus, ControlService
from smarthome.control.policy import Actor, CommandRequest, Outcome, evaluate
from smarthome.demo_home import HOME_ID, INITIAL_STATE, build_demo_home
from smarthome.domain import capabilities as caps
from smarthome.domain.capabilities import CapabilityKind, Role, SafetyClass
from smarthome.domain.models import Device, DeviceType
from smarthome.domain.state import Connectivity, Source, StateStore, utc_now
from smarthome.gateway.base import StateReport
from smarthome.gateway.simulator import SimBehavior, SimulatorGateway

MEMBER = Actor(user_id="user-member", home_id=HOME_ID, role=Role.MEMBER)
LIGHT_ON = CommandRequest(
    device_id="dev-living-light", capability=CapabilityKind.ON_OFF, value=True
)


async def _setup() -> tuple[ControlService, SimulatorGateway]:
    sim = SimulatorGateway(latency_s=0.02)
    home, members, devices = build_demo_home()
    control = ControlService([home], members, devices, {"simulator": sim}, verify_timeout_s=0.2)
    for device_id, values in INITIAL_STATE.items():
        await sim.report(device_id, values)
    return control, sim


# ---- ISSUE-02 -------------------------------------------------------------------------------


def _device(cap: caps.Capability) -> Device:
    return Device(
        id="dev-x",
        home_id=HOME_ID,
        room_id="room-living",
        name="Thiết bị X",
        type=DeviceType.PLUG,
        manufacturer="t",
        model="t",
        capabilities=(cap,),
        gateway="simulator",
        external_ref="x",
    )


def _decide(cap: caps.Capability, value: object) -> Outcome:
    device = _device(cap)
    store = StateStore()
    store.apply(device.id, {}, Source.REPORTED, utc_now(), Connectivity.ONLINE)
    request = CommandRequest(device_id=device.id, capability=cap.kind, value=value)
    return evaluate(MEMBER, device, request, store.view(device, utc_now())).outcome


def test_issue02_high_power_without_confirm_values_is_not_silently_allowed() -> None:
    heater = caps.on_off(safety=SafetyClass.HIGH_POWER)  # confirm_values forgotten
    assert _decide(heater, True) is Outcome.REQUIRE_CONFIRMATION


def test_issue02_security_without_confirm_values_is_not_silently_allowed() -> None:
    camera = caps.on_off(safety=SafetyClass.SECURITY)  # turning a camera off
    assert _decide(camera, False) is Outcome.REQUIRE_CONFIRMATION


# ---- ISSUE-01 -------------------------------------------------------------------------------


def test_issue01_report_observed_before_dispatch_does_not_verify() -> None:
    async def body() -> None:
        control, sim = await _setup()
        await sim.set_behavior("dev-living-light", SimBehavior.SILENT)
        before = utc_now() - timedelta(seconds=30)
        record = await control.submit(MEMBER, LIGHT_ON)
        # A delayed report that describes the light *before* the command was sent.
        await control.on_report(
            StateReport(
                device_id="dev-living-light",
                values={CapabilityKind.ON_OFF: True},
                source=Source.SIMULATED,
                observed_at=before,
            )
        )
        assert record.status is not CommandStatus.SUCCEEDED

    asyncio.run(body())


def test_issue01_out_of_order_report_ignored_by_store_does_not_verify() -> None:
    async def body() -> None:
        control, sim = await _setup()
        await sim.set_behavior("dev-living-light", SimBehavior.SILENT)
        record = await control.submit(MEMBER, LIGHT_ON)
        t_old = utc_now()
        t_new = t_old + timedelta(seconds=1)
        report = {"device_id": "dev-living-light", "source": Source.SIMULATED}
        # Newer report says OFF, then an older report saying ON arrives late.
        await control.on_report(
            StateReport(**report, values={CapabilityKind.ON_OFF: False}, observed_at=t_new)
        )
        await control.on_report(
            StateReport(**report, values={CapabilityKind.ON_OFF: True}, observed_at=t_old)
        )
        assert control.state.observation("dev-living-light", CapabilityKind.ON_OFF).value is False
        assert record.status is not CommandStatus.SUCCEEDED

    asyncio.run(body())


# ---- ISSUE-03 -------------------------------------------------------------------------------


def test_issue03_same_key_different_payload_is_not_treated_as_retry() -> None:
    async def body() -> None:
        control, _ = await _setup()
        await control.submit(MEMBER, LIGHT_ON, idempotency_key="k")
        other = CommandRequest(
            device_id="dev-front-lock", capability=CapabilityKind.LOCK, value="unlocked"
        )
        with pytest.raises(Exception, match="(?i)idempotency"):
            await control.submit(MEMBER, other, idempotency_key="k")

    asyncio.run(body())
