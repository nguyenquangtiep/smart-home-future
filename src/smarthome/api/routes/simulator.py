"""Simulator-only control route. Registered only in local/test environments (ADR 0005)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from smarthome.api.identity import ActorDep, ControlDep
from smarthome.control.execution import NotFoundError
from smarthome.domain.capabilities import Role
from smarthome.gateway.simulator import SimBehavior, SimulatorGateway

router = APIRouter(prefix="/api/v1/homes/{home_id}", tags=["simulator"])


class SimBehaviorBody(BaseModel):
    behavior: SimBehavior


@router.put("/simulator/devices/{device_id}/behavior", status_code=status.HTTP_204_NO_CONTENT)
async def set_sim_behavior(
    home_id: str,
    device_id: str,
    body: SimBehaviorBody,
    actor: ActorDep,
    control: ControlDep,
) -> None:
    """Simulator only: make a simulated device silent, failing or offline to test error paths."""
    if actor.role is not Role.OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "FORBIDDEN"})
    try:
        device = control.device_in_home(actor.home_id, device_id)
    except NotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": str(exc)}
        ) from None
    sim = control.gateways.get(device.gateway)
    if not isinstance(sim, SimulatorGateway):
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "NOT_SIMULATED"})
    await sim.set_behavior(device.id, body.behavior)
