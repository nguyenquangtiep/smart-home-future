"""Built-in device simulator for development and tests. Never used with real devices.

Every report it produces carries ``Source.SIMULATED`` so the UI can label it as such.
Per-device behaviours reproduce the failure cases the system must handle.
"""

import asyncio
from collections.abc import Mapping
from enum import StrEnum

from smarthome.domain.capabilities import CapabilityKind, CommandValue
from smarthome.domain.models import Device
from smarthome.domain.state import Clock, Connectivity, Source, utc_now
from smarthome.gateway.base import GatewayError, ReportListener, StateReport


class SimBehavior(StrEnum):
    NORMAL = "normal"  # accepts and reports the new value after ``latency_s``
    SILENT = "silent"  # accepts but never reports (lost confirmation)
    ERROR = "error"  # gateway rejects the command
    OFFLINE = "offline"  # device unreachable


class SimulatorGateway:
    name = "simulator"

    def __init__(
        self, latency_s: float = 0.3, clock: Clock = utc_now, correlate: bool = True
    ) -> None:
        self.latency_s = latency_s
        # True: echo the command's correlation id on resulting reports (like an adapter that
        # supports correlation). False: behave like a protocol without correlation.
        self.correlate = correlate
        self._clock = clock
        self._listeners: list[ReportListener] = []
        self._behavior: dict[str, SimBehavior] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.sent: list[tuple[str, CapabilityKind, CommandValue]] = []

    def subscribe(self, listener: ReportListener) -> None:
        self._listeners.append(listener)

    def behavior(self, device_id: str) -> SimBehavior:
        return self._behavior.get(device_id, SimBehavior.NORMAL)

    async def set_behavior(self, device_id: str, behavior: SimBehavior) -> None:
        self._behavior[device_id] = behavior
        connectivity = (
            Connectivity.OFFLINE if behavior is SimBehavior.OFFLINE else Connectivity.ONLINE
        )
        await self._emit(StateReport(**self._base(device_id), connectivity=connectivity))

    async def report(
        self,
        device_id: str,
        values: Mapping[CapabilityKind, CommandValue],
        correlation_id: str | None = None,
    ) -> None:
        """Emit a report, e.g. initial state, a sensor reading or a command result."""
        await self._emit(
            StateReport(**self._base(device_id), values=dict(values), correlation_id=correlation_id)
        )

    async def send(
        self, device: Device, kind: CapabilityKind, value: CommandValue, correlation_id: str
    ) -> str | None:
        behavior = self.behavior(device.id)
        if behavior is SimBehavior.OFFLINE:
            raise GatewayError("thiết bị mô phỏng đang offline")
        if behavior is SimBehavior.ERROR:
            raise GatewayError("gateway mô phỏng từ chối lệnh")
        self.sent.append((device.id, kind, value))
        corr = correlation_id if self.correlate else None
        if behavior is not SimBehavior.SILENT:
            task = asyncio.create_task(self._delayed_report(device.id, kind, value, corr))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return corr

    async def _delayed_report(
        self, device_id: str, kind: CapabilityKind, value: CommandValue, corr: str | None
    ) -> None:
        await asyncio.sleep(self.latency_s)
        await self.report(device_id, {kind: value}, correlation_id=corr)

    def _base(self, device_id: str) -> dict[str, object]:
        return {"device_id": device_id, "source": Source.SIMULATED, "observed_at": self._clock()}

    async def _emit(self, report: StateReport) -> None:
        for listener in self._listeners:
            await listener(report)
