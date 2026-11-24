"""Who is calling, and in which home.

There is no real authentication yet (task P0-AUTH). Until then a development-only identity is
read from the ``X-Dev-User`` header, and only when ``SMARTHOME_DEV_IDENTITY_ENABLED=true``.
With the flag off (the default) every home-scoped endpoint answers 401.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from smarthome.control.execution import ControlService
from smarthome.control.policy import Actor, Origin


def get_control(request: Request) -> ControlService:
    return request.app.state.control


def current_actor(
    home_id: str,
    request: Request,
    control: Annotated[ControlService, Depends(get_control)],
    x_dev_user: Annotated[str | None, Header()] = None,
) -> Actor:
    if not request.app.state.settings.dev_identity_enabled:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_NOT_CONFIGURED", "message": "Chưa cấu hình xác thực."},
        )
    if not x_dev_user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Thiếu danh tính người dùng."},
        )
    member = control.member(home_id, x_dev_user)
    if member is None:
        # 404, not 403: do not reveal whether a home exists to non-members.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Không tìm thấy nhà."},
        )
    return Actor(user_id=member.user_id, home_id=home_id, role=member.role, origin=Origin.UI)


ActorDep = Annotated[Actor, Depends(current_actor)]
ControlDep = Annotated[ControlService, Depends(get_control)]
