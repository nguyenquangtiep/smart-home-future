from datetime import UTC, datetime

import pytest

from smarthome.control.policy import Actor, CommandRequest, Origin, Outcome, evaluate, may_confirm
from smarthome.demo_home import HOME_ID, build_demo_home
from smarthome.domain.capabilities import CapabilityKind, Role
from smarthome.domain.state import Connectivity, Source, StateStore

NOW = datetime(2026, 11, 23, tzinfo=UTC)
_, _, DEVICES = build_demo_home()
BY_ID = {d.id: d for d in DEVICES}


def _view(device_id: str, connectivity: Connectivity | None = None):
    store = StateStore()
    store.apply(device_id, {}, Source.SIMULATED, NOW, connectivity or Connectivity.ONLINE)
    return store.view(BY_ID[device_id], NOW)


def _decide(device_id, kind, value, role=Role.MEMBER, origin=Origin.UI, home=HOME_ID, conn=None):
    actor = Actor(user_id="u", home_id=home, role=role, origin=origin)
    req = CommandRequest(device_id=device_id, capability=kind, value=value)
    return evaluate(actor, BY_ID[device_id], req, _view(device_id, conn))


def _code(decision) -> str:
    return decision.reasons[0].code


def test_allows_normal_light_command() -> None:
    assert _decide("dev-living-light", CapabilityKind.ON_OFF, True).outcome is Outcome.ALLOW


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"home": "other-home"}, "WRONG_HOME"),
        ({"role": Role.GUEST}, "FORBIDDEN_ROLE"),
        ({"conn": Connectivity.OFFLINE}, "DEVICE_OFFLINE"),
    ],
)
def test_denies(kwargs: dict, code: str) -> None:
    decision = _decide("dev-living-light", CapabilityKind.ON_OFF, True, **kwargs)
    assert decision.outcome is Outcome.DENY
    assert _code(decision) == code


def test_denies_unsupported_read_only_and_invalid() -> None:
    assert _code(_decide("dev-bedroom-light", CapabilityKind.BRIGHTNESS, 50)) == (
        "UNSUPPORTED_CAPABILITY"
    )
    assert _code(_decide("dev-living-climate", CapabilityKind.TEMPERATURE, 20)) == "READ_ONLY"
    assert _code(_decide("dev-living-ac", CapabilityKind.TARGET_TEMPERATURE, 10)) == (
        "INVALID_VALUE"
    )


def test_unlock_requires_confirmation_even_for_owner_in_ui() -> None:
    decision = _decide("dev-front-lock", CapabilityKind.LOCK, "unlocked", role=Role.OWNER)
    assert decision.outcome is Outcome.REQUIRE_CONFIRMATION
    assert _decide("dev-front-lock", CapabilityKind.LOCK, "locked").outcome is Outcome.ALLOW


def test_agent_unlock_requires_confirmation_and_automation_is_denied() -> None:
    agent = _decide("dev-front-lock", CapabilityKind.LOCK, "unlocked", origin=Origin.AGENT)
    assert agent.outcome is Outcome.REQUIRE_CONFIRMATION
    auto = _decide("dev-front-lock", CapabilityKind.LOCK, "unlocked", origin=Origin.AUTOMATION)
    assert auto.outcome is Outcome.DENY
    assert _code(auto) == "AUTOMATION_NOT_ALLOWED"


def test_water_heater_on_requires_confirmation_off_does_not() -> None:
    on = _decide("dev-water-heater", CapabilityKind.ON_OFF, True)
    off = _decide("dev-water-heater", CapabilityKind.ON_OFF, False)
    assert on.outcome is Outcome.REQUIRE_CONFIRMATION
    assert off.outcome is Outcome.ALLOW


def test_only_owner_of_same_home_may_confirm() -> None:
    lock = BY_ID["dev-front-lock"]
    assert may_confirm(Actor(user_id="o", home_id=HOME_ID, role=Role.OWNER), lock)
    assert not may_confirm(Actor(user_id="m", home_id=HOME_ID, role=Role.MEMBER), lock)
    assert not may_confirm(Actor(user_id="o", home_id="other", role=Role.OWNER), lock)
