"""Execution engine: the single path from a command request to a verified device state.

Lifecycle (``CommandStatus``)::

    REQUESTED -> policy deny                     -> REJECTED
    REQUESTED -> policy needs confirmation       -> AWAITING_CONFIRMATION
        AWAITING_CONFIRMATION -> owner rejects   -> CANCELLED
        AWAITING_CONFIRMATION -> TTL passes      -> EXPIRED
        AWAITING_CONFIRMATION -> owner approves  -> REQUESTED (atomic claim) -> policy re-run
    REQUESTED -> policy allow                    -> DISPATCHING (persisted before the gateway call)
    DISPATCHING -> device busy                   -> REJECTED
    DISPATCHING -> gateway error / timeout       -> FAILED
    DISPATCHING -> gateway accepted              -> DISPATCHED
    DISPATCHED  -> report passes verification    -> SUCCEEDED
    DISPATCHED  -> no verifying report in time   -> TIMED_OUT
    REQUESTED | DISPATCHING | DISPATCHED -> backend restart -> INTERRUPTED (never resent)

Verification policy (a report verifies a pending command only if all hold):

1. the state store accepted the value (it is not older than what is already known);
2. the value matches the commanded value;
3. if the report carries a correlation id, it must be one of this command's ids
   (``Verification.CORRELATED``); a foreign correlation id never verifies;
4. without correlation, the report must not be observed before the dispatch started
   (minus a small clock-skew allowance) (``Verification.STATE_MATCH``). The timestamp is a
   staleness filter only, not proof of causation, hence the weaker label.

TIMED_OUT and INTERRUPTED mean "unverified", not "failed": the device may or may not have changed.
A gateway that does not acknowledge in time is treated the same way (the command may have been
delivered): the engine keeps waiting for evidence instead of declaring FAILED. FAILED is only
used when the gateway explicitly refused the command.

Persistence rule: writes that precede a side effect (key reservation, the DISPATCHING row,
the approval claim) must succeed or nothing is sent. Writes after the gateway accepted a
command are best effort: a storage error is logged and never blocks verification, timeouts
or later commands. A row left at DISPATCHING becomes INTERRUPTED at the next start.
"""

import asyncio
import uuid
from collections.abc import Iterable, Mapping
from datetime import timedelta

from pydantic import BaseModel

from smarthome.control import policy
from smarthome.control.audit import AuditEvent, AuditEventIn
from smarthome.control.commands import (
    IN_FLIGHT,
    TERMINAL,
    CommandRecord,
    CommandStatus,
    Verification,
    request_fingerprint,
)
from smarthome.control.policy import Actor, CommandRequest, Outcome, Reason
from smarthome.core.logging import get_logger
from smarthome.domain.capabilities import Capability, CapabilityKind, CommandValue
from smarthome.domain.models import Device, Home, Member
from smarthome.domain.state import (
    Clock,
    Connectivity,
    DeviceView,
    PendingCommand,
    StateStore,
    utc_now,
)
from smarthome.gateway.base import DeviceGateway, GatewayError, StateReport
from smarthome.persistence.base import CommandStore, StoreConflictError
from smarthome.persistence.memory import MemoryCommandStore

__all__ = [
    "TERMINAL",
    "CommandRecord",
    "CommandStatus",
    "ConfirmationError",
    "ControlService",
    "IdempotencyConflictError",
    "NotFoundError",
    "StoreUnavailableError",
    "SubmitResult",
]

logger = get_logger("control.execution")


class NotFoundError(Exception):
    pass


class ConfirmationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StoreUnavailableError(Exception):
    """The durable store could not record the command before dispatch: nothing was sent."""

    code = "STORE_UNAVAILABLE"


class IdempotencyConflictError(Exception):
    """The idempotency key was already used for a different request."""

    code = "IDEMPOTENCY_KEY_REUSED"


class SubmitResult(BaseModel):
    record: CommandRecord
    replayed: bool = False  # True when an earlier command was returned for the same key


class ControlService:
    """Owns command lifecycle, pending state and verification for one or more homes."""

    def __init__(
        self,
        homes: Iterable[Home],
        members: Iterable[Member],
        devices: Iterable[Device],
        gateways: Mapping[str, DeviceGateway],
        state: StateStore | None = None,
        store: CommandStore | None = None,
        verify_timeout_s: float = 5.0,
        dispatch_timeout_s: float = 3.0,
        confirmation_ttl_s: float = 120.0,
        idempotency_ttl_s: float = 86_400.0,
        clock_skew_s: float = 1.0,
        clock: Clock = utc_now,
    ) -> None:
        self.homes = {h.id: h for h in homes}
        self.members = {(m.home_id, m.user_id): m for m in members}
        self.devices = {d.id: d for d in devices}
        self.gateways = dict(gateways)
        self.state = state or StateStore()
        self.store: CommandStore = store or MemoryCommandStore()
        self.verify_timeout_s = verify_timeout_s
        self.dispatch_timeout_s = dispatch_timeout_s
        self.confirmation_ttl = timedelta(seconds=confirmation_ttl_s)
        self.idempotency_ttl = timedelta(seconds=idempotency_ttl_s)
        self.clock_skew = timedelta(seconds=clock_skew_s)
        self._clock = clock
        # Records being worked on in this process. Same objects the caller got back.
        self._active: dict[str, CommandRecord] = {}
        self._pending: dict[tuple[str, CapabilityKind], PendingCommand] = {}
        self._verified: dict[str, asyncio.Event] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._submit_lock = asyncio.Lock()
        self._interrupted: list[CommandRecord] = []
        for gw in self.gateways.values():
            gw.subscribe(self.on_report)

    # ---- queries -------------------------------------------------------------------------

    def member(self, home_id: str, user_id: str) -> Member | None:
        return self.members.get((home_id, user_id))

    def home(self, home_id: str) -> Home:
        try:
            return self.homes[home_id]
        except KeyError:
            raise NotFoundError(f"home {home_id}") from None

    def home_devices(self, home_id: str) -> list[Device]:
        return [d for d in self.devices.values() if d.home_id == home_id]

    def device_in_home(self, home_id: str, device_id: str) -> Device:
        device = self.devices.get(device_id)
        # Same error for "missing" and "other home" so IDs of other homes are not revealed.
        if device is None or device.home_id != home_id:
            raise NotFoundError(f"device {device_id}")
        return device

    def view(self, device: Device) -> DeviceView:
        pending = {kind: p for (dev_id, kind), p in self._pending.items() if dev_id == device.id}
        return self.state.view(device, self._clock(), pending)

    async def get_command(self, home_id: str, command_id: str) -> CommandRecord:
        record = self._active.get(command_id) or await self.store.get(command_id)
        if record is None or record.home_id != home_id:
            raise NotFoundError(f"command {command_id}")
        await self._expire_if_due(record)
        return record

    async def list_audit(self, home_id: str, limit: int = 100) -> list[AuditEvent]:
        return await self.store.list_audit(home_id, limit)

    # ---- commands ------------------------------------------------------------------------

    async def submit(
        self, actor: Actor, request: CommandRequest, idempotency_key: str | None = None
    ) -> CommandRecord:
        return (await self.submit_command(actor, request, idempotency_key)).record

    async def submit_command(
        self, actor: Actor, request: CommandRequest, idempotency_key: str | None = None
    ) -> SubmitResult:
        device = self.device_in_home(actor.home_id, request.device_id)
        fingerprint = request_fingerprint(actor, request) if idempotency_key else None
        # Reserve the key and create the record as one step: concurrent retries see either
        # nothing or the finished reservation, never a half-created command.
        async with self._submit_lock:
            now = self._clock()
            record = CommandRecord(
                id=str(uuid.uuid4()),
                home_id=actor.home_id,
                device_id=device.id,
                capability=request.capability,
                value=request.value,
                actor_id=actor.user_id,
                origin=actor.origin,
                status=CommandStatus.REQUESTED,
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
            )
            hit = await self.store.create(
                record,
                fingerprint,
                now + self.idempotency_ttl if idempotency_key else None,
                now,
            )
            if hit is not None:
                if hit.fingerprint != fingerprint:
                    await self._audit_raw(
                        actor, "idempotency.conflict", device.id, hit.record.id, {}
                    )
                    raise IdempotencyConflictError(
                        "Idempotency-Key đã được dùng cho một yêu cầu khác."
                    )
                existing = self._active.get(hit.record.id, hit.record)
                await self._expire_if_due(existing)
                return SubmitResult(record=existing, replayed=True)
            self._active[record.id] = record

        await self._audit(record, "command.requested", value=request.value)
        decision = policy.evaluate(actor, device, request, self.view(device))
        if decision.outcome is Outcome.DENY:
            await self._finish(record, CommandStatus.REJECTED, reasons=decision.reasons)
        elif decision.outcome is Outcome.REQUIRE_CONFIRMATION:
            record.status = CommandStatus.AWAITING_CONFIRMATION
            record.reasons = decision.reasons
            record.confirmation_expires_at = now + self.confirmation_ttl
            record.updated_at = now
            await self.store.save(record)
            await self._audit(record, "command.awaiting_confirmation")
        else:
            await self._dispatch(record, device)
        return SubmitResult(record=record)

    async def confirm(self, approver: Actor, command_id: str, approve: bool) -> CommandRecord:
        record = await self.get_command(approver.home_id, command_id)
        device = self.device_in_home(record.home_id, record.device_id)
        if record.status is not CommandStatus.AWAITING_CONFIRMATION:
            raise ConfirmationError("NOT_AWAITING", f"Lệnh đang ở trạng thái {record.status}.")
        if not policy.may_confirm(approver, device):
            await self._audit(record, "confirmation.forbidden", actor_id=approver.user_id)
            raise ConfirmationError("FORBIDDEN_ROLE", "Chỉ chủ nhà mới xác nhận được thao tác này.")

        # Atomic, single-use claim bound to this command id: a concurrent or replayed
        # confirmation, or one after expiry, finds the status already changed and fails.
        new_status = CommandStatus.REQUESTED if approve else CommandStatus.CANCELLED
        now = self._clock()
        if not await self.store.claim(
            record.id, CommandStatus.AWAITING_CONFIRMATION, new_status, approver.user_id, now
        ):
            raise ConfirmationError("NOT_AWAITING", "Yêu cầu xác nhận không còn hiệu lực.")
        record.status = new_status
        record.confirmed_by = approver.user_id
        record.updated_at = now
        self._active[record.id] = record

        if not approve:
            await self._finish(
                record,
                CommandStatus.CANCELLED,
                reasons=[Reason(code="REJECTED_BY_OWNER", message="Chủ nhà đã từ chối.")],
            )
            return record

        await self._audit(record, "confirmation.approved", actor_id=approver.user_id)
        # Approval is not authorization: re-run the policy with the requester's own role
        # and the current device state.
        requester = self.member(record.home_id, record.actor_id)
        if requester is None:
            await self._finish(
                record,
                CommandStatus.REJECTED,
                reasons=[
                    Reason(code="REQUESTER_REMOVED", message="Người yêu cầu không còn quyền.")
                ],
            )
            return record
        actor = Actor(
            user_id=requester.user_id,
            home_id=record.home_id,
            role=requester.role,
            origin=record.origin,
        )
        request = CommandRequest(
            device_id=record.device_id, capability=record.capability, value=record.value
        )
        decision = policy.evaluate(actor, device, request, self.view(device))
        if decision.outcome is Outcome.DENY:
            await self._finish(record, CommandStatus.REJECTED, reasons=decision.reasons)
            return record
        await self._dispatch(record, device)
        return record

    # ---- startup recovery ----------------------------------------------------------------

    async def recover(self) -> list[CommandRecord]:
        """Mark commands left unfinished by a previous process as INTERRUPTED. Never resend."""
        recovered = []
        for record in await self.store.list_unfinished():
            await self._finish(
                record,
                CommandStatus.INTERRUPTED,
                reasons=[
                    Reason(
                        code="SERVER_RESTARTED",
                        message="Máy chủ khởi động lại khi lệnh đang xử lý; kết quả chưa xác định.",
                    )
                ],
            )
            recovered.append(record)
        self._interrupted.extend(recovered)
        return recovered

    async def reconcile_interrupted(self) -> None:
        """After the first gateway snapshot, record whether each interrupted command's target
        value is what the device now reports. Informational only: status stays INTERRUPTED."""
        for record in self._interrupted:
            device = self.devices.get(record.device_id)
            cap = device.capability(record.capability) if device else None
            obs = self.state.observation(record.device_id, record.capability)
            if (
                cap is None
                or obs is None
                or self.state.connectivity(record.device_id) is not Connectivity.ONLINE
            ):
                code, msg = "STATE_UNKNOWN_AFTER_RESTART", "Chưa biết trạng thái thiết bị."
            elif cap.matches(record.value, obs.value):
                code, msg = "STATE_MATCHES_AFTER_RESTART", "Thiết bị hiện có giá trị của lệnh."
            else:
                code, msg = "STATE_DIFFERS_AFTER_RESTART", "Thiết bị hiện khác giá trị của lệnh."
            record.reasons = [*record.reasons, Reason(code=code, message=msg)]
            record.updated_at = self._clock()
            await self.store.save(record)
            await self._audit(record, "command.reconciled", state=code)
        self._interrupted.clear()

    # ---- gateway side --------------------------------------------------------------------

    async def on_report(self, report: StateReport) -> None:
        accepted = self.state.apply(
            report.device_id,
            report.values,
            report.source,
            report.observed_at,
            report.connectivity,
            report.correlation_id,
        )
        device = self.devices.get(report.device_id)
        if device is None:
            return
        for kind in accepted:
            value = report.values[kind]
            pending = self._pending.get((device.id, kind))
            cap = device.capability(kind)
            if pending is None or cap is None:
                continue
            method = self._verification(pending, cap, report, value)
            if method is None:
                continue
            record = self._active.get(pending.command_id)
            if record is None or record.status not in IN_FLIGHT:
                continue
            await self._succeed(record, value, method)

    def _verification(
        self, pending: PendingCommand, cap: Capability, report: StateReport, value: CommandValue
    ) -> Verification | None:
        if not cap.matches(pending.value, value):
            return None
        if report.correlation_id is not None:
            if report.correlation_id in pending.correlation_ids:
                return Verification.CORRELATED
            return None  # caused by something else (another command, another client)
        if pending.dispatched_at is None:
            return None
        if report.observed_at < pending.dispatched_at - self.clock_skew:
            return None  # describes the device before this command was sent
        return Verification.STATE_MATCH

    async def wait_until_terminal(self, command_id: str, timeout_s: float) -> CommandRecord:
        """Test/agent helper: wait for a command to leave its in-flight state."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        record = self._active.get(command_id) or await self.store.get(command_id)
        if record is None:
            raise NotFoundError(f"command {command_id}")
        while record.status not in TERMINAL | {CommandStatus.AWAITING_CONFIRMATION}:
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.01)
        return record

    # ---- internals -----------------------------------------------------------------------

    async def _dispatch(self, record: CommandRecord, device: Device) -> None:
        key = (device.id, record.capability)
        if key in self._pending:
            await self._finish(record, CommandStatus.REJECTED, reasons=[_busy(device)])
            return
        gateway = self.gateways.get(device.gateway)
        if gateway is None:
            await self._finish(
                record,
                CommandStatus.FAILED,
                reasons=[
                    Reason(code="NO_GATEWAY", message=f"Chưa cấu hình gateway {device.gateway}.")
                ],
            )
            return

        now = self._clock()
        record.status = CommandStatus.DISPATCHING
        record.updated_at = now
        # Register before sending so a very fast report can still be matched.
        self._pending[key] = PendingCommand(
            command_id=record.id,
            value=record.value,
            dispatched_at=now,
            correlation_ids=frozenset({record.id}),
        )
        self._verified[record.id] = asyncio.Event()
        try:
            # Persist "in flight" BEFORE the side effect: a crash after this point leaves a
            # DISPATCHING row that recovery turns into INTERRUPTED, so it is never resent.
            await self.store.save(record)
        except StoreConflictError:
            self._release(record)
            self._verified.pop(record.id, None)
            await self._finish(record, CommandStatus.REJECTED, reasons=[_busy(device)])
            return
        except Exception as exc:
            # Could not record "in flight": do not send anything.
            self._release(record)
            self._verified.pop(record.id, None)
            await self._finish(
                record,
                CommandStatus.FAILED,
                reasons=[
                    Reason(code="STORE_UNAVAILABLE", message="Không ghi được lệnh; chưa gửi.")
                ],
            )
            raise StoreUnavailableError(str(exc)) from exc

        gateway_corr: str | None = None
        try:
            gateway_corr = await asyncio.wait_for(
                gateway.send(device, record.capability, record.value, record.id),
                self.dispatch_timeout_s,
            )
        except GatewayError as exc:
            if record.status is not CommandStatus.DISPATCHING:
                return  # a verifying report already arrived: evidence wins
            self._release(record)
            self._verified.pop(record.id, None)
            await self._finish(
                record,
                CommandStatus.FAILED,
                reasons=[Reason(code="GATEWAY_ERROR", message=str(exc) or "Gateway từ chối lệnh.")],
            )
            return
        except TimeoutError:
            if record.status is not CommandStatus.DISPATCHING:
                return  # verified while the ack was pending; never overwrite SUCCEEDED
            # No ack is not proof of non-delivery: keep waiting for a verifying report.
            record.reasons = [
                Reason(
                    code="GATEWAY_ACK_TIMEOUT",
                    message="Gateway không xác nhận kịp; lệnh có thể đã được gửi.",
                )
            ]

        if gateway_corr is not None:
            record.correlation_id = gateway_corr
            await self._adopt_gateway_correlation(record, device, gateway_corr)
        if record.status is not CommandStatus.DISPATCHING:  # verified during send
            return
        record.status = CommandStatus.DISPATCHED
        record.dispatched_at = record.updated_at = self._clock()
        await self._save_after_side_effect(record)
        await self._audit(
            record,
            "command.dispatched",
            correlation=gateway_corr is not None,
            reasons=[r.code for r in record.reasons] or None,
        )
        task = asyncio.create_task(self._await_verification(record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _adopt_gateway_correlation(
        self, record: CommandRecord, device: Device, gateway_corr: str
    ) -> None:
        key = (device.id, record.capability)
        pending = self._pending.get(key)
        if pending is None or pending.command_id != record.id:
            return
        if gateway_corr not in pending.correlation_ids:
            self._pending[key] = pending.model_copy(
                update={"correlation_ids": pending.correlation_ids | {gateway_corr}}
            )
        # The correlated report may have arrived before send() returned the gateway's id.
        obs = self.state.observation(device.id, record.capability)
        cap = device.capability(record.capability)
        if (
            obs is not None
            and cap is not None
            and obs.correlation_id == gateway_corr
            and cap.matches(record.value, obs.value)
            and record.status in IN_FLIGHT
        ):
            await self._succeed(record, obs.value, Verification.CORRELATED)

    async def _succeed(
        self, record: CommandRecord, value: CommandValue, method: Verification
    ) -> None:
        record.verified_value = value
        record.verification = method
        self._release(record)
        event = self._verified.pop(record.id, None)
        await self._finish(record, CommandStatus.SUCCEEDED)
        if event:
            event.set()

    async def _await_verification(self, record: CommandRecord) -> None:
        event = self._verified.get(record.id)
        if event is None:
            return
        try:
            await asyncio.wait_for(event.wait(), self.verify_timeout_s)
        except TimeoutError:
            self._verified.pop(record.id, None)
            if record.status is CommandStatus.DISPATCHED:
                self._release(record)
                await self._finish(
                    record,
                    CommandStatus.TIMED_OUT,
                    reasons=[
                        *record.reasons,
                        Reason(
                            code="NOT_VERIFIED",
                            message="Thiết bị chưa báo trạng thái khớp lệnh trong thời gian chờ.",
                        ),
                    ],
                )

    def _release(self, record: CommandRecord) -> None:
        key = (record.device_id, record.capability)
        pending = self._pending.get(key)
        if pending is not None and pending.command_id == record.id:
            del self._pending[key]

    async def _expire_if_due(self, record: CommandRecord) -> None:
        if (
            record.status is CommandStatus.AWAITING_CONFIRMATION
            and record.confirmation_expires_at is not None
            and self._clock() >= record.confirmation_expires_at
        ):
            await self._finish(
                record,
                CommandStatus.EXPIRED,
                reasons=[Reason(code="CONFIRMATION_EXPIRED", message="Hết hạn xác nhận.")],
            )

    async def _finish(
        self, record: CommandRecord, status: CommandStatus, reasons: list[Reason] | None = None
    ) -> None:
        # Mutate synchronously first so concurrent coroutines see the final status at once.
        now = self._clock()
        record.status = status
        if reasons is not None:
            record.reasons = reasons
        record.updated_at = record.completed_at = now
        self._active.pop(record.id, None)
        await self._save_after_side_effect(record)
        await self._audit(
            record,
            f"command.{status.value}",
            reasons=[r.code for r in record.reasons],
            verification=record.verification.value if record.verification else None,
        )

    async def _save_after_side_effect(self, record: CommandRecord) -> None:
        """Best-effort persistence for transitions that do not gate a side effect."""
        try:
            await self.store.save(record)
        except Exception:
            logger.exception(
                "could not persist command %s (status=%s); outcome kept in memory",
                record.id,
                record.status.value,
            )

    async def _audit(self, record: CommandRecord, action: str, **detail: object) -> None:
        actor_id = detail.pop("actor_id", record.actor_id)
        await self._append_audit(
            AuditEventIn(
                at=self._clock(),
                home_id=record.home_id,
                actor_id=str(actor_id),
                origin=record.origin.value,
                action=action,
                device_id=record.device_id,
                command_id=record.id,
                detail={k: v for k, v in detail.items() if v is not None},
            )
        )

    async def _append_audit(self, event: AuditEventIn) -> None:
        try:
            await self.store.append_audit(event)
        except Exception:
            # Never let an audit write failure break control or verification; make it loud.
            logger.exception(
                "AUDIT WRITE FAILED action=%s command=%s", event.action, event.command_id
            )

    async def _audit_raw(
        self,
        actor: Actor,
        action: str,
        device_id: str,
        command_id: str | None,
        detail: dict[str, object],
    ) -> None:
        await self._append_audit(
            AuditEventIn(
                at=self._clock(),
                home_id=actor.home_id,
                actor_id=actor.user_id,
                origin=actor.origin.value,
                action=action,
                device_id=device_id,
                command_id=command_id,
                detail=detail,
            )
        )


def _busy(device: Device) -> Reason:
    return Reason(code="DEVICE_BUSY", message=f"{device.name} đang chờ xác nhận một lệnh khác.")
