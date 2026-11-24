"""Home, device and command endpoints. All are scoped to one home and need a member identity."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel

from smarthome.api.identity import ActorDep, ControlDep
from smarthome.control.audit import AuditEvent
from smarthome.control.execution import (
    CommandRecord,
    CommandStatus,
    ConfirmationError,
    IdempotencyConflictError,
    NotFoundError,
    StoreUnavailableError,
)
from smarthome.control.policy import CommandRequest
from smarthome.domain.capabilities import Role
from smarthome.domain.models import Home
from smarthome.domain.state import DeviceView

router = APIRouter(prefix="/api/v1/homes/{home_id}", tags=["homes"])

# Policy reason -> HTTP status for rejected commands.
_REJECT_STATUS = {
    "WRONG_HOME": status.HTTP_404_NOT_FOUND,
    "FORBIDDEN_ROLE": status.HTTP_403_FORBIDDEN,
    "AUTOMATION_NOT_ALLOWED": status.HTTP_403_FORBIDDEN,
    "DEVICE_OFFLINE": status.HTTP_409_CONFLICT,
    "DEVICE_BUSY": status.HTTP_409_CONFLICT,
}


class HomeResponse(BaseModel):
    home: Home
    devices: list[DeviceView]


class ConfirmationBody(BaseModel):
    approve: bool


def _store_unavailable(exc: StoreUnavailableError) -> HTTPException:
    # Nothing was sent to the device. Details stay in the server log, not the response.
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": exc.code, "message": "Không ghi được lệnh; lệnh chưa được gửi."},
    )


def _not_found(exc: NotFoundError) -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": str(exc)}
    )


@router.get("", response_model=HomeResponse)
def get_home(home_id: str, actor: ActorDep, control: ControlDep) -> HomeResponse:
    home = control.home(actor.home_id)
    return HomeResponse(home=home, devices=[control.view(d) for d in control.home_devices(home.id)])


@router.get("/devices", response_model=list[DeviceView])
def list_devices(home_id: str, actor: ActorDep, control: ControlDep) -> list[DeviceView]:
    return [control.view(d) for d in control.home_devices(actor.home_id)]


@router.get("/devices/{device_id}", response_model=DeviceView)
def get_device(home_id: str, device_id: str, actor: ActorDep, control: ControlDep) -> DeviceView:
    try:
        return control.view(control.device_in_home(actor.home_id, device_id))
    except NotFoundError as exc:
        raise _not_found(exc) from None


@router.post("/commands", response_model=CommandRecord, status_code=status.HTTP_202_ACCEPTED)
async def submit_command(
    home_id: str,
    body: CommandRequest,
    actor: ActorDep,
    control: ControlDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> CommandRecord:
    """Accept a command. 202 means accepted for processing, not that the device changed:
    poll ``GET /commands/{id}`` until ``status`` is terminal.

    Same ``Idempotency-Key`` + same request returns the original command with header
    ``Idempotent-Replayed: true``; same key + different request returns 409."""
    try:
        result = await control.submit_command(actor, body, idempotency_key)
    except NotFoundError as exc:
        raise _not_found(exc) from None
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": exc.code, "message": str(exc)}
        ) from None
    except StoreUnavailableError as exc:
        raise _store_unavailable(exc) from None
    record = result.record
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    if record.status is CommandStatus.REJECTED:
        code = record.reasons[0].code if record.reasons else ""
        response.status_code = _REJECT_STATUS.get(code, 422)
    return record


@router.get("/commands/{command_id}", response_model=CommandRecord)
async def get_command(
    home_id: str, command_id: str, actor: ActorDep, control: ControlDep
) -> CommandRecord:
    try:
        return await control.get_command(actor.home_id, command_id)
    except NotFoundError as exc:
        raise _not_found(exc) from None


@router.post("/commands/{command_id}/confirmation", response_model=CommandRecord)
async def confirm_command(
    home_id: str,
    command_id: str,
    body: ConfirmationBody,
    actor: ActorDep,
    control: ControlDep,
) -> CommandRecord:
    try:
        return await control.confirm(actor, command_id, body.approve)
    except NotFoundError as exc:
        raise _not_found(exc) from None
    except StoreUnavailableError as exc:
        raise _store_unavailable(exc) from None
    except ConfirmationError as exc:
        code = status.HTTP_403_FORBIDDEN if exc.code == "FORBIDDEN_ROLE" else 409
        raise HTTPException(code, detail={"code": exc.code, "message": str(exc)}) from None


@router.get("/audit", response_model=list[AuditEvent])
async def list_audit(home_id: str, actor: ActorDep, control: ControlDep) -> list[AuditEvent]:
    if actor.role is not Role.OWNER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN_ROLE", "message": "Chỉ chủ nhà xem được nhật ký."},
        )
    return await control.list_audit(actor.home_id)
