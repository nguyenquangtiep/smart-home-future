"""S1-02: validate_home_config (AC-06-7, AC-06-13)."""

import math
from collections.abc import Callable

import pytest

from smarthome.demo_home import BEDROOM, GATEWAY_ID, HOME_ID, LIVING, build_demo_config
from smarthome.domain.capabilities import CapabilityKind, on_off
from smarthome.domain.config import (
    FOOTPRINT_TOLERANCE_M,
    ConfigInvalidError,
    check_home_config,
    validate_home_config,
)
from smarthome.domain.models import (
    Floor,
    GatewayConnection,
    HomeConfig,
    Placement,
    Room,
)


def demo() -> HomeConfig:
    return build_demo_config()[0]


def codes(config: HomeConfig) -> set[str]:
    return {v.code for v in check_home_config(config)}


def with_device(config: HomeConfig, device_id: str, **changes: object) -> HomeConfig:
    devices = tuple(
        d.model_copy(update=changes) if d.id == device_id else d for d in config.devices
    )
    return config.model_copy(update={"devices": devices})


def with_home(config: HomeConfig, **changes: object) -> HomeConfig:
    return config.model_copy(update={"home": config.home.model_copy(update=changes)})


def test_demo_home_is_valid() -> None:
    validate_home_config(demo())


def test_error_lists_every_violation() -> None:
    config = with_device(demo(), "dev-living-light", room_id="nowhere", gateway="nope")
    with pytest.raises(ConfigInvalidError) as excinfo:
        validate_home_config(config)
    found = {v.code for v in excinfo.value.violations}
    assert {"UNKNOWN_ROOM", "UNKNOWN_GATEWAY"} <= found
    assert "dev-living-light" in str(excinfo.value)


def test_duplicate_device_id() -> None:
    config = demo()
    dup = config.devices[0].model_copy(update={"external_ref": "sim.other"})
    assert "DUPLICATE_DEVICE" in codes(
        config.model_copy(update={"devices": (*config.devices, dup)})
    )


def test_duplicate_gateway_external_ref() -> None:
    config = with_device(demo(), "dev-living-tv", external_ref="sim.dev-living-light")
    assert "DUPLICATE_EXTERNAL_REF" in codes(config)


def test_same_external_ref_on_different_gateway_instances_is_allowed() -> None:
    config = demo()
    second = GatewayConnection(id="gw-2", home_id=HOME_ID, kind="simulator", name="Second")
    config = config.model_copy(update={"gateways": (*config.gateways, second)})
    config = with_device(
        config, "dev-living-tv", gateway="gw-2", external_ref="sim.dev-living-light"
    )
    validate_home_config(config)


def test_duplicate_floor_room_level() -> None:
    config = demo()
    floor = Floor(id="floor-1", name="again", level=1)
    room = Room(id=LIVING, floor_id="floor-1", name="again", footprint=(0, 0, 1, 1))
    home = config.home.model_copy(
        update={"floors": (*config.home.floors, floor), "rooms": (*config.home.rooms, room)}
    )
    assert {"DUPLICATE_FLOOR", "DUPLICATE_LEVEL", "DUPLICATE_ROOM"} <= codes(
        config.model_copy(update={"home": home})
    )


def test_room_needs_existing_floor() -> None:
    config = demo()
    room = Room(id="room-x", floor_id="floor-9", name="x", footprint=(0, 0, 1, 1))
    home = config.home.model_copy(update={"rooms": (*config.home.rooms, room)})
    assert "UNKNOWN_FLOOR" in codes(config.model_copy(update={"home": home}))


@pytest.mark.parametrize(
    "footprint",
    [(0, 0, 0, 5), (0, 0, 5, -1), (0, 0, math.nan, 5), (math.inf, 0, 5, 5)],
)
def test_bad_footprint(footprint: tuple[float, float, float, float]) -> None:
    config = demo()
    room = Room(id="room-x", floor_id="floor-1", name="x", footprint=footprint)
    home = config.home.model_copy(update={"rooms": (*config.home.rooms, room)})
    assert "BAD_FOOTPRINT" in codes(config.model_copy(update={"home": home}))


@pytest.mark.parametrize("height", [0.0, -3.0, math.nan])
def test_bad_floor_height(height: float) -> None:
    config = demo()
    floor = config.home.floors[0].model_copy(update={"height_m": height})
    assert "BAD_FLOOR_HEIGHT" in codes(with_home(config, floors=(floor,)))


def test_foreign_home_objects() -> None:
    config = demo()
    gw = config.gateways[0].model_copy(update={"home_id": "other"})
    assert "FOREIGN_GATEWAY" in codes(config.model_copy(update={"gateways": (gw,)}))
    assert "FOREIGN_DEVICE" in codes(with_device(config, "dev-living-tv", home_id="other"))


def test_device_needs_capabilities_without_duplicates() -> None:
    config = demo()
    assert "NO_CAPABILITIES" in codes(with_device(config, "dev-living-tv", capabilities=()))
    twice = (on_off(), on_off())
    assert "DUPLICATE_CAPABILITY" in codes(with_device(config, "dev-living-tv", capabilities=twice))


def test_capability_range_must_be_ordered() -> None:
    config = demo()
    device = next(d for d in config.devices if d.id == "dev-living-ac")
    caps = tuple(
        c.model_copy(update={"minimum": 30, "maximum": 20})
        if c.kind is CapabilityKind.TARGET_TEMPERATURE
        else c
        for c in device.capabilities
    )
    assert "BAD_CAPABILITY" in codes(with_device(config, device.id, capabilities=caps))


def test_placement_must_be_in_the_devices_room() -> None:
    placement = Placement(room_id=BEDROOM, position=(8, 1, 2))
    assert "PLACEMENT_ROOM_MISMATCH" in codes(
        with_device(demo(), "dev-living-tv", placement=placement)
    )


@pytest.mark.parametrize(
    ("position", "ok"),
    [
        ((3.0, 1.0, 2.0), True),
        ((0.0, 0.0, 0.0), True),  # exact corner
        ((6.0, 3.0, 5.0), True),  # opposite corner and exact ceiling
        ((6.0 + FOOTPRINT_TOLERANCE_M, 1.0, 2.0), True),  # on the tolerance edge
        ((6.06, 1.0, 2.0), False),  # 6 cm outside
        ((-0.06, 1.0, 2.0), False),
        ((3.0, 1.0, 5.06), False),
        ((3.0, 3.01, 2.0), False),  # above the floor height
        ((3.0, -0.01, 2.0), False),  # below the floor
        ((math.nan, 1.0, 2.0), False),
        ((3.0, math.inf, 2.0), False),
    ],
)
def test_placement_bounds(position: tuple[float, float, float], ok: bool) -> None:
    placement = Placement(room_id=LIVING, position=position)
    found = codes(with_device(demo(), "dev-living-tv", placement=placement))
    assert (not found) is ok, found


def test_nan_rotation_is_rejected() -> None:
    placement = Placement(room_id=LIVING, position=(3, 1, 2), rotation_y_deg=math.nan)
    assert "BAD_PLACEMENT" in codes(with_device(demo(), "dev-living-tv", placement=placement))


def test_device_without_placement_is_valid() -> None:
    validate_home_config(with_device(demo(), "dev-living-tv", placement=None))


def test_home_assistant_gateway_needs_url_and_secret_ref() -> None:
    base = demo()
    home = with_home(base, simulated=False)
    for kwargs, expected in [
        ({}, True),
        ({"url": "http://ha.local:8123"}, True),
        ({"secret_ref": "HA_TOKEN"}, True),
        ({"url": "http://ha.local:8123", "secret_ref": "HA_TOKEN"}, False),
    ]:
        gw = GatewayConnection(
            id="gw-ha", home_id=HOME_ID, kind="home_assistant", name="HA", **kwargs
        )
        config = home.model_copy(update={"gateways": (gw,)})
        config = config.model_copy(update={"devices": ()})
        assert ("GATEWAY_INCOMPLETE" in codes(config)) is expected


@pytest.mark.parametrize(
    ("simulated", "kind", "expected"),
    [
        (True, "simulator", None),
        (True, "home_assistant", "SIMULATED_HOME_REAL_GATEWAY"),
        (False, "simulator", "REAL_HOME_SIMULATOR"),
        (False, "home_assistant", None),
    ],
)
def test_simulated_flag_matches_gateway_kind(
    simulated: bool, kind: str, expected: str | None
) -> None:
    gw = GatewayConnection(
        id="gw-x",
        home_id=HOME_ID,
        kind=kind,
        name="x",  # type: ignore[arg-type]
        url="http://ha.local",
        secret_ref="HA_TOKEN",
    )
    config = HomeConfig(
        home=demo().home.model_copy(update={"simulated": simulated}), gateways=(gw,), devices=()
    )
    found = codes(config)
    assert (expected in found) if expected else not found


def test_real_home_cannot_keep_simulator_devices() -> None:
    assert "REAL_HOME_SIMULATOR" in codes(with_home(demo(), simulated=False))


def test_empty_ids_are_rejected() -> None:
    config = with_device(demo(), "dev-living-tv", external_ref=" ")
    assert "EMPTY_ID" in codes(config)


@pytest.mark.parametrize("version", [0, -1])
def test_version_must_be_positive(version: int) -> None:
    assert "BAD_VERSION" in codes(demo().model_copy(update={"version": version}))


_Mutator = Callable[[HomeConfig], HomeConfig]


def test_gateway_id_is_not_the_adapter_name() -> None:
    """A device must point at a configured instance; the adapter kind alone is not enough."""
    config = demo()
    gw = config.gateways[0].model_copy(update={"id": "gw-demo-sim"})
    config = config.model_copy(update={"gateways": (gw,)})
    assert "UNKNOWN_GATEWAY" in codes(config)  # devices still say GATEWAY_ID
    assert GATEWAY_ID != "gw-demo-sim"
