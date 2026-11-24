"""Versioned SQL migrations: ``migrations/NNNN_name.up.sql`` and ``NNNN_name.down.sql``.

Each migration runs in its own transaction (Postgres DDL is transactional) together with the
row it adds to or removes from ``schema_migrations``.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.up\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_sql: str
    down_sql: str


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found = []
    for path in sorted(directory.glob("*.up.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise ValueError(f"bad migration file name: {path.name}")
        down = path.with_name(path.name.replace(".up.sql", ".down.sql"))
        if not down.exists():
            raise ValueError(f"missing rollback for {path.name}")
        found.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                up_sql=path.read_text(encoding="utf-8"),
                down_sql=down.read_text(encoding="utf-8"),
            )
        )
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise ValueError(f"migration versions must be 1..N without gaps, got {versions}")
    return found


def latest_version() -> int:
    migrations = discover()
    return migrations[-1].version if migrations else 0


def current_version(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()
    if not row or not _first(row):
        return 0
    row = conn.execute("SELECT coalesce(max(version), 0) FROM schema_migrations").fetchone()
    return int(_first(row)) if row else 0


def upgrade(conn: psycopg.Connection, target: int | None = None) -> list[int]:
    """Apply pending migrations up to ``target`` (default: latest). Returns applied versions."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version integer PRIMARY KEY, name text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
    applied = []
    current = current_version(conn)
    for migration in discover():
        if migration.version <= current or (target is not None and migration.version > target):
            continue
        with conn.transaction():
            conn.execute(migration.up_sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (migration.version, migration.name),
            )
        applied.append(migration.version)
    return applied


def downgrade(conn: psycopg.Connection, target: int) -> list[int]:
    """Roll back applied migrations above ``target``. May destroy data: back up first."""
    reverted = []
    current = current_version(conn)
    for migration in reversed(discover()):
        if migration.version > current or migration.version <= target:
            continue
        with conn.transaction():
            conn.execute(migration.down_sql)
            conn.execute("DELETE FROM schema_migrations WHERE version = %s", (migration.version,))
        reverted.append(migration.version)
    return reverted


def _first(row: object) -> object:
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]  # type: ignore[index]
