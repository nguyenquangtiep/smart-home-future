"""Gateway contract.

``send`` returning means only "the gateway accepted the command". It is never proof that the
device changed. Proof arrives later as a ``StateReport`` delivered to subscribed listeners and
must satisfy the verification policy in ``control/execution.py``.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from smarthome.domain.capabilities import CapabilityKind, CommandValue
from smarthome.domain.models import Device
from smarthome.domain.state import Connectivity, Source


class GatewayError(Exception):
    """The gateway refused or could not deliver the command."""


class StateReport(BaseModel):
    device_id: str
    values: dict[CapabilityKind, CommandValue] = {}
    connectivity: Connectivity | None = None
    source: Source
    observed_at: datetime
    # Set when the protocol can tie this report to the command that caused it (e.g. the
    # Home Assistant context id, or the command id echoed by the device).
    correlation_id: str | None = None


ReportListener = Callable[[StateReport], Awaitable[None]]


class DeviceGateway(Protocol):
    name: str

    async def send(
        self, device: Device, kind: CapabilityKind, value: CommandValue, correlation_id: str
    ) -> str | None:
        """Deliver a command. ``correlation_id`` is offered for adapters that can echo it.

        Returns the id the gateway will put on resulting reports (may equal
        ``correlation_id``), or None when the protocol has no correlation.
        """
        ...

    def subscribe(self, listener: ReportListener) -> None: ...
