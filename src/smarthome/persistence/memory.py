"""In-process store with the same semantics as the Postgres store. Not durable."""

import itertools
from datetime import datetime

from smarthome.control.audit import AuditEvent, AuditEventIn
from smarthome.control.commands import IN_FLIGHT, TERMINAL, UNFINISHED, CommandRecord, CommandStatus
from smarthome.persistence.base import IdempotencyHit, StoreConflictError


class MemoryCommandStore:
    durable = False

    def __init__(self) -> None:
        self._commands: dict[str, CommandRecord] = {}
        self._keys: dict[tuple[str, str, str], tuple[str, str, datetime]] = {}
        self._audit: list[AuditEvent] = []
        self._seq = itertools.count(1)

    # Methods contain no ``await``, so each one is atomic within the event loop.

    async def create(
        self,
        record: CommandRecord,
        fingerprint: str | None,
        key_expires_at: datetime | None,
        now: datetime,
    ) -> IdempotencyHit | None:
        key = record.idempotency_key
        if key is not None:
            scope = (record.home_id, record.actor_id, key)
            entry = self._keys.get(scope)
            if entry is not None and entry[2] > now:
                fp, command_id, _ = entry
                return IdempotencyHit(record=self._copy(command_id), fingerprint=fp)
            assert fingerprint is not None and key_expires_at is not None
            self._keys[scope] = (fingerprint, record.id, key_expires_at)
        self._commands[record.id] = record.model_copy(deep=True)
        return None

    async def save(self, record: CommandRecord) -> None:
        if record.status in IN_FLIGHT:
            for other in self._commands.values():
                if (
                    other.id != record.id
                    and other.status in IN_FLIGHT
                    and other.device_id == record.device_id
                    and other.capability == record.capability
                ):
                    raise StoreConflictError("another command is in flight for this capability")
        self._commands[record.id] = record.model_copy(deep=True)

    async def claim(
        self,
        command_id: str,
        expected: CommandStatus,
        new_status: CommandStatus,
        confirmed_by: str,
        now: datetime,
    ) -> bool:
        stored = self._commands.get(command_id)
        if stored is None or stored.status is not expected:
            return False
        if stored.confirmation_expires_at is not None and now >= stored.confirmation_expires_at:
            return False
        stored.status = new_status
        stored.confirmed_by = confirmed_by
        stored.updated_at = now
        return True

    async def get(self, command_id: str) -> CommandRecord | None:
        return self._copy(command_id) if command_id in self._commands else None

    async def list_unfinished(self) -> list[CommandRecord]:
        return [c.model_copy(deep=True) for c in self._commands.values() if c.status in UNFINISHED]

    async def append_audit(self, event: AuditEventIn) -> AuditEvent:
        stored = AuditEvent(seq=next(self._seq), **event.model_dump())
        self._audit.append(stored)
        return stored

    async def list_audit(self, home_id: str, limit: int = 100) -> list[AuditEvent]:
        events = [e for e in self._audit if e.home_id == home_id]
        return events[-limit:][::-1]

    async def purge(self, commands_before: datetime, audit_before: datetime) -> tuple[int, int]:
        old = [
            c.id
            for c in self._commands.values()
            if c.status in TERMINAL and c.created_at < commands_before
        ]
        for command_id in old:
            del self._commands[command_id]
        self._keys = {k: v for k, v in self._keys.items() if v[1] in self._commands}
        before = len(self._audit)
        self._audit = [e for e in self._audit if e.at >= audit_before]
        return len(old), before - len(self._audit)

    async def acquire_instance_lock(self) -> None:
        return None  # one process owns its own memory

    async def close(self) -> None:
        return None

    def _copy(self, command_id: str) -> CommandRecord:
        return self._commands[command_id].model_copy(deep=True)
