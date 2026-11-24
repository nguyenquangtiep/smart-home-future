"""R3: the number of Argon2id operations running at once is bounded (each costs ~64 MiB).

The per-email/IP limit (F2) does not bound the total: many different emails from many addresses
each pass their own limit. These tests start many such logins at once, each on its own
PostgreSQL connection, and record how many hashes actually run at the same time by wrapping the
hashing function (the database, the rate limiter and the login flow are all real).
"""

import asyncio
import threading
import time
from collections.abc import Iterator

import pytest
from auth_helpers import OWNER_EMAIL, OWNER_PASSWORD, Stack, run

from smarthome.auth import passwords
from smarthome.auth.service import AuthService, InvalidCredentialsError
from smarthome.auth.store import PostgresAuthStore


class Peak:
    """Counts concurrent executions of the wrapped hashing function."""

    def __init__(self, hold_s: float = 0.0) -> None:
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.calls = 0
        self.hold_s = hold_s

    def wrap(self, fn):  # noqa: ANN001, ANN201
        def wrapped(*args: object) -> object:
            with self.lock:
                self.running += 1
                self.calls += 1
                self.peak = max(self.peak, self.running)
            try:
                if self.hold_s:
                    time.sleep(self.hold_s)  # make overlap certain on fast machines
                return fn(*args)
            finally:
                with self.lock:
                    self.running -= 1

        return wrapped


@pytest.fixture
def peak(monkeypatch: pytest.MonkeyPatch) -> Iterator[Peak]:
    counter = Peak(hold_s=0.05)
    monkeypatch.setattr(passwords, "_verify", counter.wrap(passwords._verify))
    yield counter


@pytest.fixture
def limits() -> Iterator[None]:
    """Restore the process-wide hasher limits after a test changes them."""
    saved = passwords.limits()
    yield
    passwords.configure(**saved)


async def _burst(stack: Stack, n: int) -> list[BaseException | object]:
    stores = [await PostgresAuthStore.open(stack.dsn) for _ in range(n)]
    services = [AuthService(s, stack.settings, stack.clock) for s in stores]
    go = asyncio.Event()

    async def one(i: int) -> object:
        await go.wait()
        return await services[i].login(f"user{i}@example.com", "wrong-guess-123", f"10.0.{i}.1")

    tasks = [asyncio.create_task(one(i)) for i in range(n)]
    await asyncio.sleep(0.05)
    go.set()
    try:
        return list(await asyncio.gather(*tasks, return_exceptions=True))
    finally:
        for s in stores:
            await s.close()


def test_concurrent_hashes_never_exceed_the_configured_limit(
    stack: Stack, peak: Peak, limits: None
) -> None:
    """24 logins for 24 different emails from 24 addresses: each passes its rate limit, but at
    most two hashes ever run at once, and every login still gets its normal answer."""
    passwords.configure(max_concurrent=2, max_pending=64, wait_s=30.0)
    results = run(_burst(stack, 24))
    assert all(isinstance(r, InvalidCredentialsError) for r in results), results
    assert peak.calls == 24
    assert peak.peak <= 2, f"{peak.peak} Argon2id operations ran at once (limit 2)"


def test_overflow_is_rejected_fast_without_counting_as_a_failure(
    stack: Stack, peak: Peak, limits: None
) -> None:
    """With one slot and a queue of three, a burst of twelve gets some 'busy' answers. A busy
    answer is the same for every email, does not burn the user's rate-limit budget, and is
    recorded as a security event."""
    passwords.configure(max_concurrent=1, max_pending=3, wait_s=30.0)
    results = run(_burst(stack, 12))
    busy = [r for r in results if isinstance(r, passwords.HasherBusyError)]
    checked = [r for r in results if isinstance(r, InvalidCredentialsError)]
    assert len(busy) + len(checked) == 12, results
    assert busy and len(checked) <= 12 - len(busy)
    assert peak.peak <= 1
    failures = stack.rows("SELECT count(*) AS n FROM login_attempts WHERE NOT succeeded")[0]["n"]
    assert failures == len(checked)  # rejected-as-busy attempts were released
    events = stack.rows("SELECT count(*) AS n FROM auth_events WHERE action = 'login_busy'")[0]
    assert events["n"] == len(busy)
    assert len({str(b) for b in busy}) == 1  # identical whatever the email


def test_waiting_longer_than_the_timeout_is_rejected(stack: Stack, limits: None) -> None:
    """A slot that stays taken longer than wait_s makes the next caller give up."""
    passwords.configure(max_concurrent=1, max_pending=8, wait_s=0.2)
    hashed = run(passwords.hash_password(OWNER_PASSWORD))
    release = threading.Event()

    def slow(stored: str, password: str) -> bool:
        release.wait(2)
        return False

    async def body() -> list[object]:
        real = passwords._verify
        passwords._verify = slow  # type: ignore[assignment]
        try:
            first = asyncio.create_task(passwords.verify_password(hashed, "x"))
            await asyncio.sleep(0.05)
            second = await asyncio.gather(
                passwords.verify_password(hashed, "y"), return_exceptions=True
            )
            release.set()
            return [await first, *second]
        finally:
            passwords._verify = real  # type: ignore[assignment]

    first, second = run(body())
    assert first is False
    assert isinstance(second, passwords.HasherBusyError)


def test_normal_login_is_unaffected_by_the_limit(stack: Stack, limits: None) -> None:
    passwords.configure(max_concurrent=1, max_pending=2, wait_s=5.0)
    stack.create_user()
    service = stack.new_service()
    assert run(service.login(OWNER_EMAIL, OWNER_PASSWORD, "203.0.113.5")).token
    assert passwords.limits()["max_concurrent"] == 1


def test_busy_hasher_answers_503_and_does_not_count_as_a_failure(
    stack: Stack, monkeypatch: pytest.MonkeyPatch, limits: None
) -> None:
    """HTTP contract: 503 AUTH_BUSY with Retry-After, same body for every email."""
    from fastapi.testclient import TestClient

    from smarthome.api.main import create_app

    async def busy(stored: str, password: str) -> bool:
        raise passwords.HasherBusyError

    stack.create_user()
    with TestClient(create_app(stack.settings), base_url="https://home.example") as client:
        monkeypatch.setattr(passwords, "verify_password", busy)
        bodies = []
        for email in (OWNER_EMAIL, "nobody@example.com"):
            resp = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "whatever-123456"},
                headers={"Origin": "https://home.example"},
            )
            assert resp.status_code == 503 and resp.headers["retry-after"] == "1"
            bodies.append(resp.json())
    assert bodies[0] == bodies[1] and bodies[0]["detail"]["code"] == "AUTH_BUSY"
    assert stack.rows("SELECT count(*) AS n FROM login_attempts")[0]["n"] == 0
    assert (
        stack.rows("SELECT count(*) AS n FROM auth_events WHERE action = 'login_busy'")[0]["n"] == 2
    )
