"""Audit event model. Events are persisted by the command store (memory or Postgres)."""

from datetime import datetime

from pydantic import BaseModel


class AuditEventIn(BaseModel):
    at: datetime
    home_id: str
    actor_id: str | None
    origin: str | None
    action: str  # e.g. command.requested, command.rejected, command.succeeded
    device_id: str | None = None
    command_id: str | None = None
    # Codes, IDs and command values only. Never tokens, passwords or message text.
    detail: dict[str, object] = {}


class AuditEvent(AuditEventIn):
    seq: int
