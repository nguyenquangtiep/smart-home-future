"""S1-03: HomeConfigStore contract (memory and PostgreSQL) and migration 0002 (AC-06-1...13)."""

import asyncio
import re
import threading
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from smarthome.demo_home import BEDROOM, GATEWAY_ID, HOME_ID, LIVING, build_demo_config
from smarthome.domain.config import ConfigInvalidError
from smarthome.domain.models import DeviceType, GatewayConnection, Home, HomeConfig, Placement
from smarthome.persistence import migrate
from smarthome.persistence.config_store import (
    ConfigConflictError,
    ConfigVersionConflict,
    HomeConfigStore,
    MemoryHomeConfigStore,
    PostgresHomeConfigStore,
)
from smarthome.persistence.postgres import connect

# PostgreSQL 18 reports ON DELETE RESTRICT as 23001 (RestrictViolation); 16 reports 23503
# (ForeignKeyViolation). Both mean "refused because something still references this row".
RESTRICTED = (psycopg.errors.RestrictViolation, psycopg.errors.ForeignKeyViolation)


def demo() -> HomeConfig:
    return build_demo_config()[0]


def second_home(config: HomeConfig, home_id: str = "home-b") -> HomeConfig:
    """The demo home under other ids: same room ids and external refs, other gateway/devices."""
    gateway = GatewayConnection(id=f"gw-{home_id}", home_id=home_id, kind="simulator", name="B")
    devices = tuple(
        d.model_copy(update={"id": f"{home_id}-{d.id}", "home_id": home_id, "gateway": gateway.id})
        for d in config.devices
    )
    return config.model_copy(
        update={
            "home": config.home.model_copy(update={"id": home_id, "name": "B"}),
            "gateways": (gateway,),
            "devices": devices,
        }
    )


def _canonical(home: Home) -> Home:
    return home.model_copy(
        update={
            "floors": tuple(sorted(home.floors, key=lambda f: f.id)),
            "rooms": tuple(sorted(home.rooms, key=lambda r: r.id)),
        }
    )


def without(config: HomeConfig, *device_ids: str) -> HomeConfig:
    return config.model_copy(
        update={"devices": tuple(d for d in config.devices if d.id not in device_ids)}
    )


def edit(config: HomeConfig, device_id: str, **changes: Any) -> HomeConfig:
    return config.model_copy(
        update={
            "devices": tuple(
                d.model_copy(update=changes) if d.id == device_id else d for d in config.devices
            )
        }
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---- fixtures: every contract test runs against both stores ---------------------------------


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> Iterator[HomeConfigStore]:
    if request.param == "memory":
        yield MemoryHomeConfigStore()
        return
    dsn = request.getfixturevalue("pg_dsn")
    pg = run(PostgresHomeConfigStore.open(dsn))
    yield pg
    run(pg.close())


# ---- contract -------------------------------------------------------------------------------


def test_empty_store_loads_nothing(store: HomeConfigStore) -> None:
    assert run(store.load_all()) == []


def test_round_trip_is_exact_including_capabilities(store: HomeConfigStore) -> None:
    """AC-06-2: Home, Device and Capability come back equal (types and constraints intact)."""
    config = demo()
    assert run(store.replace_home(config, None)) == 1
    (loaded,) = run(store.load_all())
    assert loaded.version == 1
    # Floors and rooms come back ordered by (level, id), not in insertion order.
    assert _canonical(loaded.home) == _canonical(config.home)
    assert loaded.gateways == config.gateways
    assert sorted(loaded.devices, key=lambda d: d.id) == sorted(config.devices, key=lambda d: d.id)
    heater = next(d for d in loaded.devices if d.id == "dev-water-heater")
    cap = heater.capabilities[0]
    assert cap.safe_values == (False,) and cap.safety.value == "high_power"


def test_create_twice_conflicts(store: HomeConfigStore) -> None:
    run(store.replace_home(demo(), None))
    with pytest.raises(ConfigVersionConflict):
        run(store.replace_home(demo(), None))


def test_version_increments_by_one_and_stale_version_writes_nothing(
    store: HomeConfigStore,
) -> None:
    """AC-06-8 (sequential part)."""
    config = demo()
    assert run(store.replace_home(config, None)) == 1
    renamed = config.model_copy(update={"home": config.home.model_copy(update={"name": "Mới"})})
    with pytest.raises(ConfigVersionConflict) as excinfo:
        run(store.replace_home(renamed, 7))
    assert (excinfo.value.expected, excinfo.value.actual) == (7, 1)
    assert run(store.load_all())[0].home.name == config.home.name
    assert run(store.replace_home(renamed, 1)) == 2
    assert run(store.replace_home(renamed, 2)) == 3
    assert run(store.load_all())[0].version == 3


def test_updating_a_missing_home_conflicts(store: HomeConfigStore) -> None:
    with pytest.raises(ConfigVersionConflict):
        run(store.replace_home(demo(), 1))


def test_invalid_config_is_rejected_before_anything_is_written(store: HomeConfigStore) -> None:
    bad = edit(demo(), "dev-living-tv", room_id="missing-room")
    with pytest.raises(ConfigInvalidError):
        run(store.replace_home(bad, None))
    assert run(store.load_all()) == []


def test_device_removed_from_config_is_soft_deleted(store: HomeConfigStore) -> None:
    config = demo()
    run(store.replace_home(config, None))
    run(store.replace_home(without(config, "dev-living-tv"), 1))
    (loaded,) = run(store.load_all())
    assert "dev-living-tv" not in {d.id for d in loaded.devices}


def test_removed_device_id_can_never_come_back(store: HomeConfigStore) -> None:
    """D-06-5 / AC-06-5: ids are not reused, even for the same device."""
    config = demo()
    run(store.replace_home(config, None))
    run(store.replace_home(without(config, "dev-living-tv"), 1))
    with pytest.raises(ConfigConflictError):
        run(store.replace_home(config, 2))


def test_external_ref_of_a_removed_device_can_be_reused_by_a_new_device(
    store: HomeConfigStore,
) -> None:
    config = demo()
    run(store.replace_home(config, None))
    tv = next(d for d in config.devices if d.id == "dev-living-tv")
    run(store.replace_home(without(config, "dev-living-tv"), 1))
    replacement = tv.model_copy(update={"id": "dev-living-tv-2"})
    run(
        store.replace_home(
            without(config, "dev-living-tv").model_copy(
                update={"devices": (*without(config, "dev-living-tv").devices, replacement)}
            ),
            2,
        )
    )
    ids = {d.id for d in run(store.load_all())[0].devices}
    assert "dev-living-tv-2" in ids and "dev-living-tv" not in ids


def test_two_homes_may_share_room_ids_and_external_refs(store: HomeConfigStore) -> None:
    """AC-06-5: the gateway instance, not the adapter name, scopes external refs."""
    a, b = demo(), second_home(demo())
    run(store.replace_home(a, None))
    run(store.replace_home(b, None))
    loaded = {c.home.id: c for c in run(store.load_all())}
    assert set(loaded) == {HOME_ID, "home-b"}
    assert {r.id for r in loaded["home-b"].home.rooms} == {LIVING, BEDROOM}
    assert {d.external_ref for d in loaded[HOME_ID].devices} == {
        d.external_ref for d in loaded["home-b"].devices
    }


def test_device_id_owned_by_another_home_conflicts(store: HomeConfigStore) -> None:
    run(store.replace_home(demo(), None))
    thief = second_home(demo())
    stolen = thief.devices[0].model_copy(update={"id": "dev-living-light"})
    thief = thief.model_copy(update={"devices": (stolen, *thief.devices[1:])})
    with pytest.raises(ConfigConflictError):
        run(store.replace_home(thief, None))
    assert {c.home.id for c in run(store.load_all())} == {HOME_ID}  # nothing half-written


def test_gateway_id_owned_by_another_home_conflicts(store: HomeConfigStore) -> None:
    run(store.replace_home(demo(), None))
    other = second_home(demo())
    gateway = other.gateways[0].model_copy(update={"id": GATEWAY_ID})
    devices = tuple(d.model_copy(update={"gateway": GATEWAY_ID}) for d in other.devices)
    with pytest.raises(ConfigConflictError):
        run(
            store.replace_home(
                other.model_copy(update={"gateways": (gateway,), "devices": devices}), None
            )
        )


def test_room_that_ever_held_a_device_cannot_be_dropped(store: HomeConfigStore) -> None:
    """AC-06-6 (store level): even soft-deleted devices keep their room alive."""
    config = demo()
    run(store.replace_home(config, None))
    no_bedroom_devices = without(config, "dev-bedroom-light", "dev-bedroom-ac", "dev-water-heater")
    run(store.replace_home(no_bedroom_devices, 1))
    home = config.home.model_copy(
        update={"rooms": tuple(r for r in config.home.rooms if r.id != BEDROOM)}
    )
    with pytest.raises(ConfigConflictError):
        run(store.replace_home(no_bedroom_devices.model_copy(update={"home": home}), 2))


def test_placement_update_and_moving_rooms(store: HomeConfigStore) -> None:
    """AC-06-4 (store level): moving a device to another room replaces its placement."""
    config = demo()
    run(store.replace_home(config, None))
    moved = edit(
        config,
        "dev-living-tv",
        room_id=BEDROOM,
        placement=Placement(room_id=BEDROOM, position=(7.0, 1.0, 1.0), rotation_y_deg=90),
    )
    run(store.replace_home(moved, 1))
    tv = next(d for d in run(store.load_all())[0].devices if d.id == "dev-living-tv")
    assert tv.room_id == BEDROOM
    assert tv.placement == Placement(room_id=BEDROOM, position=(7.0, 1.0, 1.0), rotation_y_deg=90)
    unplaced = edit(moved, "dev-living-tv", placement=None)
    run(store.replace_home(unplaced, 2))
    tv = next(d for d in run(store.load_all())[0].devices if d.id == "dev-living-tv")
    assert tv.placement is None


# ---- PostgreSQL only ------------------------------------------------------------------------


def test_migration_0002_round_trip_keeps_0001_data(empty_pg_dsn: str) -> None:
    """AC-06-1."""
    with connect(empty_pg_dsn) as conn:
        migrate.upgrade(conn, target=1)
        conn.execute(
            "INSERT INTO audit_events (at, home_id, action) VALUES (now(), 'h', 'before-0002')"
        )
        assert migrate.upgrade(conn, target=2) == [2]
        assert migrate.current_version(conn) == 2
        assert migrate.downgrade(conn, 1) == [2]
        gone = conn.execute("SELECT to_regclass('homes') IS NULL AS gone").fetchone()
        assert gone["gone"]
        kept = conn.execute("SELECT count(*) AS n FROM audit_events").fetchone()
        assert kept["n"] == 1
        assert migrate.upgrade(conn, target=2) == [2]


def _seeded(pg_dsn: str) -> psycopg.Connection[dict[str, Any]]:
    pg = run(PostgresHomeConfigStore.open(pg_dsn))
    run(pg.replace_home(demo(), None))
    run(pg.replace_home(second_home(demo()), None))
    run(pg.close())
    return connect(pg_dsn)


def test_composite_foreign_keys_block_cross_home_rows(pg_dsn: str) -> None:
    """AC-06-3: a room, a device or a gateway reference cannot point into another home."""
    conn = _seeded(pg_dsn)
    # floor-1 exists in both homes, so use a floor that exists only in home-demo
    conn.execute(
        "INSERT INTO floors (home_id, id, name, level) VALUES ('home-demo','floor-only-a','A',5)"
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO rooms (home_id, id, floor_id, name, footprint)"
            " VALUES ('home-b', 'r-x', 'floor-only-a', 'x', '[0,0,1,1]')"
        )
    conn.execute(
        "INSERT INTO rooms (home_id, id, floor_id, name, footprint)"
        " VALUES ('home-demo', 'room-only-a', 'floor-only-a', 'x', '[0,0,1,1]')"
    )
    insert_device = (
        "INSERT INTO devices (id, home_id, room_id, gateway_id, external_ref, name, type,"
        " manufacturer, model, capabilities) VALUES (%s, %s, %s, %s, 'ref', 'n', 'light', 'm', 'm',"
        " '[{}]')"
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # other home's room
        conn.execute(insert_device, ("d1", "home-b", "room-only-a", "gw-home-b"))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # other home's gateway
        conn.execute(insert_device, ("d2", "home-b", LIVING, GATEWAY_ID))
    conn.close()


def test_placement_must_match_the_devices_room(pg_dsn: str) -> None:
    """AC-06-4."""
    conn = _seeded(pg_dsn)
    conn.execute("DELETE FROM device_placements WHERE device_id = 'dev-bedroom-light'")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # placement in the wrong room
        conn.execute(
            "INSERT INTO device_placements (device_id, room_id, pos_x, pos_y, pos_z)"
            " VALUES ('dev-bedroom-light', %s, 1, 1, 1)",
            (LIVING,),
        )
    conn.execute(
        "INSERT INTO device_placements (device_id, room_id, pos_x, pos_y, pos_z)"
        " VALUES ('dev-bedroom-light', %s, 7, 1, 1)",
        (BEDROOM,),
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO device_placements (device_id, room_id, pos_x, pos_y, pos_z)"
            " VALUES ('no-such-device', %s, 1, 1, 1)",
            (LIVING,),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # change room while placement exists
        conn.execute("UPDATE devices SET room_id = %s WHERE id = 'dev-bedroom-light'", (LIVING,))
    with conn.transaction():
        conn.execute("DELETE FROM device_placements WHERE device_id = 'dev-bedroom-light'")
        conn.execute("UPDATE devices SET room_id = %s WHERE id = 'dev-bedroom-light'", (LIVING,))
    conn.close()


def test_external_ref_uniqueness_and_id_reuse(pg_dsn: str) -> None:
    """AC-06-5 at SQL level."""
    conn = _seeded(pg_dsn)
    insert = (
        "INSERT INTO devices (id, home_id, room_id, gateway_id, external_ref, name, type,"
        " manufacturer, model, capabilities) VALUES (%s, 'home-demo', %s, %s, %s, 'n', 'light',"
        " 'm', 'm', '[{}]')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):  # same (gateway, ref), both active
        conn.execute(insert, ("dup", LIVING, GATEWAY_ID, "sim.dev-living-tv"))
    conn.execute("UPDATE devices SET removed_at = now() WHERE id = 'dev-living-tv'")
    conn.execute(insert, ("again", LIVING, GATEWAY_ID, "sim.dev-living-tv"))  # now allowed
    with pytest.raises(psycopg.errors.UniqueViolation):  # id never reused, even if removed
        conn.execute(insert, ("dev-living-tv", LIVING, GATEWAY_ID, "sim.fresh"))
    conn.close()


def test_delete_rules_use_restrict(pg_dsn: str) -> None:
    """AC-06-6: RESTRICT refuses the delete (23001 on PostgreSQL 18, 23503 on 16)."""
    conn = _seeded(pg_dsn)
    with pytest.raises(RESTRICTED):
        conn.execute("DELETE FROM floors WHERE home_id = 'home-demo' AND id = 'floor-1'")
    with pytest.raises(RESTRICTED):
        conn.execute("DELETE FROM rooms WHERE home_id = 'home-demo' AND id = %s", (LIVING,))
    conn.execute("UPDATE devices SET removed_at = now() WHERE room_id = %s", (BEDROOM,))
    with pytest.raises(RESTRICTED):  # soft-deleted devices still block
        conn.execute("DELETE FROM rooms WHERE home_id = 'home-demo' AND id = %s", (BEDROOM,))
    with pytest.raises(RESTRICTED):
        conn.execute("DELETE FROM gateway_connections WHERE id = %s", (GATEWAY_ID,))
    with pytest.raises(RESTRICTED):
        conn.execute("DELETE FROM homes WHERE id = 'home-demo'")
    conn.execute("DELETE FROM devices WHERE id = 'dev-living-tv'")  # hard delete cascades placement
    left = conn.execute(
        "SELECT count(*) AS n FROM device_placements WHERE device_id = 'dev-living-tv'"
    ).fetchone()
    assert left["n"] == 0
    conn.close()


def test_check_constraints_match_the_python_enums(pg_dsn: str) -> None:
    """AC-06-12: the SQL CHECK lists and the Python types cannot drift apart."""
    conn = connect(pg_dsn)

    def allowed(table: str, column: str) -> set[str]:
        rows = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) AS d FROM pg_constraint c"
            " JOIN pg_class t ON t.oid = c.conrelid WHERE t.relname = %s AND c.contype = 'c'",
            (table,),
        ).fetchall()
        for row in rows:
            if column in row["d"] and "ANY" in row["d"]:
                return set(re.findall(r"'([a-z_]+)'::text", row["d"]))
        raise AssertionError(f"no CHECK on {table}.{column}")

    assert allowed("devices", "type") == {t.value for t in DeviceType}
    assert allowed("gateway_connections", "kind") == {"simulator", "home_assistant"}
    conn.close()


def test_concurrent_replace_with_same_version_has_exactly_one_winner(pg_dsn: str) -> None:
    """AC-06-8."""

    async def body() -> None:
        first = await PostgresHomeConfigStore.open(pg_dsn)
        second = await PostgresHomeConfigStore.open(pg_dsn)
        base = demo()
        await first.replace_home(base, None)
        a = base.model_copy(update={"home": base.home.model_copy(update={"name": "A"})})
        b = base.model_copy(update={"home": base.home.model_copy(update={"name": "B"})})
        results = await asyncio.gather(
            first.replace_home(a, 1), second.replace_home(b, 1), return_exceptions=True
        )
        wins = [r for r in results if r == 2]
        losses = [r for r in results if isinstance(r, ConfigVersionConflict)]
        assert len(wins) == 1 and len(losses) == 1, results
        (loaded,) = await first.load_all()
        assert loaded.version == 2 and loaded.home.name in {"A", "B"}
        await first.close()
        await second.close()

    asyncio.run(body())


def test_failed_write_rolls_back_every_table(pg_dsn: str) -> None:
    """A conflict half-way through replace_home leaves the stored config untouched."""

    async def body() -> None:
        store = await PostgresHomeConfigStore.open(pg_dsn)
        await store.replace_home(demo(), None)
        before = await store.load_all()
        changed = edit(demo(), "dev-living-light", name="Đổi tên")
        changed = changed.model_copy(update={"home": changed.home.model_copy(update={"name": "X"})})
        # A removed device's id cannot return: this is detected after the UPDATEs above ran.
        await store.replace_home(without(demo(), "dev-living-tv"), 1)
        with pytest.raises(ConfigConflictError):
            await store.replace_home(changed, 2)
        after = await store.load_all()
        assert after[0].version == 2
        assert after[0].home.name == before[0].home.name
        assert next(d for d in after[0].devices if d.id == "dev-living-light").name != "Đổi tên"
        await store.close()

    asyncio.run(body())


def test_load_all_is_one_consistent_snapshot(pg_dsn: str) -> None:
    """While a writer flips between two configs, every load equals one of them entirely."""
    base = demo()
    variant = edit(base, "dev-living-light", name="B-name")
    variant = variant.model_copy(update={"home": base.home.model_copy(update={"name": "B-home"})})
    snapshots: list[HomeConfig] = []
    stop = threading.Event()
    errors: list[BaseException] = []

    async def setup() -> int:
        store = await PostgresHomeConfigStore.open(pg_dsn)
        await store.replace_home(base, None)
        await store.close()
        return 1

    def writer() -> None:
        async def go() -> None:
            store = await PostgresHomeConfigStore.open(pg_dsn)
            version, flip = 1, True
            while not stop.is_set():
                version = await store.replace_home(variant if flip else base, version)
                flip = not flip
            await store.close()

        try:
            asyncio.run(go())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    asyncio.run(setup())
    thread = threading.Thread(target=writer)
    thread.start()

    async def reader() -> None:
        store = await PostgresHomeConfigStore.open(pg_dsn)
        for _ in range(60):
            snapshots.append((await store.load_all())[0])
        await store.close()

    asyncio.run(reader())
    stop.set()
    thread.join()
    assert not errors, errors
    for snap in snapshots:
        name = next(d for d in snap.devices if d.id == "dev-living-light").name
        assert (snap.home.name, name) in {
            ("B-home", "B-name"),
            (base.home.name, "Đèn trần phòng khách"),
        }


def test_corrupt_capability_json_is_refused_naming_the_device(pg_dsn: str) -> None:
    """AC-06-9 (store level)."""

    async def body() -> None:
        store = await PostgresHomeConfigStore.open(pg_dsn)
        await store.replace_home(demo(), None)
        with connect(pg_dsn) as conn:
            conn.execute(
                'UPDATE devices SET capabilities = \'[{"kind": "nonsense"}]\''
                " WHERE id = 'dev-living-tv'"
            )
        with pytest.raises(ConfigInvalidError) as excinfo:
            await store.load_all()
        assert "dev-living-tv" in str(excinfo.value)
        await store.close()

    asyncio.run(body())
