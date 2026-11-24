from datetime import UTC, datetime, timedelta

import pytest

from smarthome.demo_home import build_demo_home
from smarthome.domain import capabilities as caps
from smarthome.domain.capabilities import CapabilityKind
from smarthome.domain.state import (
    Connectivity,
    DisplayStatus,
    PendingCommand,
    Source,
    StateStore,
)

T0 = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cap", "value", "ok"),
    [
        (caps.on_off(), True, True),
        (caps.on_off(), 1, False),  # numbers are not booleans
        (caps.brightness(), 50, True),
        (caps.brightness(), 0, False),
        (caps.brightness(), 101, False),
        (caps.brightness(), True, False),  # bool is not a number
        (caps.target_temperature(), 26.5, True),
        (caps.target_temperature(), "26", False),
        (caps.lock(), "unlocked", True),
        (caps.lock(), "open", False),
    ],
)
def test_validate_value(cap: caps.Capability, value: object, ok: bool) -> None:
    assert (cap.validate_value(value) is None) is ok


def test_matches_uses_step_tolerance_and_types() -> None:
    temp = caps.target_temperature()
    assert temp.matches(26, 26.2)
    assert not temp.matches(26, 26.5)
    assert caps.on_off().matches(True, True)
    assert not caps.on_off().matches(True, 1)


def test_confirmation_values() -> None:
    assert caps.lock().requires_confirmation("unlocked")
    assert not caps.lock().requires_confirmation("locked")


def test_same_type_devices_may_differ_in_capabilities() -> None:
    _, _, devices = build_demo_home()
    by_id = {d.id: d for d in devices}
    assert by_id["dev-living-ac"].capability(CapabilityKind.HVAC_MODE) is not None
    assert by_id["dev-bedroom-ac"].capability(CapabilityKind.HVAC_MODE) is None


def _light():
    _, _, devices = build_demo_home()
    return next(d for d in devices if d.id == "dev-living-light")


def _status(view, kind=CapabilityKind.ON_OFF):
    return next(c for c in view.capabilities if c.kind is kind)


def test_view_unknown_before_any_report() -> None:
    view = StateStore().view(_light(), T0)
    assert view.connectivity is Connectivity.UNKNOWN
    assert _status(view).status is DisplayStatus.UNKNOWN
    assert _status(view).value is None


def test_view_distinguishes_simulated_and_reported() -> None:
    store = StateStore()
    store.apply("dev-living-light", {CapabilityKind.ON_OFF: True}, Source.SIMULATED, T0)
    assert _status(store.view(_light(), T0)).status is DisplayStatus.SIMULATED
    store.apply("dev-living-light", {CapabilityKind.ON_OFF: True}, Source.REPORTED, T0)
    assert _status(store.view(_light(), T0)).status is DisplayStatus.CONFIRMED


def test_pending_keeps_last_confirmed_value() -> None:
    store = StateStore()
    store.apply("dev-living-light", {CapabilityKind.ON_OFF: False}, Source.REPORTED, T0)
    pending = {CapabilityKind.ON_OFF: PendingCommand(command_id="c1", value=True)}
    cap = _status(store.view(_light(), T0, pending))
    assert cap.status is DisplayStatus.PENDING
    assert cap.value is False  # still the last reported value
    assert cap.pending_value is True


def test_offline_overrides_everything() -> None:
    store = StateStore()
    store.apply("dev-living-light", {CapabilityKind.ON_OFF: True}, Source.REPORTED, T0)
    store.apply("dev-living-light", {}, Source.REPORTED, T0, Connectivity.OFFLINE)
    cap = _status(store.view(_light(), T0))
    assert cap.status is DisplayStatus.OFFLINE
    assert cap.value is True  # last known value is kept, but labelled offline


def test_sensor_report_goes_stale() -> None:
    _, _, devices = build_demo_home()
    sensor = next(d for d in devices if d.id == "dev-living-climate")
    store = StateStore()
    store.apply(sensor.id, {CapabilityKind.TEMPERATURE: 30.0}, Source.REPORTED, T0)
    fresh = store.view(sensor, T0 + timedelta(minutes=5))
    old = store.view(sensor, T0 + timedelta(minutes=20))
    assert _status(fresh, CapabilityKind.TEMPERATURE).status is DisplayStatus.CONFIRMED
    assert _status(old, CapabilityKind.TEMPERATURE).status is DisplayStatus.STALE


def test_out_of_order_report_is_ignored() -> None:
    store = StateStore()
    store.apply("dev-living-light", {CapabilityKind.ON_OFF: True}, Source.REPORTED, T0)
    store.apply(
        "dev-living-light",
        {CapabilityKind.ON_OFF: False},
        Source.REPORTED,
        T0 - timedelta(seconds=1),
    )
    assert store.observation("dev-living-light", CapabilityKind.ON_OFF).value is True
