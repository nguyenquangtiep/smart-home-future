"""Request authentication: default-deny, session cookie, Origin and CSRF checks.

``authenticate_request`` runs as an application-wide dependency, so a route added later is
protected without anyone remembering to protect it (LLD D-07-10). The order of checks is the one
in LLD figure 4: valid session (401), then for state-changing methods Origin and CSRF (403).
Membership of a home is checked afterwards, per request, in ``identity.current_actor``.
"""

from fastapi import HTTPException, Request, status

from smarthome.auth.service import AuthService, Principal, csrf_matches
from smarthome.core.config import Settings

COOKIE_NAME = "smarthome_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Reachable without a session. Logout is idempotent: with no session it answers 204.
LOGIN_ROUTE = ("POST", "/api/v1/auth/login")
PUBLIC_ROUTES = frozenset(
    {
        ("GET", "/health"),
        LOGIN_ROUTE,
        ("POST", "/api/v1/auth/logout"),
    }
)


def error(code_: int, code: str, message: str) -> HTTPException:
    return HTTPException(code_, detail={"code": code, "message": message})


def origin_allowed(request: Request, settings: Settings) -> bool:
    """The Origin header must name an allowed origin. Without a configured list (local/test
    only) the request's own origin is accepted."""
    origin = request.headers.get("origin")
    if not origin:
        return False
    if settings.allowed_origins:
        return origin in settings.allowed_origins
    own = f"{request.url.scheme}://{request.headers.get('host', '')}"
    return origin == own


def check_origin(request: Request, settings: Settings) -> None:
    if not origin_allowed(request, settings):
        raise error(status.HTTP_403_FORBIDDEN, "ORIGIN_FORBIDDEN", "Origin không được phép.")


def check_csrf(request: Request, principal: Principal) -> None:
    check_origin(request, request.app.state.settings)
    supplied = request.headers.get(CSRF_HEADER)
    if principal.csrf_token is None or not csrf_matches(principal.csrf_token, supplied):
        raise error(status.HTTP_403_FORBIDDEN, "CSRF_FAILED", "Thiếu hoặc sai mã CSRF.")


def client_ip(request: Request) -> str | None:
    """The peer address. Forwarded headers are only honoured by the proxy middleware, which
    is installed solely for the proxies listed in SMARTHOME_TRUSTED_PROXY_IPS."""
    return request.client.host if request.client else None


async def authenticate_request(request: Request) -> None:
    """Attach ``request.state.principal`` or reject with 401/403."""
    settings: Settings = request.app.state.settings
    service: AuthService | None = request.app.state.auth
    key = (request.method, request.url.path)
    public = key in PUBLIC_ROUTES

    if key == LOGIN_ROUTE:
        check_origin(request, settings)  # login needs a good Origin but no CSRF token yet

    principal: Principal | None = None
    token = request.cookies.get(COOKIE_NAME)
    if service is not None and token:
        principal = await service.authenticate(token)
        if principal is not None and request.method not in SAFE_METHODS and key != LOGIN_ROUTE:
            check_csrf(request, principal)

    if principal is None and settings.dev_identity_enabled:
        dev_user = request.headers.get("x-dev-user")
        if dev_user:
            principal = Principal(dev_user, f"{dev_user}@dev.invalid", dev_user, dev=True)

    if principal is None and not public:
        if service is None and not settings.dev_identity_enabled:
            raise error(401, "AUTH_NOT_CONFIGURED", "Chưa cấu hình xác thực.")
        raise error(401, "UNAUTHENTICATED", "Cần đăng nhập.")
    request.state.principal = principal


def get_principal(request: Request) -> Principal:
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None:
        raise error(401, "UNAUTHENTICATED", "Cần đăng nhập.")
    return principal
