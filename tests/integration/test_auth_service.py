"""S1-06: auth storage, login, throttling, sessions, password change, CLI (AC-07-2/3/4/7/8/15/16/
17/19). Real PostgreSQL; time is a fake clock."""

import asyncio
import hashlib
import time
from datetime import timedelta
from io import StringIO

import psycopg
import pytest
from auth_helpers import OWNER_EMAIL, OWNER_PASSWORD, Stack, run

from smarthome.auth import __main__ as auth_cli
from smarthome.auth import passwords
from smarthome.auth.service import (
    InvalidCredentialsError,
    TooManyAttemptsError,
    email_digest,
    token_digest,
)
from smarthome.auth.store import (
    EmailInUseError,
    HomeNotFoundError,
    LastOwnerError,
    MemberNotFoundError,
)
from smarthome.core.config import get_settings
from smarthome.domain.capabilities import Role
from smarthome.persistence import migrate
from smarthome.persistence.postgres import connect

IP = "203.0.113.7"


# ---- migration 0003 -------------------------------------------------------------------------


def test_migration_0003_keeps_older_data_on_downgrade(empty_pg_dsn: str) -> None:
    """AC-07-19."""
    with connect(empty_pg_dsn) as conn:
        migrate.upgrade(conn, target=2)
        conn.execute("INSERT INTO homes (id, name) VALUES ('h1', 'H')")
        conn.execute("INSERT INTO audit_events (at, home_id, action) VALUES (now(), 'h1', 'a')")
        assert migrate.upgrade(conn) == [3]
        conn.execute("INSERT INTO users (id, email, display_name) VALUES ('u', 'a@b.c', 'A')")
        assert migrate.downgrade(conn, 2) == [3]
        assert conn.execute("SELECT to_regclass('users') IS NULL AS gone").fetchone()["gone"]
        assert conn.execute("SELECT count(*) AS n FROM homes").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM audit_events").fetchone()["n"] == 1
        assert migrate.upgrade(conn) == [3]


def test_auth_events_are_append_only(stack: Stack) -> None:
    """AC-07-16 (storage part)."""
    stack.create_user()
    with connect(stack.dsn) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE auth_events SET action = 'x'")
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM auth_events")
        with conn.transaction():  # the explicit purge switch still works
            conn.execute("SET LOCAL smarthome.allow_audit_purge = 'on'")
            assert conn.execute("DELETE FROM auth_events").rowcount >= 1


def test_email_is_unique_ignoring_case(stack: Stack) -> None:
    stack.create_user("Mixed.Case@Example.com")
    with pytest.raises(EmailInUseError):
        stack.create_user("mixed.case@example.com")
    assert len(stack.rows("SELECT id FROM users")) == 1


# ---- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def cli_env(stack: Stack, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setenv("SMARTHOME_DATABASE_URL", stack.dsn)
    monkeypatch.setenv("SMARTHOME_ENVIRONMENT", "test")
    get_settings.cache_clear()
    yield stack
    get_settings.cache_clear()


def cli(*argv: str, stdin: str = "", prompts: list[str] | None = None) -> int:
    answers = iter(prompts or [])
    return auth_cli.main(list(argv), StringIO(stdin), lambda _prompt: next(answers))


def test_cli_rejects_password_on_the_command_line(
    cli_env: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-07-15."""
    with pytest.raises(SystemExit) as excinfo:
        cli("create-owner", "--home", "home-demo", "--email", "a@b.co", "--name", "A",
            "--password", "secret-secret-1")  # fmt: skip
    assert excinfo.value.code == 2
    assert "never accepted as arguments" in capsys.readouterr().err
    assert cli_env.rows("SELECT id FROM users") == []


def test_cli_create_owner_for_missing_home_creates_nothing(
    cli_env: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli("create-owner", "--home", "nope", "--email", "a@b.co", "--name", "A",
               "--password-stdin", stdin="a-long-unusual-passphrase\n")  # fmt: skip
    assert code == 2
    assert "does not exist" in capsys.readouterr().err
    assert cli_env.rows("SELECT id FROM users") == []
    assert cli_env.rows("SELECT user_id FROM memberships") == []


def test_cli_create_owner_makes_user_and_membership_together(
    cli_env: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "a-long-unusual-passphrase"
    code = cli("create-owner", "--home", "home-demo", "--email", "boss@example.com",
               "--name", "Boss", "--password-stdin", stdin=secret + "\n")  # fmt: skip
    assert code == 0
    out = capsys.readouterr()
    assert secret not in out.out + out.err
    (member,) = cli_env.rows("SELECT role, home_id FROM memberships")
    assert (member["role"], member["home_id"]) == ("owner", "home-demo")
    (user,) = cli_env.rows("SELECT password_hash FROM users")
    assert user["password_hash"].startswith("$argon2id$")
    assert secret not in str(cli_env.rows("SELECT * FROM auth_events"))
    assert cli("create-owner", "--home", "home-demo", "--email", "BOSS@example.com",
               "--name", "Dup", "--password-stdin", stdin=secret + "\n") == 2  # fmt: skip
    assert len(cli_env.rows("SELECT id FROM users")) == 1


def test_cli_prompt_mismatch_and_weak_passwords_are_refused(
    cli_env: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ("create-owner", "--home", "home-demo", "--email", "a@b.co", "--name", "A")
    assert cli(*args, prompts=["a-long-unusual-passphrase", "different-passphrase-1"]) == 2
    assert "differ" in capsys.readouterr().err
    assert cli(*args, "--password-stdin", stdin="password1234\n") == 2
    assert "phổ biến" in capsys.readouterr().err
    assert cli_env.rows("SELECT id FROM users") == []
    assert cli(*args, prompts=["a-long-unusual-passphrase"] * 2) == 0


def test_cli_set_password_revokes_all_sessions_and_unlock_clears_throttle(
    cli_env: Stack, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-07-15."""
    cli_env.create_user()
    service = cli_env.new_service()
    run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    assert len(cli_env.rows("SELECT id FROM sessions WHERE revoked_at IS NULL")) == 2
    new_password = "brand-new-unusual-phrase"
    assert cli("set-password", "--email", OWNER_EMAIL, "--password-stdin",
               stdin=new_password + "\n") == 0  # fmt: skip
    assert cli_env.rows("SELECT id FROM sessions WHERE revoked_at IS NULL") == []
    assert new_password not in capsys.readouterr().out
    run(service.login(OWNER_EMAIL, new_password, IP))
    with pytest.raises(InvalidCredentialsError):
        run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))

    for _ in range(4):
        with pytest.raises(InvalidCredentialsError):
            run(service.login(OWNER_EMAIL, "wrong-wrong-wrong", IP))
    with pytest.raises(TooManyAttemptsError):
        run(service.login(OWNER_EMAIL, new_password, IP))
    assert cli("unlock", "--email", OWNER_EMAIL) == 0
    run(service.login(OWNER_EMAIL, new_password, IP))
    assert cli("set-password", "--email", "ghost@example.com", "--password-stdin",
               stdin=new_password + "\n") == 2  # fmt: skip


def test_cli_disable_user_revokes_sessions_and_blocks_login(cli_env: Stack) -> None:
    cli_env.create_user()
    service = cli_env.new_service()
    result = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    assert cli("disable-user", "--email", OWNER_EMAIL) == 0
    assert run(service.authenticate(result.token)) is None
    with pytest.raises(InvalidCredentialsError):
        run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))


# ---- login ----------------------------------------------------------------------------------


def test_login_stores_only_the_hash_of_the_token(stack: Stack) -> None:
    """AC-07-1 (storage part) and AC-07-3."""
    stack.create_user()
    service = stack.new_service()
    result = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    (row,) = stack.rows("SELECT * FROM sessions")
    assert bytes(row["token_hash"]) == hashlib.sha256(result.token.encode()).digest()
    assert result.token not in str(row) and result.token != result.csrf_token
    assert len(result.token) >= 43  # 256 bits, url-safe base64
    assert row["csrf_token"] == result.csrf_token
    (user,) = stack.rows("SELECT password_hash FROM users")
    assert user["password_hash"].startswith("$argon2id$")
    everything = (
        str(stack.rows("SELECT * FROM auth_events"))
        + str(stack.rows("SELECT * FROM audit_events"))
        + str(stack.rows("SELECT * FROM login_attempts"))
    )
    assert OWNER_PASSWORD not in everything and result.token not in everything


def test_each_login_gets_a_new_token_and_revokes_the_one_presented(stack: Stack) -> None:
    stack.create_user()
    service = stack.new_service()
    first = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    second = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP, current_token=first.token))
    assert first.token != second.token
    assert run(service.authenticate(first.token)) is None
    assert run(service.authenticate(second.token)) is not None


def test_every_failure_looks_the_same_and_always_hashes(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-07-2."""
    stack.create_user()
    stack.create_user("disabled@example.com", home_id="home-demo", role="member")
    stack.create_user("nopass@example.com", home_id="home-demo", role="guest")
    with stack.conn() as conn:
        conn.execute("UPDATE users SET disabled_at = now() WHERE email = 'disabled@example.com'")
        conn.execute("UPDATE users SET password_hash = NULL WHERE email = 'nopass@example.com'")
    calls: list[str] = []
    real = passwords.verify_password

    async def counting(stored: str, password: str) -> bool:
        calls.append(stored[:10])
        return await real(stored, password)

    monkeypatch.setattr(passwords, "verify_password", counting)
    service = stack.new_service()
    attempts = [
        ("ghost@example.com", "whatever-whatever-1"),
        (OWNER_EMAIL, "wrong-wrong-wrong-1"),
        ("disabled@example.com", OWNER_PASSWORD),
        ("nopass@example.com", OWNER_PASSWORD),
    ]
    errors = []
    for email, password in attempts:
        with pytest.raises(InvalidCredentialsError) as excinfo:
            run(service.login(email, password, IP))
        errors.append((type(excinfo.value), str(excinfo.value), excinfo.value.args))
    assert len(set(errors)) == 1
    assert len(calls) == len(attempts)  # a verification happened for every attempt
    reasons = {r["detail"]["reason"] for r in stack.rows(
        "SELECT detail FROM auth_events WHERE action = 'login_failed'")}  # fmt: skip
    assert reasons == {"unknown_user", "bad_password", "disabled", "no_password"}


# ---- throttling -----------------------------------------------------------------------------


def fail(service, email: str, ip: str = IP) -> None:  # noqa: ANN001
    with pytest.raises(InvalidCredentialsError):
        run(service.login(email, "definitely-wrong-1", ip))


def test_sixth_failure_in_the_window_is_throttled_even_for_unknown_emails(stack: Stack) -> None:
    """AC-07-4."""
    stack.create_user()
    service = stack.new_service()
    for email in (OWNER_EMAIL, "ghost@example.com"):
        for _ in range(5):
            fail(service, email, ip="198.51.100.1" if email == OWNER_EMAIL else "198.51.100.2")
        with pytest.raises(TooManyAttemptsError) as excinfo:
            run(service.login(email, OWNER_PASSWORD, "198.51.100.3"))
        assert 1 <= excinfo.value.retry_after_s <= 901
    before = stack.rows("SELECT count(*) AS n FROM login_attempts")[0]["n"]
    with pytest.raises(TooManyAttemptsError):
        run(service.login(OWNER_EMAIL, OWNER_PASSWORD, "198.51.100.3"))
    assert stack.rows("SELECT count(*) AS n FROM login_attempts")[0]["n"] == before  # not recorded
    assert (
        stack.rows("SELECT count(*) AS n FROM auth_events WHERE action = 'login_throttled'")[0]["n"]
        >= 3
    )


def test_throttle_survives_restart_and_expires_with_the_sliding_window(stack: Stack) -> None:
    stack.create_user()
    first = stack.new_service()
    for _ in range(5):
        fail(first, OWNER_EMAIL)
    restarted = stack.new_service()  # a new process: new store, same database
    with pytest.raises(TooManyAttemptsError):
        run(restarted.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    stack.clock.advance(minutes=14, seconds=59)
    with pytest.raises(TooManyAttemptsError):
        run(restarted.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    stack.clock.advance(seconds=2)
    assert run(restarted.login(OWNER_EMAIL, OWNER_PASSWORD, IP)).token


def test_failures_slide_out_one_by_one(stack: Stack) -> None:
    stack.create_user()
    service = stack.new_service()
    for _ in range(3):
        fail(service, OWNER_EMAIL)
        stack.clock.advance(minutes=6)  # failures at t=0, 6, 12 min
    for _ in range(2):
        fail(service, OWNER_EMAIL)  # t=18: first one (t=0) left the window, so 4 in window
    fail(service, OWNER_EMAIL)  # 5th in window -> still allowed to try, then blocked
    with pytest.raises(TooManyAttemptsError):
        run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))


def test_per_ip_limit_applies_across_different_emails(stack: Stack) -> None:
    service = stack.new_service()
    for i in range(30):
        fail(service, f"user{i}@example.com", ip="192.0.2.50")
    with pytest.raises(TooManyAttemptsError):
        run(service.login("another@example.com", "x" * 14, "192.0.2.50"))
    # a different address is unaffected
    with pytest.raises(InvalidCredentialsError):
        run(service.login("another@example.com", "x" * 14, "192.0.2.51"))


# ---- sessions -------------------------------------------------------------------------------


def test_session_dies_on_idle_time_but_activity_keeps_it_alive(stack: Stack) -> None:
    """AC-07-7."""
    stack.create_user()
    service = stack.new_service()
    token = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP)).token
    for _ in range(3):  # three 6-day gaps: alive thanks to activity, 18 days in total
        stack.clock.advance(days=6)
        assert run(service.authenticate(token)) is not None
    stack.clock.advance(days=7, seconds=1)
    assert run(service.authenticate(token)) is None


def test_session_dies_at_the_absolute_limit_despite_activity(stack: Stack) -> None:
    stack.create_user()
    service = stack.new_service()
    token = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP)).token
    for _ in range(4):  # day 6, 12, 18, 24: kept alive by activity
        stack.clock.advance(days=6)
        assert run(service.authenticate(token)) is not None
    stack.clock.advance(days=6)  # day 30 exactly: the absolute limit
    assert run(service.authenticate(token)) is None


def test_revoked_session_and_disabled_user_are_refused_immediately(stack: Stack) -> None:
    stack.create_user()
    service = stack.new_service()
    one = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    two = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    run(service.logout(one.principal))
    assert run(service.authenticate(one.token)) is None
    assert run(service.authenticate(two.token)) is not None
    run(service.store.disable_user(OWNER_EMAIL, stack.clock()))
    assert run(service.authenticate(two.token)) is None


def test_unknown_or_empty_token_is_refused(stack: Stack) -> None:
    service = stack.new_service()
    assert run(service.authenticate(None)) is None
    assert run(service.authenticate("")) is None
    assert run(service.authenticate("x" * 43)) is None


def test_last_seen_is_written_at_most_once_per_five_minutes(stack: Stack) -> None:
    stack.create_user()
    service = stack.new_service()
    token = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP)).token

    def last_seen():  # noqa: ANN202
        return stack.rows("SELECT last_seen_at FROM sessions")[0]["last_seen_at"]

    started = last_seen()
    stack.clock.advance(minutes=4)
    run(service.authenticate(token))
    assert last_seen() == started
    stack.clock.advance(minutes=2)  # 6 minutes since the last write
    run(service.authenticate(token))
    assert last_seen() == started + timedelta(minutes=6)


# ---- change password ------------------------------------------------------------------------


def test_change_password_rules(stack: Stack) -> None:
    """AC-07-8."""
    stack.create_user()
    service = stack.new_service()
    keep = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    other = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    new = "a-completely-new-passphrase"

    with pytest.raises(InvalidCredentialsError):
        run(service.change_password(keep.principal, "wrong-current-pw-1", new, IP))
    failures = stack.rows("SELECT count(*) AS n FROM login_attempts WHERE NOT succeeded")[0]["n"]
    assert failures == 1  # a wrong current password counts towards the limit
    for weak in ("short", "x" * 129, "password1234", OWNER_EMAIL):
        with pytest.raises(passwords.WeakPasswordError):
            run(service.change_password(keep.principal, OWNER_PASSWORD, weak, IP))
    assert run(service.authenticate(other.token)) is not None  # nothing changed yet

    run(service.change_password(keep.principal, OWNER_PASSWORD, new, IP))
    assert run(service.authenticate(keep.token)) is not None  # current session survives
    assert run(service.authenticate(other.token)) is None  # every other session is gone
    run(service.login(OWNER_EMAIL, new, IP))
    with pytest.raises(InvalidCredentialsError):
        run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    assert new not in str(stack.rows("SELECT * FROM auth_events"))


def test_wrong_current_password_is_throttled(stack: Stack) -> None:
    stack.create_user()
    service = stack.new_service()
    session = run(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            run(service.change_password(session.principal, "wrong-current-pw-1", "x" * 14, IP))
    with pytest.raises(TooManyAttemptsError):
        run(service.change_password(session.principal, OWNER_PASSWORD, "y" * 14, IP))


# ---- event loop -----------------------------------------------------------------------------


def test_password_verification_does_not_block_the_event_loop(stack: Stack) -> None:
    """AC-07-17: while five verifications run, a 10 ms ticker is never late by 50 ms."""
    hashed = run(passwords.hash_password(OWNER_PASSWORD))

    async def body() -> float:
        worst = 0.0
        done = asyncio.Event()

        async def ticker() -> None:
            nonlocal worst
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                worst = max(worst, now - last - 0.01)
                last = now

        tick = asyncio.create_task(ticker())
        await asyncio.gather(*(passwords.verify_password(hashed, OWNER_PASSWORD) for _ in range(5)))
        done.set()
        await tick
        return worst

    assert run(body()) < 0.05


# ---- membership storage ---------------------------------------------------------------------


def test_membership_source_reads_the_database_every_time(stack: Stack) -> None:
    user_id = stack.create_user()
    store = stack.new_store()
    source = store.membership_source()
    member = run(source.get("home-demo", user_id))
    assert member is not None and member.role is Role.OWNER
    stack.create_user("second@example.com")  # second owner so the first can be demoted
    run(store.change_role("home-demo", user_id, Role.GUEST, "someone", stack.clock()))
    assert run(source.get("home-demo", user_id)).role is Role.GUEST  # type: ignore[union-attr]
    run(store.remove_member("home-demo", user_id, "someone", stack.clock()))
    assert run(source.get("home-demo", user_id)) is None
    assert run(source.get("other-home", user_id)) is None


def test_membership_source_hides_disabled_users(stack: Stack) -> None:
    user_id = stack.create_user()
    store = stack.new_store()
    run(store.disable_user(OWNER_EMAIL, stack.clock()))
    assert run(store.membership_source().get("home-demo", user_id)) is None


def test_store_rejects_changes_to_unknown_things(stack: Stack) -> None:
    store = stack.new_store()
    with pytest.raises(HomeNotFoundError):
        run(store.add_member("nope", "a@b.co", "A", Role.GUEST, "h", "x", stack.clock()))
    with pytest.raises(MemberNotFoundError):
        run(store.change_role("home-demo", "ghost", Role.GUEST, "x", stack.clock()))
    stack.create_user()
    (owner,) = stack.rows("SELECT user_id FROM memberships")
    with pytest.raises(LastOwnerError):
        run(store.remove_member("home-demo", owner["user_id"], "x", stack.clock()))
    assert email_digest(" Owner@Example.COM ") == email_digest(OWNER_EMAIL)
    assert token_digest("abc") == hashlib.sha256(b"abc").digest()
