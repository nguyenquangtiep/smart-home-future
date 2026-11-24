"""Simulated pilot home: one floor, two rooms, ten devices. Development and tests only.

Matches the P0 pilot shape (one home, ~2 rooms, 8-12 devices). Every device here uses the
``simulator`` gateway, and the home is flagged ``simulated=True``.
"""

from smarthome.domain import capabilities as caps
from smarthome.domain.capabilities import CapabilityKind, Role, SafetyClass
from smarthome.domain.models import (
    Device,
    DeviceType,
    Floor,
    GatewayConnection,
    Home,
    HomeConfig,
    Member,
    Placement,
    Room,
)

HOME_ID = "home-demo"
GATEWAY_ID = "simulator"  # the demo home's one simulator instance (global ID, owned by the demo)
LIVING = "room-living"
BEDROOM = "room-bedroom"


def build_demo_home() -> tuple[Home, list[Member], list[Device]]:
    home = Home(
        id=HOME_ID,
        name="Nhà mẫu (giả lập)",
        floors=(Floor(id="floor-1", name="Tầng 1", level=1),),
        rooms=(
            Room(id=LIVING, floor_id="floor-1", name="Phòng khách", footprint=(0, 0, 6, 5)),
            Room(id=BEDROOM, floor_id="floor-1", name="Phòng ngủ", footprint=(6, 0, 4, 5)),
        ),
        simulated=True,
    )
    members = [
        Member(user_id="user-owner", home_id=HOME_ID, display_name="Chủ nhà", role=Role.OWNER),
        Member(user_id="user-member", home_id=HOME_ID, display_name="Thành viên", role=Role.MEMBER),
        Member(user_id="user-guest", home_id=HOME_ID, display_name="Khách", role=Role.GUEST),
    ]

    def dev(
        id_: str,
        room: str,
        name: str,
        type_: DeviceType,
        capabilities: tuple[caps.Capability, ...],
        pos: tuple[float, float, float],
    ) -> Device:
        return Device(
            id=id_,
            home_id=HOME_ID,
            room_id=room,
            name=name,
            type=type_,
            manufacturer="Simulator",
            model=f"sim-{type_.value}",
            capabilities=capabilities,
            placement=Placement(room_id=room, position=pos),
            gateway=GATEWAY_ID,
            external_ref=f"sim.{id_}",
        )

    devices = [
        dev(
            "dev-living-light",
            LIVING,
            "Đèn trần phòng khách",
            DeviceType.LIGHT,
            (caps.on_off(), caps.brightness()),
            (3, 2.8, 2.5),
        ),
        dev(
            "dev-living-curtain",
            LIVING,
            "Rèm phòng khách",
            DeviceType.CURTAIN,
            (caps.position(),),
            (0.1, 1.5, 2.5),
        ),
        dev(
            "dev-living-ac",
            LIVING,
            "Điều hòa phòng khách",
            DeviceType.AIR_CONDITIONER,
            (caps.on_off(), caps.target_temperature(), caps.hvac_mode()),
            (5.9, 2.4, 1.0),
        ),
        dev(
            "dev-living-tv",
            LIVING,
            "TV phòng khách",
            DeviceType.TV,
            (caps.on_off(),),
            (3, 1.2, 4.9),
        ),
        dev(
            "dev-living-climate",
            LIVING,
            "Cảm biến nhiệt ẩm phòng khách",
            DeviceType.SENSOR,
            (
                caps.sensor(CapabilityKind.TEMPERATURE, "°C"),
                caps.sensor(CapabilityKind.HUMIDITY, "%"),
            ),
            (0.2, 1.5, 4.5),
        ),
        dev(
            "dev-living-motion",
            LIVING,
            "Cảm biến hiện diện phòng khách",
            DeviceType.SENSOR,
            (caps.sensor(CapabilityKind.OCCUPANCY, None, max_age_s=600),),
            (5.8, 2.6, 4.8),
        ),
        dev(
            "dev-front-lock",
            LIVING,
            "Khóa cửa chính",
            DeviceType.LOCK,
            (caps.lock(),),
            (0, 1.0, 0.8),
        ),
        dev(
            "dev-bedroom-light",
            BEDROOM,
            "Đèn phòng ngủ",
            DeviceType.LIGHT,
            (caps.on_off(),),
            (8, 2.8, 2.5),
        ),
        dev(
            "dev-bedroom-ac",
            BEDROOM,
            "Điều hòa phòng ngủ",
            DeviceType.AIR_CONDITIONER,
            (caps.on_off(), caps.target_temperature(minimum=18, maximum=28)),
            (9.9, 2.4, 2.5),
        ),
        dev(
            "dev-water-heater",
            BEDROOM,
            "Bình nóng lạnh",
            DeviceType.WATER_HEATER,
            (
                caps.on_off(
                    safety=SafetyClass.HIGH_POWER, min_role=Role.MEMBER, safe_values=(False,)
                ),
            ),
            (9.5, 2.0, 4.8),
        ),
    ]
    return home, members, devices


def build_demo_config() -> tuple[HomeConfig, list[Member]]:
    """The demo home as a ``HomeConfig`` (what the config store holds) plus its members."""
    home, members, devices = build_demo_home()
    gateway = GatewayConnection(id=GATEWAY_ID, home_id=HOME_ID, kind="simulator", name="Simulator")
    config = HomeConfig(home=home, version=1, gateways=(gateway,), devices=tuple(devices))
    return config, members


INITIAL_STATE: dict[str, dict[CapabilityKind, bool | float | str]] = {
    "dev-living-light": {CapabilityKind.ON_OFF: False, CapabilityKind.BRIGHTNESS: 80},
    "dev-living-curtain": {CapabilityKind.POSITION: 100},
    "dev-living-ac": {
        CapabilityKind.ON_OFF: False,
        CapabilityKind.TARGET_TEMPERATURE: 28,
        CapabilityKind.HVAC_MODE: "cool",
    },
    "dev-living-tv": {CapabilityKind.ON_OFF: False},
    "dev-living-climate": {CapabilityKind.TEMPERATURE: 30.5, CapabilityKind.HUMIDITY: 70},
    "dev-living-motion": {CapabilityKind.OCCUPANCY: True},
    "dev-front-lock": {CapabilityKind.LOCK: "locked"},
    "dev-bedroom-light": {CapabilityKind.ON_OFF: False},
    "dev-bedroom-ac": {CapabilityKind.ON_OFF: False, CapabilityKind.TARGET_TEMPERATURE: 27},
    "dev-water-heater": {CapabilityKind.ON_OFF: False},
}
