"""Shared fixtures.

Postgres tests need ``SMARTHOME_TEST_DATABASE_URL`` pointing at a server where the user may create
databases (e.g. ``postgresql://postgres@127.0.0.1:5432/postgres``). Each test gets a fresh,
migrated database that is dropped afterwards. Without the variable those tests are skipped,
unless ``SMARTHOME_REQUIRE_POSTGRES=1`` (set in CI), in which case they fail instead.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from smarthome.persistence import migrate
from smarthome.persistence.postgres import connect

ADMIN_DSN = os.environ.get("SMARTHOME_TEST_DATABASE_URL")
REQUIRE_POSTGRES = os.environ.get("SMARTHOME_REQUIRE_POSTGRES") == "1"


def _dsn_for(database: str) -> str:
    assert ADMIN_DSN is not None
    info = psycopg.conninfo.conninfo_to_dict(ADMIN_DSN)
    info["dbname"] = database
    return psycopg.conninfo.make_conninfo(**info)


@pytest.fixture
def empty_pg_dsn() -> Iterator[str]:
    """DSN of a brand-new, empty database (no migrations applied)."""
    if ADMIN_DSN is None:
        if REQUIRE_POSTGRES:
            pytest.fail("SMARTHOME_REQUIRE_POSTGRES=1 but SMARTHOME_TEST_DATABASE_URL is not set")
        pytest.skip("SMARTHOME_TEST_DATABASE_URL not set")
    name = f"smarthome_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield _dsn_for(name)
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def pg_dsn(empty_pg_dsn: str) -> str:
    """DSN of a fresh database migrated to the latest schema."""
    with connect(empty_pg_dsn) as conn:
        migrate.upgrade(conn)
    return empty_pg_dsn
