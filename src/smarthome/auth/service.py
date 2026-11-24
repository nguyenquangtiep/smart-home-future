"""Login, sessions and password change (ADR 0005, LLD P0-07).

Rules enforced here:
  * unknown email, wrong password, disabled user and a user without a password all produce the
    same error, and a hash is always computed (a dummy one when needed);
  * failures are throttled per email and per IP over a sliding window, even for unknown emails;
  * the session token is random, only its SHA-256 is stored, a new login always gets a new token;
  * a session dies on idle time, absolute age, revocation or when its user is disabled.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from smarthome.auth import passwords
from smarthome.auth.store import HomeRole, PostgresAuthStore, SessionRow
from smarthome.core.config import Settings
from smarthome.domain.state import Clock, utc_now


class InvalidCredentialsError(Exception):
    """Same message for every cause; never says which part was wrong."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after_s: int) -> None:
        self.retry_after_s = retry_after_s
        super().__init__("too many failed attempts")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. ``session_id`` and ``csrf_token`` are None for the dev identity."""

    user_id: str
    email: str
    display_name: str
    session_id: str | None = None
    csrf_token: str | None = None
    dev: bool = False


@dataclass(frozen=True)
class LoginResult:
    principal: Principal
    homes: list[HomeRole]
    token: str  # the cookie value; shown once, never stored
    csrf_token: str
    expires_at: datetime


def email_digest(email: str) -> bytes:
    """What login_attempts stores instead of the address."""
    return hashlib.sha256(email.strip().lower().encode()).digest()


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def csrf_matches(expected: str, supplied: str | None) -> bool:
    return supplied is not None and hmac.compare_digest(expected.encode(), supplied.encode())


class AuthService:
    def __init__(
        self, store: PostgresAuthStore, settings: Settings, clock: Clock = utc_now
    ) -> None:
        self.store = store
        self.settings = settings
        self._clock = clock

    def now(self) -> datetime:
        return self._clock()

    # ---- login ---------------------------------------------------------------------------

    async def login(
        self,
        email: str,
        password: str,
        ip: str | None,
        current_token: str | None = None,
    ) -> LoginResult:
        now = self._clock()
        digest = email_digest(email)
        attempt_id = await self._reserve(digest, ip, now, user_id=None)

        user = await self.store.find_user_by_email(email)
        usable = user is not None and user.password_hash is not None and user.disabled_at is None
        # Always spend one hash verification, on a dummy when there is nothing to check.
        stored = user.password_hash if (usable and user and user.password_hash) else None
        ok = await self._verify(attempt_id, digest, ip, now, None, stored, password)
        if not (usable and ok and user):
            reason = (
                "unknown_user"
                if user is None
                else "disabled"
                if user.disabled_at
                else "no_password"
                if user.password_hash is None
                else "bad_password"
            )
            await self.store.finish_attempt(
                attempt_id, False, now, user.id if user else None, "login_failed", digest, ip,
                reason,
            )  # fmt: skip
            raise InvalidCredentialsError

        await self.store.finish_attempt(
            attempt_id, True, now, user.id, "login_succeeded", digest, ip
        )
        old = await self._session_for(current_token) if current_token else None
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires_at = now + timedelta(days=self.settings.session_max_days)
        session_id = await self.store.create_session(
            user.id, token_digest(token), csrf, now, expires_at, old.id if old else None
        )
        principal = Principal(user.id, user.email, user.display_name, session_id, csrf)
        homes = await self.store.homes_for_user(user.id)
        return LoginResult(principal, homes, token, csrf, expires_at)

    async def _reserve(
        self, digest: bytes, ip: str | None, now: datetime, user_id: str | None
    ) -> int:
        """Take an attempt slot atomically in PostgreSQL, or raise TooManyAttemptsError."""
        slot = await self.store.reserve_attempt(
            digest,
            ip,
            now,
            timedelta(seconds=self.settings.login_window_s),
            self.settings.login_max_failures_email,
            self.settings.login_max_failures_ip,
        )
        if slot.attempt_id is None:
            assert slot.retry_after is not None
            await self.store.record_event(
                now, "login_throttled", user_id, email_hash=digest.hex(), ip=ip
            )
            raise TooManyAttemptsError(max(1, int(slot.retry_after.total_seconds()) + 1))
        return slot.attempt_id

    async def _verify(
        self,
        attempt_id: int,
        digest: bytes,
        ip: str | None,
        now: datetime,
        user_id: str | None,
        stored: str | None,
        password: str,
    ) -> bool:
        """Verify against ``stored`` (or the dummy hash). If the hasher is saturated (R3), give
        the reserved slot back so a busy answer never counts against the user, then re-raise."""
        try:
            return await passwords.verify_password(stored or passwords.dummy_hash(), password)
        except passwords.HasherBusyError:
            await self.store.release_attempt(attempt_id, now, user_id, digest, ip)
            raise

    # ---- sessions ------------------------------------------------------------------------

    async def _session_for(self, token: str) -> SessionRow | None:
        """The session behind ``token`` if it is still usable, else None."""
        row = await self.store.get_session(token_digest(token))
        if row is None:
            return None
        now = self._clock()
        idle = timedelta(days=self.settings.session_idle_days)
        if (
            row.revoked_at is not None
            or row.user_disabled_at is not None
            or now >= row.expires_at
            or now >= row.last_seen_at + idle
        ):
            return None
        return row

    async def authenticate(self, token: str | None) -> Principal | None:
        if not token:
            return None
        row = await self._session_for(token)
        if row is None:
            return None
        await self.store.touch_session(row.id, self._clock())
        return Principal(row.user_id, row.email, row.display_name, row.id, row.csrf_token)

    async def logout(self, principal: Principal) -> None:
        if principal.session_id:
            await self.store.revoke_session(principal.session_id, principal.user_id, self._clock())

    # ---- password ------------------------------------------------------------------------

    async def change_password(
        self, principal: Principal, current_password: str, new_password: str, ip: str | None
    ) -> None:
        """Raises InvalidCredentialsError (counted), TooManyAttemptsError, WeakPasswordError."""
        now = self._clock()
        digest = email_digest(principal.email)
        attempt_id = await self._reserve(digest, ip, now, principal.user_id)
        user = await self.store.get_user(principal.user_id)
        stored = user.password_hash if user else None
        ok = await self._verify(
            attempt_id, digest, ip, now, principal.user_id, stored, current_password
        )
        if not (user and stored and ok):
            await self.store.finish_attempt(
                attempt_id, False, now, principal.user_id, "login_failed", digest, ip,
                "bad_current_password",
            )  # fmt: skip
            raise InvalidCredentialsError
        await self.store.finish_attempt(
            attempt_id, True, now, principal.user_id, "password_verified", digest, ip
        )
        checked = passwords.check_policy(new_password, principal.email)  # WeakPasswordError
        new_hash = await passwords.hash_password(checked)
        assert principal.session_id is not None
        await self.store.change_password(principal.user_id, new_hash, principal.session_id, now)
