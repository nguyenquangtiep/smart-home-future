"""Store contract used by the execution engine."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from smarthome.control.audit import AuditEvent, AuditEventIn
from smarthome.control.commands import CommandRecord, CommandStatus


class StoreConflictError(Exception):
    """A uniqueness rule was violated, e.g. a second in-flight command for one capability."""


class InstanceLockError(RuntimeError):
    """Another backend instance is already running against this database."""


class IdempotencyHit(BaseModel):
    """An unexpired idempotency key already exists in this scope."""

    record: CommandRecord
    fingerprint: str


class CommandStore(Protocol):
    durable: bool

    async def create(
        self,
        record: CommandRecord,
        fingerprint: str | None,
        key_expires_at: datetime | None,
        now: datetime,
    ) -> IdempotencyHit | None:
        """Atomically reserve ``record.idempotency_key`` (if any) and insert ``record``.

        Returns the existing entry instead of inserting when an unexpired key exists in the
        scope ``(home_id, actor_id, key)``. An expired key is rebound to the new record.
        """
        ...

    async def save(self, record: CommandRecord) -> None:
        """Persist all mutable fields. Raises StoreConflictError on the in-flight rule."""
        ...

    async def claim(
        self,
        command_id: str,
        expected: CommandStatus,
        new_status: CommandStatus,
        confirmed_by: str,
        now: datetime,
    ) -> bool:
        """Conditional transition used for approvals: succeeds once, only before expiry."""
        ...

    async def get(self, command_id: str) -> CommandRecord | None: ...

    async def list_unfinished(self) -> list[CommandRecord]: ...

    async def append_audit(self, event: AuditEventIn) -> AuditEvent: ...

    async def list_audit(self, home_id: str, limit: int = 100) -> list[AuditEvent]: ...

    async def purge(self, commands_before: datetime, audit_before: datetime) -> tuple[int, int]:
        """Delete finished commands and audit events older than the given instants."""
        ...

    async def acquire_instance_lock(self) -> None:
        """Claim exclusive use of the data for this process (startup recovery assumes it).

        Raises InstanceLockError when another instance holds it.
        """
        ...

    async def close(self) -> None: ...
