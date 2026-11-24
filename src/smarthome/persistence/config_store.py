"""Home configuration store: Postgres is the source of truth, YAML is import/export only.

Contract (LLD P0-06 section 6): ``load_all`` returns one consistent snapshot per home;
``replace_home`` writes one home in one transaction under optimistic concurrency. Both stores
below follow it, so tests and local development behave like production.
"""

from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from smarthome.domain.capabilities import Capability
from smarthome.domain.config import ConfigInvalidError, Violation, validate_home_config
from smarthome.domain.models import (
    Device,
    DeviceType,
    Floor,
    GatewayConnection,
    Home,
    HomeConfig,
    Placement,
    Room,
)
from smarthome.persistence.pgconn import Conn, PgConnection


class ConfigVersionConflict(Exception):
    """``expected_version`` does not match the stored version; nothing was written."""

    def __init__(self, expected: int | None, actual: int | None) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected config version {expected}, stored version is {actual}")


class ConfigConflictError(Exception):
    """The write breaks an identity rule (ID owned by another home, removed device ID reuse,
    deleting a room, floor or gateway that still has devices)."""


class HomeConfigStore(Protocol):
    async def load_all(self) -> list[HomeConfig]:
        """Every home as one consistent snapshot. Raises ConfigInvalidError on bad data."""
        ...

    async def replace_home(self, config: HomeConfig, expected_version: int | None) -> int:
        """Make the stored home equal ``config`` and return the new version.

        ``expected_version=None`` creates the home (conflict if it exists); otherwise it must
        equal the stored version. Devices missing from ``config`` are soft-deleted.
        """
        ...

    async def close(self) -> None: ...


# ---- in-memory ----------------------------------------------------------------------------


class MemoryHomeConfigStore:
    """Same contract as Postgres, in memory. Used when no database is configured (local/test)."""

    def __init__(self, configs: list[HomeConfig] | None = None) -> None:
        self._homes: dict[str, HomeConfig] = {}
        # Every device id ever used (kept after removal: ids are never reused).
        self._device_home: dict[str, str] = {}
        self._device_room: dict[str, str] = {}
        self._device_gateway: dict[str, str] = {}
        self._removed: set[str] = set()
        self._gateway_owner: dict[str, str] = {}
        for config in configs or []:
            validate_home_config(config)
            self._apply(config, version=config.version, removed=set())

    async def load_all(self) -> list[HomeConfig]:
        return list(self._homes.values())

    async def replace_home(self, config: HomeConfig, expected_version: int | None) -> int:
        validate_home_config(config)
        home_id = config.home.id
        current = self._homes.get(home_id)
        if expected_version is None:
            if current is not None:
                raise ConfigVersionConflict(None, current.version)
        elif current is None or current.version != expected_version:
            raise ConfigVersionConflict(expected_version, current.version if current else None)

        for gw in config.gateways:
            if self._gateway_owner.get(gw.id, home_id) != home_id:
                raise ConfigConflictError(f"gateway id {gw.id} belongs to another home")
        for d in config.devices:
            if d.id in self._removed or self._device_home.get(d.id, home_id) != home_id:
                raise ConfigConflictError(
                    f"device id {d.id} belongs to another home or was removed"
                )
        newly_removed: set[str] = set()
        if current is not None:
            kept = {d.id for d in config.devices}
            newly_removed = {d.id for d in current.devices if d.id not in kept}
            rooms_left = {r.id for r in config.home.rooms}
            gateways_left = {g.id for g in config.gateways}
            for did, owner in self._device_home.items():
                if owner != home_id or did in kept:
                    continue
                if self._device_room[did] not in rooms_left:
                    raise ConfigConflictError(f"room {self._device_room[did]} still has devices")
                if self._device_gateway[did] not in gateways_left:
                    raise ConfigConflictError(
                        f"gateway {self._device_gateway[did]} still has devices"
                    )

        version = (current.version if current else 0) + 1
        self._apply(config, version=version, removed=newly_removed)
        return version

    def _apply(self, config: HomeConfig, version: int, removed: set[str]) -> None:
        home_id = config.home.id
        self._removed |= removed
        for gw in config.gateways:
            self._gateway_owner[gw.id] = home_id
        for d in config.devices:
            self._device_home[d.id] = home_id
            self._device_room[d.id] = d.room_id
            self._device_gateway[d.id] = d.gateway
        self._homes[home_id] = config.model_copy(update={"version": version})

    async def close(self) -> None:
        return None


# ---- PostgreSQL ---------------------------------------------------------------------------


class PostgresHomeConfigStore:
    def __init__(self, pg: PgConnection) -> None:
        self._pg = pg

    @classmethod
    async def open(cls, dsn: str) -> "PostgresHomeConfigStore":
        return cls(await PgConnection.open(dsn, min_version=2))

    async def close(self) -> None:
        await self._pg.close()

    async def load_all(self) -> list[HomeConfig]:
        raw = await self._pg.run(_read_all, isolation="REPEATABLE READ")
        return _assemble(raw)

    async def replace_home(self, config: HomeConfig, expected_version: int | None) -> int:
        validate_home_config(config)

        def tx(conn: Conn) -> int:
            return _write_home(conn, config, expected_version)

        try:
            return await self._pg.run(tx)
        except (
            psycopg.errors.RestrictViolation,
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.UniqueViolation,
        ) as exc:
            raise ConfigConflictError(str(exc).splitlines()[0]) from exc


def _read_all(conn: Conn) -> dict[str, Any]:
    def rows(sql: str) -> list[dict[str, Any]]:
        return conn.execute(sql).fetchall()  # type: ignore[arg-type]

    return {
        "homes": rows("SELECT id, name, simulated, config_version FROM homes ORDER BY id"),
        "floors": rows(
            "SELECT home_id, id, name, level, elevation_m, height_m FROM floors"
            " ORDER BY home_id, level"
        ),
        "rooms": rows(
            "SELECT home_id, id, floor_id, name, footprint FROM rooms ORDER BY home_id, id"
        ),
        "gateways": rows(
            "SELECT id, home_id, kind, name, url, secret_ref, enabled"
            " FROM gateway_connections ORDER BY id"
        ),
        "devices": rows(
            "SELECT d.id, d.home_id, d.room_id, d.gateway_id, d.external_ref, d.name, d.type,"
            " d.manufacturer, d.model, d.capabilities, p.room_id AS p_room,"
            " p.pos_x, p.pos_y, p.pos_z, p.rotation_y_deg"
            " FROM devices d LEFT JOIN device_placements p ON p.device_id = d.id"
            " WHERE d.removed_at IS NULL ORDER BY d.created_at, d.id"
        ),
    }


def _assemble(raw: dict[str, Any]) -> list[HomeConfig]:
    violations: list[Violation] = []
    devices_by_home: dict[str, list[Device]] = {}
    for row in raw["devices"]:
        try:
            capabilities = tuple(Capability.model_validate(c) for c in row["capabilities"])
            placement = (
                Placement(
                    room_id=row["p_room"],
                    position=(row["pos_x"], row["pos_y"], row["pos_z"]),
                    rotation_y_deg=row["rotation_y_deg"],
                )
                if row["p_room"] is not None
                else None
            )
            device = Device(
                id=row["id"],
                home_id=row["home_id"],
                room_id=row["room_id"],
                name=row["name"],
                type=DeviceType(row["type"]),
                manufacturer=row["manufacturer"],
                model=row["model"],
                capabilities=capabilities,
                placement=placement,
                gateway=row["gateway_id"],
                external_ref=row["external_ref"],
            )
        except (ValidationError, ValueError) as exc:
            violations.append(Violation("INVALID_DEVICE_DATA", str(exc).splitlines()[0], row["id"]))
            continue
        devices_by_home.setdefault(row["home_id"], []).append(device)
    if violations:
        raise ConfigInvalidError(violations)

    configs = []
    for h in raw["homes"]:
        hid = h["id"]
        home = Home(
            id=hid,
            name=h["name"],
            simulated=h["simulated"],
            floors=tuple(
                Floor(
                    id=f["id"],
                    name=f["name"],
                    level=f["level"],
                    elevation_m=f["elevation_m"],
                    height_m=f["height_m"],
                )
                for f in raw["floors"]
                if f["home_id"] == hid
            ),
            rooms=tuple(
                Room(id=r["id"], floor_id=r["floor_id"], name=r["name"], footprint=r["footprint"])
                for r in raw["rooms"]
                if r["home_id"] == hid
            ),
        )
        gateways = tuple(
            GatewayConnection(
                id=g["id"],
                home_id=g["home_id"],
                kind=g["kind"],
                name=g["name"],
                url=g["url"],
                secret_ref=g["secret_ref"],
                enabled=g["enabled"],
            )
            for g in raw["gateways"]
            if g["home_id"] == hid
        )
        config = HomeConfig(
            home=home,
            version=h["config_version"],
            gateways=gateways,
            devices=tuple(devices_by_home.get(hid, [])),
        )
        validate_home_config(config)
        configs.append(config)
    return configs


def _write_home(conn: Conn, config: HomeConfig, expected_version: int | None) -> int:
    home = config.home
    now = datetime.now(UTC)
    if expected_version is None:
        created = conn.execute(
            "INSERT INTO homes (id, name, simulated) VALUES (%s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING RETURNING config_version",
            (home.id, home.name, home.simulated),
        ).fetchone()
        if created is None:
            row = conn.execute(
                "SELECT config_version FROM homes WHERE id = %s", (home.id,)
            ).fetchone()
            raise ConfigVersionConflict(None, row["config_version"] if row else None)
        new_version = created["config_version"]
    else:
        row = conn.execute(
            "SELECT config_version FROM homes WHERE id = %s FOR UPDATE", (home.id,)
        ).fetchone()
        if row is None or row["config_version"] != expected_version:
            raise ConfigVersionConflict(expected_version, row["config_version"] if row else None)
        new_version = row["config_version"] + 1

    # Structure: upsert what is in the config, drop what is not (RESTRICT guards devices).
    for f in home.floors:
        conn.execute(
            "INSERT INTO floors (home_id, id, name, level, elevation_m, height_m)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (home_id, id) DO UPDATE SET name = EXCLUDED.name,"
            " level = EXCLUDED.level, elevation_m = EXCLUDED.elevation_m,"
            " height_m = EXCLUDED.height_m",
            (home.id, f.id, f.name, f.level, f.elevation_m, f.height_m),
        )
    for r in home.rooms:
        conn.execute(
            "INSERT INTO rooms (home_id, id, floor_id, name, footprint)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (home_id, id) DO UPDATE SET floor_id = EXCLUDED.floor_id,"
            " name = EXCLUDED.name, footprint = EXCLUDED.footprint",
            (home.id, r.id, r.floor_id, r.name, Jsonb(list(r.footprint))),
        )
    for g in config.gateways:
        updated = conn.execute(
            "INSERT INTO gateway_connections (id, home_id, kind, name, url, secret_ref, enabled)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name,"
            " url = EXCLUDED.url, secret_ref = EXCLUDED.secret_ref, enabled = EXCLUDED.enabled"
            " WHERE gateway_connections.home_id = EXCLUDED.home_id RETURNING id",
            (g.id, home.id, g.kind, g.name, g.url, g.secret_ref, g.enabled),
        ).fetchone()
        if updated is None:
            raise ConfigConflictError(f"gateway id {g.id} belongs to another home")

    # Devices: soft-delete the ones that left, then upsert the rest. A removed device's id is
    # never reused, in this home or any other.
    device_ids = [d.id for d in config.devices]
    conn.execute(
        "UPDATE devices SET removed_at = %s"
        " WHERE home_id = %s AND removed_at IS NULL AND id <> ALL(%s)",
        (now, home.id, device_ids),
    )
    conn.execute(
        "DELETE FROM device_placements WHERE device_id IN"
        " (SELECT id FROM devices WHERE home_id = %s AND removed_at IS NOT NULL)",
        (home.id,),
    )
    for d in config.devices:
        # Moving to another room: the old placement is meaningless there.
        conn.execute(
            "DELETE FROM device_placements WHERE device_id = %s AND room_id <> %s",
            (d.id, d.room_id),
        )
        updated = conn.execute(
            "INSERT INTO devices (id, home_id, room_id, gateway_id, external_ref, name, type,"
            " manufacturer, model, capabilities) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET room_id = EXCLUDED.room_id,"
            " gateway_id = EXCLUDED.gateway_id, external_ref = EXCLUDED.external_ref,"
            " name = EXCLUDED.name, type = EXCLUDED.type, manufacturer = EXCLUDED.manufacturer,"
            " model = EXCLUDED.model, capabilities = EXCLUDED.capabilities"
            " WHERE devices.home_id = EXCLUDED.home_id AND devices.removed_at IS NULL"
            " RETURNING id",
            (
                d.id,
                home.id,
                d.room_id,
                d.gateway,
                d.external_ref,
                d.name,
                d.type.value,
                d.manufacturer,
                d.model,
                Jsonb([c.model_dump(mode="json") for c in d.capabilities]),
            ),
        ).fetchone()
        if updated is None:
            raise ConfigConflictError(f"device id {d.id} belongs to another home or was removed")
        p = d.placement
        if p is None:
            conn.execute("DELETE FROM device_placements WHERE device_id = %s", (d.id,))
        else:
            conn.execute(
                "INSERT INTO device_placements"
                " (device_id, room_id, pos_x, pos_y, pos_z, rotation_y_deg)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (device_id) DO UPDATE SET pos_x = EXCLUDED.pos_x,"
                " pos_y = EXCLUDED.pos_y, pos_z = EXCLUDED.pos_z,"
                " rotation_y_deg = EXCLUDED.rotation_y_deg,"
                " version = device_placements.version + 1"
                " WHERE (device_placements.pos_x, device_placements.pos_y,"
                " device_placements.pos_z, device_placements.rotation_y_deg)"
                " IS DISTINCT FROM (EXCLUDED.pos_x, EXCLUDED.pos_y, EXCLUDED.pos_z,"
                " EXCLUDED.rotation_y_deg)",
                (d.id, p.room_id, *p.position, p.rotation_y_deg),
            )

    # Drop structure that left the config. RESTRICT refuses while any device (even a
    # soft-deleted one) still references it.
    conn.execute(
        "DELETE FROM rooms WHERE home_id = %s AND id <> ALL(%s)",
        (home.id, [r.id for r in home.rooms]),
    )
    conn.execute(
        "DELETE FROM floors WHERE home_id = %s AND id <> ALL(%s)",
        (home.id, [f.id for f in home.floors]),
    )
    conn.execute(
        "DELETE FROM gateway_connections WHERE home_id = %s AND id <> ALL(%s)",
        (home.id, [g.id for g in config.gateways]),
    )
    conn.execute(
        "UPDATE homes SET name = %s, simulated = %s, config_version = %s, updated_at = %s"
        " WHERE id = %s",
        (home.name, home.simulated, new_version, now, home.id),
    )
    return new_version
