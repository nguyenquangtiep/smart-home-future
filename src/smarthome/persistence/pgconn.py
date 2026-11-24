"""One synchronous psycopg connection used from worker threads (see postgres.py for why).

Shared by the home-configuration and authentication stores. Every ``run`` is one transaction on
a connection protected by a lock; a lost connection is replaced on the next call, never retried.
"""

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import Any, TypeVar

import psycopg

from smarthome.persistence import migrate
from smarthome.persistence.postgres import SchemaVersionError, connect

T = TypeVar("T")
Conn = psycopg.Connection[dict[str, Any]]


class PgConnection:
    def __init__(self, conn: Conn, dsn: str) -> None:
        self._conn = conn
        self._dsn = dsn
        self._lock = threading.Lock()

    @classmethod
    async def open(cls, dsn: str, min_version: int) -> "PgConnection":
        conn = await asyncio.to_thread(connect, dsn)
        version = await asyncio.to_thread(migrate.current_version, conn)
        latest = migrate.latest_version()
        if version != latest or version < min_version:
            await asyncio.to_thread(conn.close)
            raise SchemaVersionError(
                f"database schema is at version {version}, code expects {latest}: "
                "run `python -m smarthome.persistence upgrade` (after a backup)"
            )
        return cls(conn, dsn)

    def _ensure(self) -> None:
        if not (self._conn.closed or self._conn.broken):
            return
        with contextlib.suppress(psycopg.Error):
            self._conn.close()
        self._conn = connect(self._dsn)

    async def run(self, fn: Callable[[Conn], T], *, isolation: str | None = None) -> T:
        """Run ``fn`` in one transaction (optionally with an explicit isolation level)."""

        def work() -> T:
            with self._lock:
                self._ensure()
                with self._conn.transaction():
                    if isolation:
                        self._conn.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
                    return fn(self._conn)

        return await asyncio.to_thread(work)

    async def close(self) -> None:
        await asyncio.to_thread(self._conn.close)
