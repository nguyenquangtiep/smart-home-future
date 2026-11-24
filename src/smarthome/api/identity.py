"""Who is calling, and in which home.

Authentication happens once, in ``security.authenticate_request`` (session cookie, or the
development-only ``X-Dev-User`` header in local/test). This module turns the authenticated user
into an ``Actor`` for one home by looking the membership up *now*, on every request, so a
removal or demotion takes effect immediately.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from smarthome.api.security import get_principal
from smarthome.auth.service import Principal
from smarthome.control.execution import ControlService
from smarthome.control.policy import Actor, Origin


def get_control(request: Request) -> ControlService:
    return request.app.state.control


async def current_actor(
    home_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    control: Annotated[ControlService, Depends(get_control)],
) -> Actor:
    member = await control.member(home_id, principal.user_id)
    if member is None:
        # 404, not 403: do not reveal whether a home exists to non-members.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Không tìm thấy nhà."},
        )
    return Actor(user_id=member.user_id, home_id=home_id, role=member.role, origin=Origin.UI)


ActorDep = Annotated[Actor, Depends(current_actor)]
ControlDep = Annotated[ControlService, Depends(get_control)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
