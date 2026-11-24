"""Command record and lifecycle states, shared by the execution engine and the stores."""

import hashlib
import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from smarthome.control.policy import Actor, CommandRequest, Origin, Reason
from smarthome.domain.capabilities import CapabilityKind, CommandValue


class CommandStatus(StrEnum):
    REQUESTED = "requested"  # accepted; policy/dispatch not finished yet
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    REJECTED = "rejected"
    DISPATCHING = "dispatching"  # persisted as in flight, gateway call in progress
    DISPATCHED = "dispatched"  # gateway accepted, waiting for a verifying report
    SUCCEEDED = "succeeded"  # a report satisfied the verification policy
    TIMED_OUT = "timed_out"  # no verifying report in time: outcome unknown
    FAILED = "failed"  # gateway refused or did not answer
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    INTERRUPTED = "interrupted"  # backend restarted while in flight: outcome unknown, never resent


TERMINAL = frozenset(
    {
        CommandStatus.REJECTED,
        CommandStatus.SUCCEEDED,
        CommandStatus.TIMED_OUT,
        CommandStatus.FAILED,
        CommandStatus.CANCELLED,
        CommandStatus.EXPIRED,
        CommandStatus.INTERRUPTED,
    }
)
UNFINISHED = frozenset(
    {CommandStatus.REQUESTED, CommandStatus.DISPATCHING, CommandStatus.DISPATCHED}
)
IN_FLIGHT = frozenset({CommandStatus.DISPATCHING, CommandStatus.DISPATCHED})


class Verification(StrEnum):
    CORRELATED = "correlated"  # report carried this command's correlation id
    STATE_MATCH = "state_match"  # fresh, in-order report with the commanded value


class CommandRecord(BaseModel):
    id: str
    home_id: str
    device_id: str
    capability: CapabilityKind
    value: CommandValue
    actor_id: str
    origin: Origin
    status: CommandStatus
    reasons: list[Reason] = []
    idempotency_key: str | None = None
    created_at: datetime
    updated_at: datetime
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None
    confirmation_expires_at: datetime | None = None
    confirmed_by: str | None = None
    correlation_id: str | None = None
    verification: Verification | None = None
    verified_value: CommandValue | None = None


def request_fingerprint(actor: Actor, request: CommandRequest) -> str:
    """Stable digest of everything that changes what a command means or who may run it.

    Home and actor are part of the idempotency scope itself, so they are not repeated here.
    The value type is included so that ``true`` and ``1`` never collide.
    """
    payload = {
        "device_id": request.device_id,
        "capability": request.capability.value,
        "value": request.value,
        "value_type": type(request.value).__name__,
        "origin": actor.origin.value,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
