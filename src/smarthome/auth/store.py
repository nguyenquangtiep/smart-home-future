"""PostgreSQL storage for users, memberships, sessions, login attempts and security events.

Every public method is one transaction. Membership changes also write the home's own
``audit_events`` row in the same transaction, so a change and its audit record cannot diverge.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from smarthome.domain.capabilities import Role
from smarthome.domain.models import Member
from smarthome.persistence.pgconn import Conn, PgConnection

# last_seen_at is written at most this often (LLD D-07-15).
LAST_SEEN_GRANULARITY = timedelta(minutes=5)
LOGIN_ATTEMPT_RETENTION = timedelta(hours=24)


class AuthStoreError(Exception):
    code = "AUTH_ERROR"


class EmailInUseError(AuthStoreError):
    code = "EMAIL_IN_USE"


class UserNotFoundError(AuthStoreError):
    code = "USER_NOT_FOUND"


class HomeNotFoundError(AuthStoreError):
    code = "HOME_NOT_FOUND"


class MemberNotFoundError(AuthStoreError):
    code = "NOT_FOUND"


class LastOwnerError(AuthStoreError):
    code = "LAST_OWNER"


@dataclass(frozen=True)
class UserRow:
    id: str
    email: str
    display_name: str
    password_hash: str | None
    disabled_at: datetime | None


@dataclass(frozen=True)
class SessionRow:
    id: str
    user_id: str
    email: str
    display_name: str
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    user_disabled_at: datetime | None


@dataclass(frozen=True)
class MemberRow:
    user_id: str
    email: str
    display_name: str
    role: Role
    created_at: datetime


@dataclass(frozen=True)
class HomeRole:
    home_id: str
    name: str
    role: Role


def _event(
    conn: Conn,
    now: datetime,
    action: str,
    user_id: str | None = None,
    home_id: str | None = None,
    **detail: object,
) -> None:
    conn.execute(
        "INSERT INTO auth_events (at, user_id, home_id, action, detail)"
        " VALUES (%s, %s, %s, %s, %s)",
        (now, user_id, home_id, action, Jsonb(detail)),
    )


def _user(row: dict[str, Any]) -> UserRow:
    return UserRow(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        password_hash=row["password_hash"],
        disabled_at=row["disabled_at"],
    )


class PostgresAuthStore:
    def __init__(self, pg: PgConnection) -> None:
        self._pg = pg

    @classmethod
    async def open(cls, dsn: str) -> "PostgresAuthStore":
        return cls(await PgConnection.open(dsn, min_version=3))

    async def close(self) -> None:
        await self._pg.close()

    def membership_source(self) -> "PostgresMembershipSource":
        return PostgresMembershipSource(self._pg)

    # ---- users ---------------------------------------------------------------------------

    async def find_user_by_email(self, email: str) -> UserRow | None:
        def tx(conn: Conn) -> UserRow | None:
            row = conn.execute(
                "SELECT id, email, display_name, password_hash, disabled_at FROM users"
                " WHERE lower(email) = lower(%s)",
                (email.strip(),),
            ).fetchone()
            return _user(row) if row else None

        return await self._pg.run(tx)

    async def get_user(self, user_id: str) -> UserRow | None:
        def tx(conn: Conn) -> UserRow | None:
            row = conn.execute(
                "SELECT id, email, display_name, password_hash, disabled_at FROM users"
                " WHERE id = %s",
                (user_id,),
            ).fetchone()
            return _user(row) if row else None

        return await self._pg.run(tx)

    async def create_owner(
        self,
        home_id: str,
        email: str,
        display_name: str,
        password_hash: str,
        now: datetime,
    ) -> str:
        """CLI: one transaction creating the user and its owner membership."""

        def tx(conn: Conn) -> str:
            if not conn.execute("SELECT 1 FROM homes WHERE id = %s", (home_id,)).fetchone():
                raise HomeNotFoundError(home_id)
            user_id = str(uuid.uuid4())
            try:
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO users (id, email, display_name, password_hash,"
                        " password_changed_at) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, email.strip(), display_name, password_hash, now),
                    )
            except psycopg.errors.UniqueViolation:
                raise EmailInUseError(email) from None
            conn.execute(
                "INSERT INTO memberships (home_id, user_id, role, created_by)"
                " VALUES (%s, %s, 'owner', 'cli')",
                (home_id, user_id),
            )
            _event(conn, now, "owner_created", user_id, home_id, by="cli")
            return user_id

        return await self._pg.run(tx)

    async def seed_demo_users(self, members: list[Member], now: datetime) -> int:
        """Demo members get a user row with no password (they sign in through the dev identity
        in local/test only) and a membership. Idempotent. Returns how many were created."""

        def tx(conn: Conn) -> int:
            created = 0
            for m in members:
                email = f"{m.user_id}@demo.invalid"
                made = conn.execute(
                    "INSERT INTO users (id, email, display_name) VALUES (%s, %s, %s)"
                    " ON CONFLICT DO NOTHING RETURNING id",
                    (m.user_id, email, m.display_name),
                ).fetchone()
                conn.execute(
                    "INSERT INTO memberships (home_id, user_id, role, created_by)"
                    " VALUES (%s, %s, %s, 'seed-demo') ON CONFLICT DO NOTHING",
                    (m.home_id, m.user_id, m.role.value),
                )
                created += 1 if made else 0
            if created:
                _event(conn, now, "demo_users_seeded", None, members[0].home_id, count=created)
            return created

        return await self._pg.run(tx)

    async def set_password(self, email: str, password_hash: str, now: datetime) -> str:
        """CLI: set a new password and revoke every session of the user."""

        def tx(conn: Conn) -> str:
            row = conn.execute(
                "UPDATE users SET password_hash = %s, password_changed_at = %s"
                " WHERE lower(email) = lower(%s) RETURNING id",
                (password_hash, now, email.strip()),
            ).fetchone()
            if row is None:
                raise UserNotFoundError(email)
            revoked = conn.execute(
                "UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
                (now, row["id"]),
            ).rowcount
            _event(conn, now, "password_set_by_cli", row["id"], by="cli", sessions_revoked=revoked)
            return row["id"]

        return await self._pg.run(tx)

    async def change_password(
        self, user_id: str, password_hash: str, keep_session_id: str, now: datetime
    ) -> int:
        """Set a new hash and revoke every *other* session. Returns how many were revoked."""

        def tx(conn: Conn) -> int:
            conn.execute(
                "UPDATE users SET password_hash = %s, password_changed_at = %s WHERE id = %s",
                (password_hash, now, user_id),
            )
            revoked = conn.execute(
                "UPDATE sessions SET revoked_at = %s"
                " WHERE user_id = %s AND revoked_at IS NULL AND id <> %s",
                (now, user_id, keep_session_id),
            ).rowcount
            _event(conn, now, "password_changed", user_id, sessions_revoked=revoked)
            return revoked

        return await self._pg.run(tx)

    async def disable_user(self, email: str, now: datetime) -> str:
        def tx(conn: Conn) -> str:
            row = conn.execute(
                "UPDATE users SET disabled_at = coalesce(disabled_at, %s)"
                " WHERE lower(email) = lower(%s) RETURNING id",
                (now, email.strip()),
            ).fetchone()
            if row is None:
                raise UserNotFoundError(email)
            revoked = conn.execute(
                "UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
                (now, row["id"]),
            ).rowcount
            _event(conn, now, "user_disabled", row["id"], by="cli", sessions_revoked=revoked)
            return row["id"]

        return await self._pg.run(tx)

    async def unlock(self, email: str, email_hash: bytes, now: datetime) -> int:
        """CLI: forget the failed attempts for this email so the account can log in again."""

        def tx(conn: Conn) -> int:
            removed = conn.execute(
                "DELETE FROM login_attempts WHERE email_hash = %s AND NOT succeeded",
                (email_hash,),
            ).rowcount
            user = conn.execute(
                "SELECT id FROM users WHERE lower(email) = lower(%s)", (email.strip(),)
            ).fetchone()
            _event(
                conn,
                now,
                "login_unlocked",
                user["id"] if user else None,
                by="cli",
                attempts_cleared=removed,
            )
            return removed

        return await self._pg.run(tx)

    # ---- sessions ------------------------------------------------------------------------

    async def create_session(
        self,
        user_id: str,
        token_hash: bytes,
        csrf_token: str,
        now: datetime,
        expires_at: datetime,
        revoke_session_id: str | None = None,
    ) -> str:
        def tx(conn: Conn) -> str:
            if revoke_session_id:
                conn.execute(
                    "UPDATE sessions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
                    (now, revoke_session_id),
                )
            session_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sessions (id, token_hash, user_id, csrf_token, created_at,"
                " last_seen_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (session_id, token_hash, user_id, csrf_token, now, now, expires_at),
            )
            return session_id

        return await self._pg.run(tx)

    async def get_session(self, token_hash: bytes) -> SessionRow | None:
        def tx(conn: Conn) -> SessionRow | None:
            row = conn.execute(
                "SELECT s.id, s.user_id, u.email, u.display_name, s.csrf_token, s.created_at,"
                " s.last_seen_at, s.expires_at, s.revoked_at, u.disabled_at AS user_disabled_at"
                " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = %s",
                (token_hash,),
            ).fetchone()
            return SessionRow(**row) if row else None

        return await self._pg.run(tx)

    async def touch_session(self, session_id: str, now: datetime) -> bool:
        """Update last_seen_at only when it is older than the granularity. True if written."""

        def tx(conn: Conn) -> bool:
            return (
                conn.execute(
                    "UPDATE sessions SET last_seen_at = %s WHERE id = %s AND last_seen_at < %s",
                    (now, session_id, now - LAST_SEEN_GRANULARITY),
                ).rowcount
                > 0
            )

        return await self._pg.run(tx)

    async def revoke_session(self, session_id: str, user_id: str, now: datetime) -> None:
        def tx(conn: Conn) -> None:
            conn.execute(
                "UPDATE sessions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
                (now, session_id),
            )
            _event(conn, now, "logout", user_id, session_id=session_id)

        await self._pg.run(tx)

    # ---- login throttling and events -----------------------------------------------------

    async def failure_window(
        self, email_hash: bytes, ip: str | None, since: datetime
    ) -> tuple[int, datetime | None, int, datetime | None]:
        """(failures for the email, oldest of them, failures for the IP, oldest of them)."""

        def tx(conn: Conn) -> tuple[int, datetime | None, int, datetime | None]:
            e = conn.execute(
                "SELECT count(*) AS n, min(at) AS oldest FROM login_attempts"
                " WHERE email_hash = %s AND NOT succeeded AND at > %s",
                (email_hash, since),
            ).fetchone()
            if ip is None:
                return e["n"], e["oldest"], 0, None
            i = conn.execute(
                "SELECT count(*) AS n, min(at) AS oldest FROM login_attempts"
                " WHERE ip = %s AND NOT succeeded AND at > %s",
                (ip, since),
            ).fetchone()
            return e["n"], e["oldest"], i["n"], i["oldest"]

        return await self._pg.run(tx)

    async def record_attempt(
        self,
        email_hash: bytes,
        ip: str | None,
        succeeded: bool,
        now: datetime,
        user_id: str | None,
        action: str,
        reason: str | None = None,
    ) -> None:
        """Record the attempt and its security event together (and prune old attempts)."""

        def tx(conn: Conn) -> None:
            conn.execute(
                "INSERT INTO login_attempts (at, email_hash, ip, succeeded)"
                " VALUES (%s, %s, %s, %s)",
                (now, email_hash, ip, succeeded),
            )
            conn.execute(
                "DELETE FROM login_attempts WHERE at < %s", (now - LOGIN_ATTEMPT_RETENTION,)
            )
            _event(
                conn, now, action, user_id, None,
                email_hash=email_hash.hex(), ip=ip, **({"reason": reason} if reason else {}),
            )  # fmt: skip

        await self._pg.run(tx)

    async def record_event(
        self,
        now: datetime,
        action: str,
        user_id: str | None = None,
        home_id: str | None = None,
        **detail: object,
    ) -> None:
        await self._pg.run(lambda conn: _event(conn, now, action, user_id, home_id, **detail))

    # ---- memberships ---------------------------------------------------------------------

    async def homes_for_user(self, user_id: str) -> list[HomeRole]:
        def tx(conn: Conn) -> list[HomeRole]:
            rows = conn.execute(
                "SELECT h.id, h.name, m.role FROM memberships m JOIN homes h ON h.id = m.home_id"
                " WHERE m.user_id = %s ORDER BY h.id",
                (user_id,),
            ).fetchall()
            return [HomeRole(r["id"], r["name"], Role(r["role"])) for r in rows]

        return await self._pg.run(tx)

    async def list_members(self, home_id: str) -> list[MemberRow]:
        def tx(conn: Conn) -> list[MemberRow]:
            rows = conn.execute(
                "SELECT u.id, u.email, u.display_name, m.role, m.created_at FROM memberships m"
                " JOIN users u ON u.id = m.user_id WHERE m.home_id = %s"
                " ORDER BY m.created_at, u.id",
                (home_id,),
            ).fetchall()
            return [
                MemberRow(r["id"], r["email"], r["display_name"], Role(r["role"]), r["created_at"])
                for r in rows
            ]

        return await self._pg.run(tx)

    async def add_member(
        self,
        home_id: str,
        email: str,
        display_name: str,
        role: Role,
        password_hash: str,
        actor_id: str,
        now: datetime,
    ) -> MemberRow:
        def tx(conn: Conn) -> MemberRow:
            _lock_home(conn, home_id)
            user_id = str(uuid.uuid4())
            try:
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO users (id, email, display_name, password_hash,"
                        " password_changed_at) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, email.strip(), display_name, password_hash, now),
                    )
            except psycopg.errors.UniqueViolation:
                raise EmailInUseError(email) from None
            conn.execute(
                "INSERT INTO memberships (home_id, user_id, role, created_by, created_at)"
                " VALUES (%s, %s, %s, %s, %s)",
                (home_id, user_id, role.value, actor_id, now),
            )
            _record_change(conn, now, "member_added", home_id, actor_id, user_id, new_role=role)
            return MemberRow(user_id, email.strip(), display_name, role, now)

        return await self._pg.run(tx)

    async def change_role(
        self, home_id: str, user_id: str, role: Role, actor_id: str, now: datetime
    ) -> MemberRow:
        def tx(conn: Conn) -> MemberRow:
            _lock_home(conn, home_id)
            old = _current_role(conn, home_id, user_id)
            if old is Role.OWNER and role is not Role.OWNER:
                _require_another_owner(conn, home_id)
            conn.execute(
                "UPDATE memberships SET role = %s WHERE home_id = %s AND user_id = %s",
                (role.value, home_id, user_id),
            )
            _record_change(
                conn, now, "member_role_changed", home_id, actor_id, user_id,
                old_role=old, new_role=role,
            )  # fmt: skip
            row = conn.execute(
                "SELECT u.id, u.email, u.display_name, m.role, m.created_at FROM memberships m"
                " JOIN users u ON u.id = m.user_id WHERE m.home_id = %s AND m.user_id = %s",
                (home_id, user_id),
            ).fetchone()
            assert row is not None
            return MemberRow(
                row["id"], row["email"], row["display_name"], Role(row["role"]), row["created_at"]
            )

        return await self._pg.run(tx)

    async def remove_member(self, home_id: str, user_id: str, actor_id: str, now: datetime) -> None:
        def tx(conn: Conn) -> None:
            _lock_home(conn, home_id)
            old = _current_role(conn, home_id, user_id)
            if old is Role.OWNER:
                _require_another_owner(conn, home_id)
            conn.execute(
                "DELETE FROM memberships WHERE home_id = %s AND user_id = %s", (home_id, user_id)
            )
            _record_change(conn, now, "member_removed", home_id, actor_id, user_id, old_role=old)

        await self._pg.run(tx)


def _lock_home(conn: Conn, home_id: str) -> None:
    """Serialise membership changes of one home (LLD D-07-12)."""
    if not conn.execute("SELECT 1 FROM homes WHERE id = %s FOR UPDATE", (home_id,)).fetchone():
        raise HomeNotFoundError(home_id)


def _current_role(conn: Conn, home_id: str, user_id: str) -> Role:
    row = conn.execute(
        "SELECT role FROM memberships WHERE home_id = %s AND user_id = %s", (home_id, user_id)
    ).fetchone()
    if row is None:
        raise MemberNotFoundError(user_id)
    return Role(row["role"])


def _require_another_owner(conn: Conn, home_id: str) -> None:
    owners = conn.execute(
        "SELECT count(*) AS n FROM memberships WHERE home_id = %s AND role = 'owner'", (home_id,)
    ).fetchone()
    if owners["n"] <= 1:
        raise LastOwnerError(home_id)


def _record_change(
    conn: Conn,
    now: datetime,
    action: str,
    home_id: str,
    actor_id: str,
    target_user_id: str,
    **roles: Role,
) -> None:
    detail = {k: v.value for k, v in roles.items()}
    _event(conn, now, action, actor_id, home_id, target_user_id=target_user_id, **detail)
    conn.execute(
        "INSERT INTO audit_events (at, home_id, actor_id, origin, action, detail)"
        " VALUES (%s, %s, %s, 'ui', %s, %s)",
        (
            now,
            home_id,
            actor_id,
            action.replace("member_", "member.", 1),
            Jsonb({"target_user_id": target_user_id, **detail}),
        ),
    )


class PostgresMembershipSource:
    """``MembershipSource`` backed by the memberships table. Looked up on every call; a
    disabled user has no membership as far as the control path is concerned."""

    def __init__(self, pg: PgConnection) -> None:
        self._pg = pg

    async def get(self, home_id: str, user_id: str) -> Member | None:
        def tx(conn: Conn) -> Member | None:
            row = conn.execute(
                "SELECT m.role, u.display_name FROM memberships m JOIN users u ON u.id = m.user_id"
                " WHERE m.home_id = %s AND m.user_id = %s AND u.disabled_at IS NULL",
                (home_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return Member(
                user_id=user_id,
                home_id=home_id,
                display_name=row["display_name"],
                role=Role(row["role"]),
            )

        return await self._pg.run(tx)
