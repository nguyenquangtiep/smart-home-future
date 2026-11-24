"""Home structure: home, floors, rooms, members and devices (with their 3D placement)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from smarthome.domain.capabilities import Capability, CapabilityKind, Role

Vec3 = tuple[float, float, float]


class DeviceType(StrEnum):
    LIGHT = "light"
    CURTAIN = "curtain"
    AIR_CONDITIONER = "air_conditioner"
    TV = "tv"
    FAN = "fan"
    PLUG = "plug"
    SENSOR = "sensor"
    LOCK = "lock"
    CAMERA = "camera"
    ENERGY_METER = "energy_meter"
    WATER_HEATER = "water_heater"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Floor(_Frozen):
    id: str
    name: str
    level: int
    elevation_m: float = 0.0
    height_m: float = 3.0


class Room(_Frozen):
    id: str
    floor_id: str
    name: str
    # Footprint on the floor plane, metres: x, z of one corner, then width (x) and depth (z).
    footprint: tuple[float, float, float, float]


class Placement(_Frozen):
    """Where the device sits in the 3D model. Purely presentational, never used for control."""

    room_id: str
    position: Vec3
    rotation_y_deg: float = 0.0


class Device(_Frozen):
    id: str  # stable system ID, independent of the gateway's own entity id
    home_id: str
    room_id: str
    name: str
    type: DeviceType
    manufacturer: str
    model: str
    capabilities: tuple[Capability, ...]
    placement: Placement | None = None
    gateway: str  # which gateway adapter controls it, e.g. "simulator", "home_assistant"
    external_ref: str  # the device's id inside that gateway

    def capability(self, kind: CapabilityKind) -> Capability | None:
        return next((c for c in self.capabilities if c.kind is kind), None)


class Member(_Frozen):
    user_id: str
    home_id: str
    display_name: str
    role: Role


class Home(_Frozen):
    id: str
    name: str
    floors: tuple[Floor, ...]
    rooms: tuple[Room, ...]
    # True when the home and its devices come from the built-in simulator seed.
    simulated: bool = False
