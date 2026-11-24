"""S1-07 and S1-08: login API, cookies, CSRF, default-deny, household isolation, member
management and dynamic RBAC (AC-07-1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16)."""

import asyncio
import hashlib
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from auth_helpers import (
    ORIGIN,
    OWNER_PASSWORD,
    SECOND_HOME,
    Stack,
    add_second_home,
    make_stack,
    run,
    settings_for,
)
from fastapi.testclient import TestClient

from smarthome.api.main import create_app
from smarthome.api.security import PUBLIC_ROUTES
from smarthome.auth.store import PostgresAuthStore
from smarthome.core.config import Settings
from smarthome.demo_home import HOME_ID

H = f"/api/v1/homes/{HOME_ID}"
PASSWORDS = {
    "owner": OWNER_PASSWORD,
    "owner2": "second-owner-passphrase",
    "member": "member-account-passphrase",
    "guest": "guest-account-passphrase",
    "other": "home-b-owner-passphrase",
}
LIGHT_ON = {"device_id": "dev-living-light", "capability": "on_off", "value": True}
HEATER_ON = {"device_id": "dev-water-heater", "capability": "on_off", "value": True}


def email_of(role: str) -> str:
    return f"{role}@example.com"


class Api:
    """A TestClient over https (the cookie is Secure) plus login/CSRF conveniences."""

    def __init__(self, app: Any, portal: Any, base: str = ORIGIN) -> None:
        self.client = TestClient(app, base_url=base)
        # Share the lifespan's event loop. A bare TestClient runs every request on a throw-away
        # loop and cancels the background tasks (simulator reports) when the request ends.
        self.client.portal = portal
        self.csrf: str | None = None

    def login(self, role: str, password: str | None = None) -> "Api":
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"email": email_of(role), "password": password or PASSWORDS[role]},
            headers={"Origin": ORIGIN},
        )
        assert resp.status_code == 200, resp.text
        self.csrf = resp.json()["csrf_token"]
        return self

    def request(self, method: str, path: str, *, csrf: bool = True, origin: bool = True, **kw: Any):  # noqa: ANN202
        headers = dict(kw.pop("headers", {}))
        if origin:
            headers.setdefault("Origin", ORIGIN)
        if csrf and self.csrf:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return self.client.request(method, path, headers=headers, **kw)

    def get(self, path: str, **kw: Any):  # noqa: ANN202
        return self.request("GET", path, csrf=False, origin=False, **kw)

    def post(self, path: str, **kw: Any):  # noqa: ANN202
        return self.request("POST", path, **kw)


class World:
    def __init__(self, stack: Stack, **setting_overrides: object) -> None:
        self.stack = stack
        self.settings = settings_for(stack.dsn, **setting_overrides)
        self.app = create_app(self.settings)
        self._entered: list[TestClient] = []

    def start(self) -> "World":
        self._lifespan = TestClient(self.app, base_url=ORIGIN)
        self._lifespan.__enter__()
        return self

    def stop(self) -> None:
        self._lifespan.__exit__(None, None, None)

    def api(self, role: str | None = None) -> Api:
        api = Api(self.app, self._lifespan.portal)
        if role:
            api.login(role)
        return api

    def audit(self, action: str) -> list[dict[str, Any]]:
        return self.stack.rows("SELECT * FROM audit_events WHERE action = %s", action)

    def auth_events(self, action: str) -> list[dict[str, Any]]:
        return self.stack.rows("SELECT * FROM auth_events WHERE action = %s", action)


@pytest.fixture
def world(stack: Stack) -> Iterator[World]:
    """Home A (demo) with owner, owner2, member, guest; home B with its own owner."""
    add_second_home(stack.dsn)
    stack.create_user(email_of("owner"), PASSWORDS["owner"], "owner")
    stack.create_user(email_of("owner2"), PASSWORDS["owner2"], "owner")
    stack.create_user(email_of("member"), PASSWORDS["member"], "member")
    stack.create_user(email_of("guest"), PASSWORDS["guest"], "guest")
    stack.create_user(email_of("other"), PASSWORDS["other"], "owner", home_id=SECOND_HOME)
    w = World(stack).start()
    yield w
    w.stop()


# ---- login and cookies ----------------------------------------------------------------------


def test_login_sets_a_hardened_cookie_and_returns_csrf_and_homes(world: World) -> None:
    """AC-07-1."""
    api = world.api()
    resp = api.client.post(
        "/api/v1/auth/login",
        json={"email": email_of("member"), "password": PASSWORDS["member"]},
        headers={"Origin": ORIGIN},
    )
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"]
    flags = [part.strip().lower() for part in cookie.split(";")]
    assert "httponly" in flags and "secure" in flags and "samesite=strict" in flags
    assert "path=/" in flags and not any(f.startswith("domain=") for f in flags)
    body = resp.json()
    assert body["homes"] == [{"id": HOME_ID, "name": "Nhà mẫu (giả lập)", "role": "member"}]
    assert body["user"]["email"] == email_of("member")
    assert len(body["csrf_token"]) >= 43
    token = cookie.split("smarthome_session=")[1].split(";")[0]
    (row,) = world.stack.rows(
        "SELECT token_hash FROM sessions WHERE user_id = (SELECT id FROM users WHERE email = %s)",
        email_of("member"),
    )
    assert bytes(row["token_hash"]) == hashlib.sha256(token.encode()).digest()
    assert token not in str(world.stack.rows("SELECT * FROM sessions"))


def test_session_endpoint_returns_the_same_csrf_token(world: World) -> None:
    api = world.api("member")
    resp = api.get("/api/v1/auth/session")
    assert resp.status_code == 200 and resp.json()["csrf_token"] == api.csrf
    assert world.api().get("/api/v1/auth/session").json()["detail"]["code"] == "UNAUTHENTICATED"


def test_all_login_failures_look_identical(world: World) -> None:
    """AC-07-2 (HTTP level)."""
    world.stack.conn().execute("UPDATE users SET password_hash = NULL WHERE email = %s",
                               (email_of("guest"),))  # fmt: skip
    responses = []
    for email, password in [
        ("nobody@example.com", "whatever-whatever-1"),
        (email_of("member"), "wrong-wrong-wrong-1"),
        (email_of("guest"), PASSWORDS["guest"]),
    ]:
        resp = world.api().client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"Origin": ORIGIN},
        )
        responses.append((resp.status_code, resp.json()))
    assert len({str(r) for r in responses}) == 1
    assert responses[0][0] == 401 and responses[0][1]["detail"]["code"] == "INVALID_CREDENTIALS"


def test_login_is_throttled_with_retry_after(world: World) -> None:
    """AC-07-4 (HTTP level), identical for unknown and known emails."""
    bodies = {}
    for email in ("nobody@example.com", email_of("member")):
        api = world.api()
        for _ in range(5):
            assert api.post("/api/v1/auth/login", json={"email": email, "password": "x" * 14}
                            ).status_code == 401  # fmt: skip
        resp = api.post("/api/v1/auth/login", json={"email": email, "password": "x" * 14})
        assert resp.status_code == 429
        assert 1 <= int(resp.headers["retry-after"]) <= 901
        bodies[email] = resp.json()
    assert bodies["nobody@example.com"] == bodies[email_of("member")]
    assert bodies[email_of("member")]["detail"]["code"] == "TOO_MANY_ATTEMPTS"


def test_forwarded_for_is_ignored_unless_the_peer_is_a_trusted_proxy(world: World) -> None:
    """AC-07-5."""

    def attempt(target: World) -> str | None:
        api = target.api()
        api.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "x" * 14},
            headers={"X-Forwarded-For": "198.51.100.9"},
        )
        rows = world.stack.rows("SELECT ip FROM login_attempts ORDER BY id DESC LIMIT 1")
        return rows[0]["ip"]

    assert attempt(world) == "testclient"  # header from an untrusted peer is ignored
    world.stop()
    trusted = World(world.stack, trusted_proxy_ips=("testclient",)).start()
    try:
        assert attempt(trusted) == "198.51.100.9"  # honoured only for the listed proxy
    finally:
        trusted.stop()
        world.start()


def test_a_header_identity_is_never_trusted_without_the_dev_flag(world: World) -> None:
    resp = world.api().client.get(f"{H}/devices", headers={"X-Dev-User": "user-owner"})
    assert resp.status_code == 401 and resp.json()["detail"]["code"] == "UNAUTHENTICATED"


# ---- CSRF and Origin ------------------------------------------------------------------------


def test_state_changing_requests_need_origin_and_csrf(world: World) -> None:
    """AC-07-6."""
    api = world.api("member")
    assert api.get(f"{H}/devices").status_code == 200  # reads need neither
    ok = api.post(f"{H}/commands", json=LIGHT_ON)
    assert ok.status_code == 202

    no_csrf = api.request("POST", f"{H}/commands", json=LIGHT_ON, csrf=False)
    assert (no_csrf.status_code, no_csrf.json()["detail"]["code"]) == (403, "CSRF_FAILED")
    wrong = api.request("POST", f"{H}/commands", json=LIGHT_ON, headers={"X-CSRF-Token": "x" * 43})
    assert (wrong.status_code, wrong.json()["detail"]["code"]) == (403, "CSRF_FAILED")
    foreign = api.request(
        "POST", f"{H}/commands", json=LIGHT_ON, headers={"Origin": "https://evil.example"}
    )
    assert (foreign.status_code, foreign.json()["detail"]["code"]) == (403, "ORIGIN_FORBIDDEN")
    missing = api.request("POST", f"{H}/commands", json=LIGHT_ON, origin=False)
    assert (missing.status_code, missing.json()["detail"]["code"]) == (403, "ORIGIN_FORBIDDEN")
    # Another user's CSRF token does not work for this session.
    other = world.api("guest")
    stolen = api.request(
        "POST", f"{H}/commands", json=LIGHT_ON, headers={"X-CSRF-Token": other.csrf}
    )
    assert stolen.status_code == 403


def test_login_itself_needs_a_good_origin(world: World) -> None:
    api = world.api()
    body = {"email": email_of("member"), "password": PASSWORDS["member"]}
    for headers in ({"Origin": "https://evil.example"}, {}):
        resp = api.client.post("/api/v1/auth/login", json=body, headers=headers)
        assert (resp.status_code, resp.json()["detail"]["code"]) == (403, "ORIGIN_FORBIDDEN")
    sessions = "SELECT id FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = %s)"
    assert world.stack.rows(sessions, email_of("member")) == []


def test_logout_revokes_the_session_and_is_idempotent(world: World) -> None:
    api = world.api("member")
    token = api.client.cookies.get("smarthome_session")
    assert api.post("/api/v1/auth/logout").status_code == 204
    # replay the old cookie: revoked
    api.client.cookies.set("smarthome_session", token, domain="home.example")
    assert api.get(f"{H}/devices").status_code == 401
    anonymous = world.api().post("/api/v1/auth/logout", origin=False)
    assert anonymous.status_code == 204  # no session: still 204


def test_logout_with_a_session_still_needs_csrf(world: World) -> None:
    api = world.api("member")
    assert api.request("POST", "/api/v1/auth/logout", csrf=False).status_code == 403
    assert api.get(f"{H}/devices").status_code == 200  # the session survived the bad attempt


def test_expired_idle_and_forged_sessions_are_refused(world: World) -> None:
    """AC-07-7 (HTTP level; clock arithmetic is covered in test_auth_service)."""
    api = world.api("member")
    assert api.get(f"{H}/devices").status_code == 200
    with world.stack.conn() as conn:
        conn.execute("UPDATE sessions SET last_seen_at = now() - interval '8 days'")
    assert api.get(f"{H}/devices").status_code == 401
    api = world.api("member")
    with world.stack.conn() as conn:
        conn.execute(
            "UPDATE sessions SET created_at = now() - interval '40 days',"
            " expires_at = now() - interval '10 days' WHERE revoked_at IS NULL"
        )
    assert api.get(f"{H}/devices").status_code == 401
    forged = world.api()
    forged.client.cookies.set("smarthome_session", "A" * 43, domain="home.example")
    assert forged.get(f"{H}/devices").status_code == 401


def test_change_password_over_http(world: World) -> None:
    """AC-07-8 (HTTP level)."""
    keep, other = world.api("member"), world.api("member")
    body = {"current_password": PASSWORDS["member"], "new_password": "fresh-member-passphrase"}
    wrong = keep.post(
        "/api/v1/auth/password", json={**body, "current_password": "nope-nope-nope-1"}
    )
    assert (wrong.status_code, wrong.json()["detail"]["code"]) == (401, "INVALID_CREDENTIALS")
    weak = keep.post("/api/v1/auth/password", json={**body, "new_password": "password1234"})
    assert (weak.status_code, weak.json()["detail"]["code"]) == (422, "WEAK_PASSWORD")
    assert keep.post("/api/v1/auth/password", json=body).status_code == 204
    assert keep.get(f"{H}/devices").status_code == 200
    assert other.get(f"{H}/devices").status_code == 401
    world.api().login("member", "fresh-member-passphrase")
    assert keep.request("POST", "/api/v1/auth/password", csrf=False, json=body).status_code == 403


def test_agent_endpoint_requires_a_session_and_csrf(world: World) -> None:
    anonymous = world.api().post("/api/v1/agent/invoke", json={"message": "xin chào"})
    assert anonymous.status_code == 401
    api = world.api("guest")
    assert api.post("/api/v1/agent/invoke", json={"message": "xin chào"}).status_code == 200
    assert api.request("POST", "/api/v1/agent/invoke", csrf=False,
                       json={"message": "xin chào"}).status_code == 403  # fmt: skip


# ---- default-deny and isolation -------------------------------------------------------------


def _concrete(path: str) -> str:
    out = path
    for name in ("home_id", "device_id", "command_id", "user_id"):
        out = out.replace("{" + name + "}", HOME_ID if name == "home_id" else "x")
    return out


def test_every_route_is_denied_without_a_session(world: World) -> None:
    """AC-07-9: walk the real route table, so a route added later is covered too."""
    api = world.api()
    checked = 0
    # FastAPI wraps included routers lazily, so the schema is the reliable list of routes.
    for path, operations in world.app.openapi()["paths"].items():
        for method in sorted(m.upper() for m in operations):
            if (method, path) in PUBLIC_ROUTES:
                continue
            resp = api.client.request(method, _concrete(path), json={})
            assert resp.status_code == 401, (method, path, resp.status_code)
            assert resp.json()["detail"]["code"] == "UNAUTHENTICATED"
            checked += 1
    assert checked >= 15  # homes, devices, commands, audit, members, agent, simulator, auth
    assert world.api().client.get("/health").status_code == 200


def test_public_routes_are_exactly_health_login_and_logout() -> None:
    assert {
        ("GET", "/health"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
    } == PUBLIC_ROUTES


# method, path, json, expected status for guest / member / owner (None = not checked here)
ROUTES: list[tuple[str, str, dict | None, tuple[int | None, int | None, int | None]]] = [
    ("GET", f"{H}", None, (200, 200, 200)),
    ("GET", f"{H}/devices", None, (200, 200, 200)),
    ("GET", f"{H}/devices/dev-living-light", None, (200, 200, 200)),
    ("POST", f"{H}/commands", LIGHT_ON, (403, 202, 202)),
    ("GET", f"{H}/audit", None, (403, 403, 200)),
    ("GET", f"{H}/members", None, (403, 403, 200)),
    ("POST", f"{H}/members", {"email": "n@example.com", "display_name": "N", "role": "guest",
                              "password": "newcomer-passphrase-1"}, (403, 403, None)),
    ("PATCH", f"{H}/members/some-user", {"role": "guest"}, (403, 403, None)),
    ("DELETE", f"{H}/members/some-user", None, (403, 403, None)),
    ("PUT", f"{H}/simulator/devices/dev-living-light/behavior", {"behavior": "normal"},
     (403, 403, 204)),
]  # fmt: skip


@pytest.mark.parametrize(("method", "path", "body", "expected"), ROUTES)
def test_role_matrix_and_isolation_for_every_home_route(
    world: World,
    method: str,
    path: str,
    body: dict | None,
    expected: tuple[int | None, int | None, int | None],
) -> None:
    """AC-07-10: anonymous 401, outsiders 404, each role as in the LLD matrix."""
    kwargs = {"json": body} if body is not None else {}
    assert world.api().request(method, path, **kwargs).status_code == 401
    # A member of home B (and an owner of home A pointed at home B) are outsiders: 404.
    other = world.api("other")
    assert other.request(method, path, **kwargs).status_code == 404
    other_path = path.replace(HOME_ID, SECOND_HOME)
    assert world.api("owner").request(method, other_path, **kwargs).status_code == 404
    for role, want in zip(("guest", "member", "owner"), expected, strict=True):
        if want is None:
            continue
        resp = world.api(role).request(method, path, **kwargs)
        assert resp.status_code == want, (role, method, path, resp.status_code)
        if resp.status_code == 202:  # let the command finish so the next role is not "busy"
            settle(world, resp.json()["id"])


def test_confirmation_route_roles(world: World) -> None:
    """The remaining matrix row: only an owner confirms; each role gets its own pending command."""
    for role, want in (("guest", 403), ("member", 403), ("owner", 200)):
        pending = world.api("member").post(f"{H}/commands", json=HEATER_ON).json()
        assert pending["status"] == "awaiting_confirmation"
        resp = world.api(role).post(
            f"{H}/commands/{pending['id']}/confirmation", json={"approve": False}
        )
        assert resp.status_code == want, (role, resp.text)
        outsider = world.api("other").post(
            f"{H}/commands/{pending['id']}/confirmation", json={"approve": False}
        )
        assert outsider.status_code == 404


def test_commands_of_one_home_are_invisible_from_another(world: World) -> None:
    sent = world.api("member").post(f"{H}/commands", json=LIGHT_ON).json()
    assert world.api("other").get(f"{H}/commands/{sent['id']}").status_code == 404
    # The same command id asked through home B's own URL is not found either.
    assert (
        world.api("other").get(f"/api/v1/homes/{SECOND_HOME}/commands/{sent['id']}").status_code
        == 404
    )
    assert world.api("owner").get(f"{H}/commands/{sent['id']}").status_code == 200


# ---- members --------------------------------------------------------------------------------


def _member(world: World, role: str) -> str:
    rows = world.stack.rows("SELECT id FROM users WHERE email = %s", email_of(role))
    return rows[0]["id"]


def test_owner_manages_members(world: World) -> None:
    """AC-07-13."""
    owner = world.api("owner")
    listing = owner.get(f"{H}/members").json()
    assert {m["email"] for m in listing} >= {email_of(r) for r in ("owner", "member", "guest")}
    body = {"email": "new@example.com", "display_name": "New", "role": "member",
            "password": "newcomer-passphrase-1"}  # fmt: skip
    created = owner.post(f"{H}/members", json=body)
    assert created.status_code == 201 and created.json()["role"] == "member"
    dup = owner.post(f"{H}/members", json={**body, "email": "NEW@example.com"})
    assert (dup.status_code, dup.json()["detail"]["code"]) == (409, "EMAIL_IN_USE")
    assert owner.post(f"{H}/members", json={**body, "email": "x@y.co", "role": "root"}
                      ).status_code == 422  # fmt: skip
    assert owner.post(f"{H}/members", json={**body, "email": "not-an-email"}).status_code == 422
    weak = owner.post(f"{H}/members", json={**body, "email": "w@example.com", "password": "short"})
    assert (weak.status_code, weak.json()["detail"]["code"]) == (422, "WEAK_PASSWORD")
    assert world.api("member").post(f"{H}/members", json={**body, "email": "z@example.com"}
                                    ).status_code == 403  # fmt: skip
    # The new account can log in, and after removal its live session stops working at once.
    newcomer = world.api().login("new", "newcomer-passphrase-1") if False else None
    assert newcomer is None
    fresh = world.api()
    resp = fresh.client.post("/api/v1/auth/login",
                             json={"email": "new@example.com", "password": "newcomer-passphrase-1"},
                             headers={"Origin": ORIGIN})  # fmt: skip
    assert resp.status_code == 200
    fresh.csrf = resp.json()["csrf_token"]
    assert fresh.get(f"{H}").status_code == 200
    changed = owner.request(
        "PATCH", f"{H}/members/{created.json()['user_id']}", json={"role": "guest"}
    )
    assert changed.status_code == 200 and changed.json()["role"] == "guest"
    assert fresh.post(f"{H}/commands", json=LIGHT_ON).status_code == 403  # demoted: now a guest
    removed = owner.request("DELETE", f"{H}/members/{created.json()['user_id']}")
    assert removed.status_code == 204
    assert fresh.get(f"{H}").status_code == 404  # still logged in, no longer in the home
    assert owner.request("DELETE", f"{H}/members/{created.json()['user_id']}").status_code == 404
    assert owner.request("PATCH", f"{H}/members/ghost", json={"role": "guest"}).status_code == 404


def test_the_last_owner_cannot_leave_or_be_demoted(world: World) -> None:
    """AC-07-12 (HTTP level): only the real accounts count; remove the others first."""
    owner = world.api("owner")
    for user in ("owner2", "user-owner"):
        user_id = user if user.startswith("user-") else _member(world, user)
        assert owner.request("DELETE", f"{H}/members/{user_id}").status_code == 204
    me = _member(world, "owner")
    demote = owner.request("PATCH", f"{H}/members/{me}", json={"role": "member"})
    assert (demote.status_code, demote.json()["detail"]["code"]) == (409, "LAST_OWNER")
    leave = owner.request("DELETE", f"{H}/members/{me}")
    assert (leave.status_code, leave.json()["detail"]["code"]) == (409, "LAST_OWNER")
    still = [m for m in owner.get(f"{H}/members").json() if m["role"] == "owner"]
    assert len(still) == 1


def test_two_owners_demoting_each_other_at_once_leave_one_owner(world: World) -> None:
    """AC-07-12: two connections race; the home lock lets exactly one change succeed."""
    from smarthome.domain.capabilities import Role

    # Reduce home A to exactly the two owners that race.
    owner = world.api("owner")
    for user_id in (_member(world, "member"), _member(world, "guest"), "user-owner",
                    "user-member", "user-guest"):  # fmt: skip
        owner.request("DELETE", f"{H}/members/{user_id}")
    a, b = _member(world, "owner"), _member(world, "owner2")

    async def race() -> list[object]:
        s1 = await PostgresAuthStore.open(world.stack.dsn)
        s2 = await PostgresAuthStore.open(world.stack.dsn)
        now = world.stack.clock()
        try:
            return list(await asyncio.gather(
                s1.change_role(HOME_ID, b, Role.MEMBER, a, now),
                s2.change_role(HOME_ID, a, Role.MEMBER, b, now),
                return_exceptions=True,
            ))  # fmt: skip
        finally:
            await s1.close()
            await s2.close()

    results = run(race())
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 1 and type(failures[0]).__name__ == "LastOwnerError", results
    owners = world.stack.rows(
        "SELECT user_id FROM memberships WHERE home_id = %s AND role = 'owner'", HOME_ID
    )
    assert len(owners) == 1


# ---- dynamic RBAC while a command waits (DR-H08) ---------------------------------------------


def _heater_state(world: World) -> object:
    resp = world.api("owner").get(f"{H}/devices/dev-water-heater").json()
    return next(c for c in resp["capabilities"] if c["kind"] == "on_off")["value"]


def test_demoted_owner_cannot_approve_a_pending_command(world: World) -> None:
    """AC-07-11: the approver's role is re-read when the click arrives."""
    pending = world.api("member").post(f"{H}/commands", json=HEATER_ON).json()
    assert pending["status"] == "awaiting_confirmation"
    second = world.api("owner2")  # authenticated while still an owner
    demote = world.api("owner").request(
        "PATCH", f"{H}/members/{_member(world, 'owner2')}", json={"role": "member"}
    )
    assert demote.status_code == 200
    resp = second.post(f"{H}/commands/{pending['id']}/confirmation", json={"approve": True})
    assert resp.status_code == 403
    assert _heater_state(world) is False  # nothing reached the device
    assert world.api("owner").get(f"{H}/commands/{pending['id']}").json()["status"] == (
        "awaiting_confirmation"
    )


def test_removed_owner_cannot_approve_and_removed_requester_is_rejected(world: World) -> None:
    pending = world.api("member").post(f"{H}/commands", json=HEATER_ON).json()
    second = world.api("owner2")
    world.api("owner").request("DELETE", f"{H}/members/{_member(world, 'owner2')}")
    assert second.post(f"{H}/commands/{pending['id']}/confirmation",
                       json={"approve": True}).status_code == 404  # fmt: skip

    world.api("owner").request("DELETE", f"{H}/members/{_member(world, 'member')}")
    resp = world.api("owner").post(
        f"{H}/commands/{pending['id']}/confirmation", json={"approve": True}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert resp.json()["reasons"][0]["code"] == "REQUESTER_REMOVED"
    assert _heater_state(world) is False


def test_requester_demoted_to_guest_is_rejected_on_approval(world: World) -> None:
    pending = world.api("member").post(f"{H}/commands", json=HEATER_ON).json()
    world.api("owner").request("PATCH", f"{H}/members/{_member(world, 'member')}",
                               json={"role": "guest"})  # fmt: skip
    resp = world.api("owner").post(
        f"{H}/commands/{pending['id']}/confirmation", json={"approve": True}
    )
    assert resp.json()["status"] == "rejected" and _heater_state(world) is False


def test_disabled_user_loses_access_immediately(world: World) -> None:
    api = world.api("member")
    assert api.get(f"{H}").status_code == 200
    run(world.stack.new_store().disable_user(email_of("member"), world.stack.clock()))
    assert api.get(f"{H}").status_code == 401


# ---- security events ------------------------------------------------------------------------


def test_every_security_event_is_recorded_without_secrets(world: World) -> None:
    """AC-07-16."""
    owner = world.api("owner")
    owner.post(f"{H}/members", json={"email": "ev@example.com", "display_name": "Ev",
               "role": "guest", "password": "event-test-passphrase-1"})  # fmt: skip
    ev = _member(world, "member")
    owner.request("PATCH", f"{H}/members/{ev}", json={"role": "guest"})
    owner.request("DELETE", f"{H}/members/{ev}")
    api = world.api("guest")
    api.post("/api/v1/auth/password", json={"current_password": "wrong-wrong-wrong-1",
                                            "new_password": "x" * 14})  # fmt: skip
    api.post("/api/v1/auth/logout")
    for _ in range(6):
        world.api().post("/api/v1/auth/login", json={"email": "ghost@example.com",
                                                     "password": "g" * 14})  # fmt: skip
    seen = {r["action"] for r in world.stack.rows("SELECT action FROM auth_events")}
    assert seen >= {
        "owner_created", "login_succeeded", "login_failed", "login_throttled", "logout",
        "member_added", "member_role_changed", "member_removed",
    }  # fmt: skip
    audited = {r["action"] for r in world.stack.rows("SELECT action FROM audit_events")}
    assert {"member.added", "member.role_changed", "member.removed"} <= audited
    haystack = (
        str(world.stack.rows("SELECT * FROM auth_events"))
        + str(world.stack.rows("SELECT * FROM audit_events"))
        + str(world.stack.rows("SELECT * FROM login_attempts"))
    )
    for secret in [*PASSWORDS.values(), "event-test-passphrase-1", owner.csrf,
                   owner.client.cookies.get("smarthome_session"), "ghost@example.com"]:  # fmt: skip
        assert secret and secret not in haystack, secret


def test_changing_a_password_is_logged_without_the_password(world: World) -> None:
    api = world.api("member")
    new = "another-fresh-member-passphrase"
    api.post("/api/v1/auth/password", json={"current_password": PASSWORDS["member"],
                                            "new_password": new})  # fmt: skip
    (event,) = world.auth_events("password_changed")
    assert new not in str(event) and PASSWORDS["member"] not in str(event)


# ---- environment gate over HTTP --------------------------------------------------------------


def test_strict_environment_serves_no_docs_and_no_simulator_route(stack: Stack) -> None:
    """AC-07-14 (HTTP level)."""
    strict = Settings(
        _env_file=None,
        environment="staging",
        database_url=stack.dsn,
        allowed_origins=(ORIGIN,),
        sim_latency_s=0.02,
    )
    stack.create_user()
    with TestClient(create_app(strict), base_url=ORIGIN) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        api = Api(client.app, client.portal)
        api.login("owner")
        assert (
            api.request(
                "PUT",
                f"{H}/simulator/devices/dev-living-light/behavior",
                json={"behavior": "normal"},
            ).status_code
            == 404
        )
        assert api.get(f"{H}/devices").status_code == 200  # the normal API still works
        dev = client.get(f"{H}/devices", headers={"X-Dev-User": "user-owner"})
        assert dev.status_code == 401  # dev identity does not exist here


def test_local_environment_keeps_docs_for_developers(stack: Stack) -> None:
    with TestClient(create_app(settings_for(stack.dsn)), base_url=ORIGIN) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def settle(world: World, command_id: str) -> None:
    owner = world.api("owner")
    assert _wait_for(
        lambda: (
            owner.get(f"{H}/commands/{command_id}").json()["status"]
            not in {"requested", "dispatching", "dispatched"}
        ),
        seconds=5,
    )


def _wait_for(check: Callable[[], bool], seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def test_member_command_is_verified_end_to_end_with_a_real_session(world: World) -> None:
    """The whole path with real auth: login, CSRF, command, simulator report, verification."""
    api = world.api("member")
    sent = api.post(f"{H}/commands", json=LIGHT_ON).json()
    seen: list[str] = []

    def finished() -> bool:
        seen.append(api.get(f"{H}/commands/{sent['id']}").json()["status"])
        return seen[-1] == "succeeded"

    assert _wait_for(finished, seconds=5), seen
    done = api.get(f"{H}/commands/{sent['id']}").json()
    assert done["verification"] in {"correlated", "state_match"}
    assert world.api("owner").get(f"{H}/audit").status_code == 200
    # make_stack is imported for symmetry with other modules; keep the name referenced
    assert callable(make_stack)
