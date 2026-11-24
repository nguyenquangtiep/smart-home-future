"""Observed device state and the display status derived from it.

Rule: the displayed value of a capability is always the last value a gateway *reported*.
A command that was sent but not yet confirmed shows up as ``pending_value`` next to it,
never in place of it.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from smarthome.domain.capabilities import CapabilityKind, CommandValue
from smarthome.domain.models import Device, Placement

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Source(StrEnum):
    REPORTED = "reported"  # a real gateway/device report
    SIMULATED = "simulated"  # produced by the built-in simulator


class Connectivity(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class DisplayStatus(StrEnum):
    CONFIRMED = "confirmed"  # real report, fresh
    SIMULATED = "simulated"  # simulator report, fresh
    PENDING = "pending"  # a command is in flight, waiting for a matching report
    STALE = "stale"  # last report older than the capability's max age
    UNKNOWN = "unknown"  # never reported
    OFFLINE = "offline"  # device or gateway unreachable; value is last known only


class Observation(BaseModel):
    value: CommandValue
    source: Source
    observed_at: datetime
    correlation_id: str | None = None  # command/gateway correlation carried by the report


class CapabilityView(BaseModel):
    kind: CapabilityKind
    status: DisplayStatus
    value: CommandValue | None
    source: Source | None
    observed_at: datetime | None
    pending_value: CommandValue | None = None
    pending_command_id: str | None = None


class DeviceView(BaseModel):
    id: str
    name: str
    type: str
    room_id: str
    manufacturer: str
    model: str
    gateway: str
    placement: Placement | None
    connectivity: Connectivity
    last_seen: datetime | None
    capabilities: list[CapabilityView]


def _age_s(now: datetime, obs: Observation) -> float:
    return (now - obs.observed_at).total_seconds()


class PendingCommand(BaseModel):
    command_id: str
    value: CommandValue
    dispatched_at: datetime | None = None  # server time just before the gateway call
    # IDs a report may carry to prove it was caused by this command.
    correlation_ids: frozenset[str] = frozenset()


class StateStore:
    """Latest-known state per device: a cache of what gateways reported. Deliberately not
    persisted; after a restart the gateway snapshot is the source of truth."""

    def __init__(self) -> None:
        self._observations: dict[str, dict[CapabilityKind, Observation]] = {}
        self._connectivity: dict[str, tuple[Connectivity, datetime]] = {}
        self._last_seen: dict[str, datetime] = {}

    def apply(
        self,
        device_id: str,
        values: Mapping[CapabilityKind, CommandValue],
        source: Source,
        observed_at: datetime,
        connectivity: Connectivity | None = None,
        correlation_id: str | None = None,
    ) -> frozenset[CapabilityKind]:
        """Apply a report and return the capabilities whose value was accepted.

        Out-of-order data (older than what is already known) is ignored, for values and for
        connectivity alike. Callers must only act on the accepted capabilities.
        """
        per_device = self._observations.setdefault(device_id, {})
        accepted: set[CapabilityKind] = set()
        for kind, value in values.items():
            current = per_device.get(kind)
            if current is not None and current.observed_at > observed_at:
                continue
            per_device[kind] = Observation(
                value=value, source=source, observed_at=observed_at, correlation_id=correlation_id
            )
            accepted.add(kind)
        new_conn = connectivity or (Connectivity.ONLINE if values else None)
        if new_conn is not None:
            current_conn = self._connectivity.get(device_id)
            if current_conn is None or current_conn[1] <= observed_at:
                self._connectivity[device_id] = (new_conn, observed_at)
        if accepted or connectivity is Connectivity.ONLINE:
            previous = self._last_seen.get(device_id)
            if previous is None or observed_at > previous:
                self._last_seen[device_id] = observed_at
        return frozenset(accepted)

    def observation(self, device_id: str, kind: CapabilityKind) -> Observation | None:
        return self._observations.get(device_id, {}).get(kind)

    def connectivity(self, device_id: str) -> Connectivity:
        entry = self._connectivity.get(device_id)
        return entry[0] if entry else Connectivity.UNKNOWN

    def view(
        self,
        device: Device,
        now: datetime,
        pending: Mapping[CapabilityKind, PendingCommand] | None = None,
    ) -> DeviceView:
        pending = pending or {}
        connectivity = self.connectivity(device.id)
        views = []
        for cap in device.capabilities:
            obs = self.observation(device.id, cap.kind)
            in_flight = pending.get(cap.kind)
            if connectivity is Connectivity.OFFLINE:
                status = DisplayStatus.OFFLINE
            elif in_flight is not None:
                status = DisplayStatus.PENDING
            elif obs is None:
                status = DisplayStatus.UNKNOWN
            elif (
                cap.max_age_s is not None
                and (now - obs.observed_at).total_seconds() > cap.max_age_s
            ):
                status = DisplayStatus.STALE
            elif obs.source is Source.SIMULATED:
                status = DisplayStatus.SIMULATED
            else:
                status = DisplayStatus.CONFIRMED
            views.append(
                CapabilityView(
                    kind=cap.kind,
                    status=status,
                    value=obs.value if obs else None,
                    source=obs.source if obs else None,
                    observed_at=obs.observed_at if obs else None,
                    pending_value=in_flight.value if in_flight else None,
                    pending_command_id=in_flight.command_id if in_flight else None,
                )
            )
        return DeviceView(
            id=device.id,
            name=device.name,
            type=device.type.value,
            room_id=device.room_id,
            manufacturer=device.manufacturer,
            model=device.model,
            gateway=device.gateway,
            placement=device.placement,
            connectivity=connectivity,
            last_seen=self._last_seen.get(device.id),
            capabilities=views,
        )
