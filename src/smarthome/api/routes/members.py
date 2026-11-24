"""Membership management: owner only (LLD P0-07 sections 3 and 6)."""

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from smarthome.api.identity import ActorDep, PrincipalDep
from smarthome.api.security import error
from smarthome.auth import passwords
from smarthome.auth.service import AuthService
from smarthome.auth.store import (
    EmailInUseError,
    LastOwnerError,
    MemberNotFoundError,
    MemberRow,
)
from smarthome.domain.capabilities import Role

router = APIRouter(prefix="/api/v1/homes/{home_id}/members", tags=["members"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: Role
    created_at: datetime


class AddMemberBody(BaseModel):
    email: Annotated[str, Field(max_length=254)]
    display_name: Annotated[str, Field(min_length=1, max_length=100)]
    role: Role
    password: Annotated[str, Field(min_length=1, max_length=1024)]

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL.match(value):
            raise ValueError("email không hợp lệ")
        return value


class RoleBody(BaseModel):
    role: Role


def _service(request: Request) -> AuthService:
    service: AuthService | None = request.app.state.auth
    if service is None:
        raise error(
            status.HTTP_501_NOT_IMPLEMENTED,
            "AUTH_NOT_CONFIGURED",
            "Quản lý thành viên cần PostgreSQL (SMARTHOME_DATABASE_URL).",
        )
    return service


def _owner_only(actor: ActorDep) -> None:
    if actor.role is not Role.OWNER:
        raise error(status.HTTP_403_FORBIDDEN, "FORBIDDEN_ROLE", "Chỉ chủ nhà quản lý thành viên.")


def _out(row: MemberRow) -> MemberOut:
    return MemberOut(
        user_id=row.user_id,
        email=row.email,
        display_name=row.display_name,
        role=row.role,
        created_at=row.created_at,
    )


def _not_found() -> HTTPException:
    return error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Không tìm thấy thành viên.")


def _last_owner() -> HTTPException:
    return error(status.HTTP_409_CONFLICT, "LAST_OWNER", "Nhà phải còn ít nhất một chủ nhà.")


@router.get("", response_model=list[MemberOut])
async def list_members(home_id: str, actor: ActorDep, request: Request) -> list[MemberOut]:
    _owner_only(actor)
    return [_out(r) for r in await _service(request).store.list_members(actor.home_id)]


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    home_id: str, body: AddMemberBody, actor: ActorDep, request: Request, principal: PrincipalDep
) -> MemberOut:
    _owner_only(actor)
    service = _service(request)
    try:
        checked = passwords.check_policy(body.password, body.email)
    except passwords.WeakPasswordError as exc:
        raise error(422, "WEAK_PASSWORD", exc.reason) from None
    hashed = await passwords.hash_password(checked)
    try:
        row = await service.store.add_member(
            actor.home_id,
            body.email,
            body.display_name,
            body.role,
            hashed,
            principal.user_id,
            service.now(),
        )
    except EmailInUseError:
        raise error(
            status.HTTP_409_CONFLICT, "EMAIL_IN_USE", "Email này đã có tài khoản."
        ) from None
    return _out(row)


@router.patch("/{user_id}", response_model=MemberOut)
async def change_role(
    home_id: str,
    user_id: str,
    body: RoleBody,
    actor: ActorDep,
    request: Request,
    principal: PrincipalDep,
) -> MemberOut:
    _owner_only(actor)
    service = _service(request)
    try:
        row = await service.store.change_role(
            actor.home_id, user_id, body.role, principal.user_id, service.now()
        )
    except MemberNotFoundError:
        raise _not_found() from None
    except LastOwnerError:
        raise _last_owner() from None
    return _out(row)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    home_id: str, user_id: str, actor: ActorDep, request: Request, principal: PrincipalDep
) -> None:
    _owner_only(actor)
    service = _service(request)
    try:
        await service.store.remove_member(actor.home_id, user_id, principal.user_id, service.now())
    except MemberNotFoundError:
        raise _not_found() from None
    except LastOwnerError:
        raise _last_owner() from None
