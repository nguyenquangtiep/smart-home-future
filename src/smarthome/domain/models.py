"""Home structure: home, floors, rooms, members and devices (with their 3D placement)."""

from enum import StrEnum
from typing import Literal

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
    gateway: str  # ID of the gateway *instance* (GatewayConnection.id), not the adapter name
    external_ref: str  # the device's id inside that gateway instance

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


GatewayKind = Literal["simulator", "home_assistant"]


class GatewayConnection(_Frozen):
    """One configured gateway instance. Owned by exactly one home; its ``id`` is global.

    ``Device.gateway`` holds this ID, so two homes that both use Home Assistant never share a
    key. ``secret_ref`` names an environment variable or secret, never the secret itself.
    """

    id: str
    home_id: str
    kind: GatewayKind
    name: str
    url: str | None = None
    secret_ref: str | None = None
    enabled: bool = True


class HomeConfig(_Frozen):
    """One home as one consistent snapshot: structure, gateways and active devices."""

    home: Home
    version: int = 1
    gateways: tuple[GatewayConnection, ...]
    devices: tuple[Device, ...]  # active devices only; each carries its placement
