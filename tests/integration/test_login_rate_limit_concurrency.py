"""F2: the login limit must hold when many attempts arrive at once, on separate PostgreSQL
connections (ADR 0005 D5, LLD P0-07 D-07-7, AC-07-4).

Each attempt below runs on its own AuthService with its own database connection, all started
together, so nothing in one Python object serialises them: only the database can. The bug these
tests reproduce: "read the failure count, verify (about 90 ms), then record" let every concurrent
request read the same low count and all of them got a password guess.
"""

import asyncio

import pytest
from auth_helpers import OWNER_EMAIL, OWNER_PASSWORD, Stack, run

from smarthome.auth.service import AuthService, InvalidCredentialsError, TooManyAttemptsError
from smarthome.auth.store import PostgresAuthStore

ATTEMPTS = 20
EMAIL_LIMIT = 5  # SMARTHOME_LOGIN_MAX_FAILURES_EMAIL default


async def _burst(
    stack: Stack, calls: list[tuple[str, str, str | None]]
) -> list[BaseException | object]:
    """Run each (email, password, ip) on its own service and connection, all at once."""
    stores = [await PostgresAuthStore.open(stack.dsn) for _ in calls]
    services = [AuthService(store, stack.settings, stack.clock) for store in stores]
    start = asyncio.Event()

    async def one(service: AuthService, email: str, password: str, ip: str | None) -> object:
        await start.wait()
        return await service.login(email, password, ip)

    tasks = [
        asyncio.create_task(one(s, e, p, ip)) for s, (e, p, ip) in zip(services, calls, strict=True)
    ]
    await asyncio.sleep(0.05)  # every task is parked on the event
    start.set()
    try:
        return list(await asyncio.gather(*tasks, return_exceptions=True))
    finally:
        for store in stores:
            await store.close()


def _count(results: list[BaseException | object]) -> tuple[int, int, list[BaseException]]:
    guesses = sum(isinstance(r, InvalidCredentialsError) for r in results)
    throttled = sum(isinstance(r, TooManyAttemptsError) for r in results)
    other = [r for r in results if isinstance(r, BaseException) and not isinstance(
        r, InvalidCredentialsError | TooManyAttemptsError)]  # fmt: skip
    return guesses, throttled, other


@pytest.mark.parametrize("email", [OWNER_EMAIL, "nobody@example.com"])
def test_concurrent_wrong_passwords_get_at_most_the_limit_of_guesses(
    stack: Stack, email: str
) -> None:
    """Twenty simultaneous wrong passwords for one email (existing or not): exactly five are
    checked, fifteen are throttled, and exactly five failures are stored."""
    stack.create_user()
    calls = [(email, f"wrong-guess-{i:03d}-xyz", f"198.51.100.{i}") for i in range(ATTEMPTS)]
    guesses, throttled, other = _count(run(_burst(stack, calls)))
    assert not other, other
    assert guesses == EMAIL_LIMIT, f"{guesses} password guesses were checked (limit {EMAIL_LIMIT})"
    assert throttled == ATTEMPTS - EMAIL_LIMIT
    stored = stack.rows("SELECT count(*) AS n FROM login_attempts WHERE NOT succeeded")[0]["n"]
    assert stored == EMAIL_LIMIT


def test_concurrent_wrong_passwords_from_one_ip_respect_the_ip_limit(stack: Stack) -> None:
    """Forty simultaneous attempts from one address, each for a different email: 30 checked."""
    calls = [(f"user{i}@example.com", "wrong-guess-ip-limit", "192.0.2.77") for i in range(40)]
    guesses, throttled, other = _count(run(_burst(stack, calls)))
    assert not other, other
    assert (guesses, throttled) == (30, 10)


def test_correct_password_racing_with_wrong_ones_is_not_locked_out_wrongly(stack: Stack) -> None:
    """Four wrong guesses and the right password together stay under the limit: the owner gets
    in, and the next attempt is still allowed (the success does not count as a failure)."""
    stack.create_user()
    calls = [(OWNER_EMAIL, f"wrong-guess-{i}-xyz", "203.0.113.1") for i in range(4)]
    calls.append((OWNER_EMAIL, OWNER_PASSWORD, "203.0.113.1"))
    results = run(_burst(stack, calls))
    guesses, throttled, other = _count(results)
    assert not other and throttled == 0 and guesses == 4
    assert sum(not isinstance(r, BaseException) for r in results) == 1  # the login succeeded
    service = stack.new_service()
    assert run(service.login(OWNER_EMAIL, OWNER_PASSWORD, "203.0.113.1")).token
    rows = stack.rows("SELECT succeeded FROM login_attempts ORDER BY id")
    assert sorted(r["succeeded"] for r in rows) == [False] * 4 + [True] * 2


def test_retry_after_is_reported_to_throttled_concurrent_requests(stack: Stack) -> None:
    calls = [("nobody@example.com", "wrong-guess-retry", f"198.51.100.{i}") for i in range(8)]
    results = run(_burst(stack, calls))
    waits = [r.retry_after_s for r in results if isinstance(r, TooManyAttemptsError)]
    assert len(waits) == 3
    assert all(1 <= w <= int(stack.settings.login_window_s) + 1 for w in waits)
