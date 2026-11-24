"""Login, logout, current session and password change (LLD P0-07 section 6)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from smarthome.api.identity import PrincipalDep
from smarthome.api.security import COOKIE_NAME, client_ip, error
from smarthome.auth.passwords import WeakPasswordError
from smarthome.auth.service import (
    AuthService,
    InvalidCredentialsError,
    LoginResult,
    Principal,
    TooManyAttemptsError,
)
from smarthome.auth.store import HomeRole

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Bounded so a huge body cannot be used to burn CPU in the hasher; policy limits come later.
Secret = Annotated[str, Field(min_length=1, max_length=1024)]


class LoginBody(BaseModel):
    email: Annotated[str, Field(min_length=3, max_length=254)]
    password: Secret


class PasswordBody(BaseModel):
    current_password: Secret
    new_password: Secret


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str


class HomeOut(BaseModel):
    id: str
    name: str
    role: str


class SessionOut(BaseModel):
    user: UserOut
    homes: list[HomeOut]
    csrf_token: str


def _service(request: Request) -> AuthService:
    service: AuthService | None = request.app.state.auth
    if service is None:
        raise error(
            status.HTTP_501_NOT_IMPLEMENTED,
            "AUTH_NOT_CONFIGURED",
            "Đăng nhập cần PostgreSQL (SMARTHOME_DATABASE_URL).",
        )
    return service


def _session_out(principal: Principal, homes: list[HomeRole], csrf: str) -> SessionOut:
    return SessionOut(
        user=UserOut(
            id=principal.user_id, email=principal.email, display_name=principal.display_name
        ),
        homes=[HomeOut(id=h.home_id, name=h.name, role=h.role.value) for h in homes],
        csrf_token=csrf,
    )


def _too_many(exc: TooManyAttemptsError) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "TOO_MANY_ATTEMPTS", "message": "Quá nhiều lần thử. Hãy thử lại sau."},
        headers={"Retry-After": str(exc.retry_after_s)},
    )


def _invalid() -> HTTPException:
    return error(status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "Email hoặc mật khẩu sai.")


@router.post("/login", response_model=SessionOut)
async def login(body: LoginBody, request: Request, response: Response) -> SessionOut:
    service = _service(request)
    settings = request.app.state.settings
    try:
        result: LoginResult = await service.login(
            body.email,
            body.password,
            client_ip(request),
            current_token=request.cookies.get(COOKIE_NAME),
        )
    except InvalidCredentialsError:
        raise _invalid() from None
    except TooManyAttemptsError as exc:
        raise _too_many(exc) from None
    max_age = int((result.expires_at - service.now()).total_seconds())
    response.set_cookie(
        COOKIE_NAME,
        result.token,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return _session_out(result.principal, result.homes, result.csrf_token)


@router.get("/session", response_model=SessionOut)
async def session(request: Request, principal: PrincipalDep) -> SessionOut:
    if principal.session_id is None or principal.csrf_token is None:
        raise error(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "Cần đăng nhập.")
    homes = await _service(request).store.homes_for_user(principal.user_id)
    return _session_out(principal, homes, principal.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is not None and principal.session_id is not None:
        await _service(request).logout(principal)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    out.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return out


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordBody, request: Request, principal: PrincipalDep) -> None:
    if principal.session_id is None:
        raise error(status.HTTP_403_FORBIDDEN, "NO_SESSION", "Cần phiên đăng nhập thật.")
    try:
        await _service(request).change_password(
            principal, body.current_password, body.new_password, client_ip(request)
        )
    except InvalidCredentialsError:
        raise _invalid() from None
    except TooManyAttemptsError as exc:
        raise _too_many(exc) from None
    except WeakPasswordError as exc:
        raise error(422, "WEAK_PASSWORD", exc.reason) from None
