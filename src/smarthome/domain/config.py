"""Pure validation of one home's configuration (LLD P0-06, invariants I10-I12 and friends).

The database enforces what SQL can (keys, foreign keys). This module checks the rest and is the
single place both the store and the future importer (P0-10) call, so the rules cannot drift.
"""

import math
from collections.abc import Hashable, Iterable
from dataclasses import dataclass

from smarthome.domain.models import HomeConfig

# A placement may sit this far outside the room footprint (metres): wall thickness, rounding.
FOOTPRINT_TOLERANCE_M = 0.05


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    subject: str = ""  # the offending id, e.g. a device id

    def __str__(self) -> str:
        where = f" [{self.subject}]" if self.subject else ""
        return f"{self.code}{where}: {self.message}"


class ConfigInvalidError(ValueError):
    """The configuration breaks one or more rules. ``violations`` lists every one."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__("; ".join(str(v) for v in violations))


def _duplicates(items: Iterable[Hashable]) -> list[Hashable]:
    seen: set[Hashable] = set()
    dup: list[Hashable] = []
    for item in items:
        if item in seen and item not in dup:
            dup.append(item)
        seen.add(item)
    return dup


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def check_home_config(config: HomeConfig) -> list[Violation]:
    """Return every violation (empty list = valid)."""
    out: list[Violation] = []

    def bad(code: str, message: str, subject: str = "") -> None:
        out.append(Violation(code, message, subject))

    home = config.home
    if not home.id.strip():
        bad("EMPTY_ID", "home id is empty")
    if config.version < 1:
        bad("BAD_VERSION", "version must be >= 1", home.id)

    # ---- structure ----------------------------------------------------------------------
    for dup in _duplicates(f.id for f in home.floors):
        bad("DUPLICATE_FLOOR", "floor id used twice", str(dup))
    for dup in _duplicates(f.level for f in home.floors):
        bad("DUPLICATE_LEVEL", f"two floors share level {dup}", str(dup))
    for dup in _duplicates(r.id for r in home.rooms):
        bad("DUPLICATE_ROOM", "room id used twice", str(dup))
    floors = {f.id: f for f in home.floors}
    rooms = {r.id: r for r in home.rooms}
    for floor in home.floors:
        if not floor.id.strip():
            bad("EMPTY_ID", "floor id is empty")
        if not (_finite(floor.height_m, floor.elevation_m) and floor.height_m > 0):
            bad("BAD_FLOOR_HEIGHT", "floor height must be a positive finite number", floor.id)
    for room in home.rooms:
        if not room.id.strip():
            bad("EMPTY_ID", "room id is empty")
        if room.floor_id not in floors:
            bad("UNKNOWN_FLOOR", f"room refers to missing floor {room.floor_id!r}", room.id)
        x, z, width, depth = room.footprint
        if not (_finite(x, z, width, depth) and width > 0 and depth > 0):
            bad("BAD_FOOTPRINT", "footprint needs finite values and positive size", room.id)

    # ---- gateways -----------------------------------------------------------------------
    for dup in _duplicates(g.id for g in config.gateways):
        bad("DUPLICATE_GATEWAY", "gateway id used twice", str(dup))
    gateways = {g.id: g for g in config.gateways}
    for gw in config.gateways:
        if not gw.id.strip():
            bad("EMPTY_ID", "gateway id is empty")
        if gw.home_id != home.id:
            bad("FOREIGN_GATEWAY", f"gateway belongs to home {gw.home_id!r}", gw.id)
        if gw.kind == "home_assistant" and not (gw.url and gw.secret_ref):
            bad("GATEWAY_INCOMPLETE", "home_assistant needs url and secret_ref", gw.id)
        if home.simulated and gw.kind != "simulator":
            bad("SIMULATED_HOME_REAL_GATEWAY", "a simulated home may only use simulators", gw.id)
        if not home.simulated and gw.kind == "simulator":
            bad("REAL_HOME_SIMULATOR", "a real home must not use the simulator", gw.id)

    # ---- devices ------------------------------------------------------------------------
    for dup in _duplicates(d.id for d in config.devices):
        bad("DUPLICATE_DEVICE", "device id used twice", str(dup))
    ref_owner: dict[tuple[str, str], str] = {}
    for device in config.devices:
        key = (device.gateway, device.external_ref)
        if key in ref_owner:
            bad(
                "DUPLICATE_EXTERNAL_REF",
                f"{device.gateway}/{device.external_ref} already used by {ref_owner[key]}",
                device.id,
            )
        ref_owner.setdefault(key, device.id)

    for device in config.devices:
        if not device.id.strip() or not device.external_ref.strip():
            bad("EMPTY_ID", "device id and external_ref must not be empty", device.id)
        if device.home_id != home.id:
            bad("FOREIGN_DEVICE", f"device belongs to home {device.home_id!r}", device.id)
        room = rooms.get(device.room_id)
        if room is None:
            bad("UNKNOWN_ROOM", f"device refers to missing room {device.room_id!r}", device.id)
        if device.gateway not in gateways:
            bad(
                "UNKNOWN_GATEWAY", f"device refers to missing gateway {device.gateway!r}", device.id
            )
        if not device.capabilities:
            bad("NO_CAPABILITIES", "a device needs at least one capability", device.id)
        kinds = [c.kind for c in device.capabilities]
        if len(set(kinds)) != len(kinds):
            bad("DUPLICATE_CAPABILITY", "capability kind listed twice", device.id)
        for cap in device.capabilities:
            if cap.minimum is not None and cap.maximum is not None and cap.minimum > cap.maximum:
                bad("BAD_CAPABILITY", f"{cap.kind.value}: minimum > maximum", device.id)

        placement = device.placement
        if placement is None:
            continue
        if placement.room_id != device.room_id:
            bad(
                "PLACEMENT_ROOM_MISMATCH",
                f"placement is in {placement.room_id!r}, device in {device.room_id!r}",
                device.id,
            )
            continue
        px, py, pz = placement.position
        if not (_finite(px, py, pz) and math.isfinite(placement.rotation_y_deg)):
            bad("BAD_PLACEMENT", "position and rotation must be finite", device.id)
            continue
        if room is None:
            continue
        x, z, width, depth = room.footprint
        tol = FOOTPRINT_TOLERANCE_M
        if not (x - tol <= px <= x + width + tol and z - tol <= pz <= z + depth + tol):
            bad("PLACEMENT_OUTSIDE_ROOM", f"({px:g}, {pz:g}) is outside the room", device.id)
        floor = floors.get(room.floor_id)
        if floor is not None and not (0 <= py <= floor.height_m):
            bad("PLACEMENT_HEIGHT", f"y={py:g} is outside 0..{floor.height_m:g} m", device.id)
    return out


def validate_home_config(config: HomeConfig) -> None:
    """Raise ConfigInvalidError listing every violation; return None when valid."""
    violations = check_home_config(config)
    if violations:
        raise ConfigInvalidError(violations)
