"""Durable command store on PostgreSQL.

Uses synchronous psycopg in a worker thread (``asyncio.to_thread``) with one connection and a
lock: portable (async psycopg does not run on the default Windows event loop) and sufficient
for one home. Every method is one transaction.
"""

import asyncio
import contextlib
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from smarthome.control.audit import AuditEvent, AuditEventIn
from smarthome.control.commands import TERMINAL, UNFINISHED, CommandRecord, CommandStatus
from smarthome.persistence import migrate
from smarthome.persistence.base import IdempotencyHit, InstanceLockError, StoreConflictError

T = TypeVar("T")

_COLUMNS = (
    "id, home_id, device_id, capability, value, actor_id, origin, status, reasons,"
    " idempotency_key, created_at, updated_at, dispatched_at, completed_at,"
    " confirmation_expires_at, confirmed_by, correlation_id, verification, verified_value"
)
_UNFINISHED = [s.value for s in UNFINISHED]
_TERMINAL = [s.value for s in TERMINAL]
# Session-level advisory lock key that marks "a backend instance owns this database".
_INSTANCE_LOCK_KEY = 0x5C01_0001


class SchemaVersionError(RuntimeError):
    pass


def connect(dsn: str) -> psycopg.Connection[dict[str, Any]]:
    # autocommit=True so that every ``conn.transaction()`` block is a real transaction.
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


class PostgresCommandStore:
    durable = True

    def __init__(self, conn: psycopg.Connection[dict[str, Any]], dsn: str) -> None:
        self._conn = conn
        self._dsn = dsn
        self._lock = threading.Lock()
        self._holds_instance_lock = False

    @classmethod
    async def open(cls, dsn: str) -> "PostgresCommandStore":
        conn = await asyncio.to_thread(connect, dsn)
        version = await asyncio.to_thread(migrate.current_version, conn)
        latest = migrate.latest_version()
        if version != latest:
            await asyncio.to_thread(conn.close)
            raise SchemaVersionError(
                f"database schema is at version {version}, code expects {latest}: "
                "run `python -m smarthome.persistence upgrade` (after a backup)"
            )
        return cls(conn, dsn)

    async def _run(self, fn: Callable[[psycopg.Connection[dict[str, Any]]], T]) -> T:
        def work() -> T:
            with self._lock:
                self._ensure_connection()
                with self._conn.transaction():
                    return fn(self._conn)

        return await asyncio.to_thread(work)

    def _ensure_connection(self) -> None:
        """Reconnect if the previous call found the connection lost. Never retries a statement:
        a failed call surfaces its error, the next call gets a fresh connection."""
        if not (self._conn.closed or self._conn.broken):
            return
        with contextlib.suppress(psycopg.Error):
            self._conn.close()
        self._conn = connect(self._dsn)
        if self._holds_instance_lock and not _try_instance_lock(self._conn):
            raise InstanceLockError(
                "lost the instance lock while reconnecting: another instance took over"
            )

    async def acquire_instance_lock(self) -> None:
        def work() -> None:
            with self._lock:
                self._ensure_connection()
                if not _try_instance_lock(self._conn):
                    raise InstanceLockError("another instance is already using this database")
                self._holds_instance_lock = True

        await asyncio.to_thread(work)

    # ---- commands ------------------------------------------------------------------------

    async def create(
        self,
        record: CommandRecord,
        fingerprint: str | None,
        key_expires_at: datetime | None,
        now: datetime,
    ) -> IdempotencyHit | None:
        def tx(conn: psycopg.Connection[dict[str, Any]]) -> IdempotencyHit | None:
            key = record.idempotency_key
            if key is not None:
                scope = (record.home_id, record.actor_id, key)
                inserted = conn.execute(
                    "INSERT INTO idempotency_keys"
                    " (home_id, actor_id, key, fingerprint, command_id, expires_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING RETURNING 1",
                    (*scope, fingerprint, record.id, key_expires_at),
                ).fetchone()
                if inserted is None:
                    # Key exists. Concurrent creators block on the unique index above until
                    # the first commits, so this row is the committed reservation.
                    row = conn.execute(
                        "SELECT fingerprint, command_id, expires_at FROM idempotency_keys"
                        " WHERE home_id = %s AND actor_id = %s AND key = %s FOR UPDATE",
                        scope,
                    ).fetchone()
                    assert row is not None
                    if row["expires_at"] > now:
                        existing = _get(conn, row["command_id"])
                        assert existing is not None
                        return IdempotencyHit(record=existing, fingerprint=row["fingerprint"])
                    conn.execute(
                        "UPDATE idempotency_keys SET fingerprint = %s, command_id = %s,"
                        " expires_at = %s WHERE home_id = %s AND actor_id = %s AND key = %s",
                        (fingerprint, record.id, key_expires_at, *scope),
                    )
            conn.execute(
                f"INSERT INTO commands ({_COLUMNS}) VALUES ({', '.join(['%s'] * 19)})",
                _params(record),
            )
            return None

        return await self._run(tx)

    async def save(self, record: CommandRecord) -> None:
        def tx(conn: psycopg.Connection[dict[str, Any]]) -> None:
            conn.execute(
                "UPDATE commands SET status = %s, reasons = %s, updated_at = %s,"
                " dispatched_at = %s, completed_at = %s, confirmation_expires_at = %s,"
                " confirmed_by = %s, correlation_id = %s, verification = %s,"
                " verified_value = %s WHERE id = %s",
                (
                    record.status.value,
                    Jsonb([r.model_dump() for r in record.reasons]),
                    record.updated_at,
                    record.dispatched_at,
                    record.completed_at,
                    record.confirmation_expires_at,
                    record.confirmed_by,
                    record.correlation_id,
                    record.verification.value if record.verification else None,
                    _json_or_none(record.verified_value),
                    record.id,
                ),
            )

        try:
            await self._run(tx)
        except psycopg.errors.UniqueViolation as exc:
            raise StoreConflictError(str(exc)) from exc

    async def claim(
        self,
        command_id: str,
        expected: CommandStatus,
        new_status: CommandStatus,
        confirmed_by: str,
        now: datetime,
    ) -> bool:
        def tx(conn: psycopg.Connection[dict[str, Any]]) -> bool:
            row = conn.execute(
                "UPDATE commands SET status = %s, confirmed_by = %s, updated_at = %s"
                " WHERE id = %s AND status = %s"
                " AND (confirmation_expires_at IS NULL OR confirmation_expires_at > %s)"
                " RETURNING id",
                (new_status.value, confirmed_by, now, command_id, expected.value, now),
            ).fetchone()
            return row is not None

        return await self._run(tx)

    async def get(self, command_id: str) -> CommandRecord | None:
        return await self._run(lambda conn: _get(conn, command_id))

    async def list_unfinished(self) -> list[CommandRecord]:
        def tx(conn: psycopg.Connection[dict[str, Any]]) -> list[CommandRecord]:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM commands WHERE status = ANY(%s) ORDER BY created_at",
                (_UNFINISHED,),
            ).fetchall()
            return [_record(r) for r in rows]

        return await self._run(tx)

    # ---- audit ---------------------------------------------------------------------------

    async def append_audit(self, event: AuditEventIn) -> AuditEvent:
        def tx(conn: psycopg.Connection[dict[str, Any]]) -> AuditEvent:
            row = conn.execute(
                "INSERT INTO audit_events"
                " (at, home_id, actor_id, origin, action, device_id, command_id, detail)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING seq",
                (
                    event.at,
                    event.home_id,
                    event.actor_id,
                    event.origin,
                    event.action,
                    event.device_id,
                    event.command_id,
                    Jsonb(event.detail),
                ),
            ).fetchone()
            assert row is not None
            return AuditEvent(seq=row["seq"], **event.model_dump())

        return await self._run(tx)

    async def list_audit(self, home_id: str, limit: int = 100) -> list[AuditEvent]:
        def tx(conn: psycopg.Connection[dict[str, Any]]) -> list[AuditEvent]:
            rows = conn.execute(
                "SELECT seq, at, home_id, actor_id, origin, action, device_id, command_id, detail"
                " FROM audit_events WHERE home_id = %s ORDER BY seq DESC LIMIT %s",
                (home_id, limit),
            ).fetchall()
            return [AuditEvent(**r) for r in rows]

        return await self._run(tx)

    async def purge(self, commands_before: datetime, audit_before: datetime) -> tuple[int, int]:
        def tx(conn: psycopg.Connection[dict[str, Any]]) -> tuple[int, int]:
            commands = conn.execute(
                "DELETE FROM commands WHERE status = ANY(%s) AND created_at < %s",
                (_TERMINAL, commands_before),
            ).rowcount
            conn.execute("SET LOCAL smarthome.allow_audit_purge = 'on'")
            audit = conn.execute("DELETE FROM audit_events WHERE at < %s", (audit_before,)).rowcount
            return commands, audit

        return await self._run(tx)

    async def close(self) -> None:
        await asyncio.to_thread(self._conn.close)


def _try_instance_lock(conn: psycopg.Connection[dict[str, Any]]) -> bool:
    row = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (_INSTANCE_LOCK_KEY,)).fetchone()
    return bool(row and row["ok"])


def _get(conn: psycopg.Connection[dict[str, Any]], command_id: str) -> CommandRecord | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM commands WHERE id = %s", (command_id,)).fetchone()
    return _record(row) if row else None


def _record(row: dict[str, Any]) -> CommandRecord:
    return CommandRecord(**row)


def _json_or_none(value: object) -> Jsonb | None:
    return None if value is None else Jsonb(value)


def _params(record: CommandRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.home_id,
        record.device_id,
        record.capability.value,
        Jsonb(record.value),
        record.actor_id,
        record.origin.value,
        record.status.value,
        Jsonb([r.model_dump() for r in record.reasons]),
        record.idempotency_key,
        record.created_at,
        record.updated_at,
        record.dispatched_at,
        record.completed_at,
        record.confirmation_expires_at,
        record.confirmed_by,
        record.correlation_id,
        record.verification.value if record.verification else None,
        _json_or_none(record.verified_value),
    )
