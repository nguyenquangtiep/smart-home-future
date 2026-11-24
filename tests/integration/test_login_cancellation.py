"""R4 / T5, T6: cancelling a login task at each stage keeps the PostgreSQL reservation safe.

Rule: once an attempt slot is reserved it counts as a failure unless the password was verified
correct and recorded. Cancelling never deletes it (cancelling must not become a free guess), no
session is handed out, and a `login_cancelled` security event names the stage.

These tests cancel the asyncio task directly. Whether an HTTP client disconnect cancels the
request handler depends on the ASGI server and middleware; that is not claimed here.
"""

import asyncio
import threading
import time
from collections.abc import Callable, Iterator

import pytest
from auth_helpers import OWNER_EMAIL, OWNER_PASSWORD, Stack, run

from smarthome.auth import passwords
from smarthome.auth.service import AuthService, TooManyAttemptsError, email_digest

IP = "203.0.113.9"


class Blocker:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Semaphore(0)
        self.ran = 0

    def __call__(self, stored: str, password: str) -> bool:
        self.ran += 1
        self.started.release()
        self.release.wait(10)
        return False


@pytest.fixture
def hasher() -> Iterator[None]:
    saved = passwords.limits()
    passwords.configure(max_concurrent=1, max_pending=8, wait_s=10.0)
    yield
    passwords.configure(**saved)  # type: ignore[arg-type]


@pytest.fixture
def blocker(monkeypatch: pytest.MonkeyPatch, hasher: None) -> Iterator[Blocker]:
    b = Blocker()
    monkeypatch.setattr(passwords, "_verify", b)
    yield b
    b.release.set()
    _wait(lambda: passwords.stats()["pending"] == 0)


def _wait(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.005)


def _attempts(stack: Stack) -> list[bool]:
    rows = stack.rows(
        "SELECT succeeded FROM login_attempts WHERE email_hash = %s ORDER BY id",
        email_digest(OWNER_EMAIL),
    )
    return [r["succeeded"] for r in rows]


def _cancel_events(stack: Stack) -> list[str]:
    rows = stack.rows(
        "SELECT detail FROM auth_events WHERE action = 'login_cancelled' ORDER BY seq"
    )
    return [r["detail"]["reason"] for r in rows]


def _sessions(stack: Stack) -> int:
    return stack.rows("SELECT count(*) AS n FROM sessions")[0]["n"]


async def _cancel_when(task: "asyncio.Task[object]", ready: Callable[[], bool]) -> None:
    await asyncio.to_thread(_wait, ready)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_cancel_while_waiting_for_the_hasher(stack: Stack, blocker: Blocker) -> None:
    stack.create_user()
    service = stack.new_service()

    async def body() -> None:
        holder = asyncio.create_task(passwords.verify_password("x", "y"))  # takes the slot
        await asyncio.to_thread(blocker.started.acquire)
        login = asyncio.create_task(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
        await _cancel_when(login, lambda: passwords.stats()["waiting"] >= 1)
        blocker.release.set()
        await holder

    run(body())
    _wait(lambda: passwords.stats()["pending"] == 0)
    assert blocker.ran == 1  # the cancelled login never hashed
    assert _attempts(stack) == [False]  # still counted
    assert _cancel_events(stack) == ["verifying"]
    assert _sessions(stack) == 0


def test_cancel_while_hashing(stack: Stack, blocker: Blocker) -> None:
    stack.create_user()
    service = stack.new_service()

    async def body() -> None:
        login = asyncio.create_task(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
        await _cancel_when(login, lambda: blocker.ran == 1)
        assert passwords.stats()["pending"] == 1  # the hash is still accounted for
        blocker.release.set()

    run(body())
    _wait(lambda: passwords.stats()["pending"] == 0)
    assert _attempts(stack) == [False]
    assert _cancel_events(stack) == ["verifying"]
    assert _sessions(stack) == 0


def _gate_store(
    service: AuthService, method: str, when: Callable[[tuple], bool]
) -> "tuple[asyncio.Event, list[int]]":
    """Make one store method hang (until cancelled) when `when(args)` is true."""
    real = getattr(service.store, method)
    entered = asyncio.Event()
    calls: list[int] = []

    async def gated(*args: object) -> object:
        if when(args):
            calls.append(1)
            entered.set()
            await asyncio.Event().wait()  # hangs until the task is cancelled
        return await real(*args)

    setattr(service.store, method, gated)
    return entered, calls


def test_cancel_after_a_correct_password_but_before_it_is_recorded(
    stack: Stack, hasher: None
) -> None:
    """The password was checked: the attempt is NOT deleted (it stays a failure) and no session
    exists."""
    stack.create_user()
    service = stack.new_service()

    async def body() -> None:
        entered, _ = _gate_store(service, "finish_attempt", lambda a: a[1] is True)
        login = asyncio.create_task(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
        await entered.wait()
        login.cancel()
        with pytest.raises(asyncio.CancelledError):
            await login

    run(body())
    assert _attempts(stack) == [False]
    assert _cancel_events(stack) == ["recording_result"]
    assert _sessions(stack) == 0


def test_cancel_while_creating_the_session(stack: Stack, hasher: None) -> None:
    stack.create_user()
    service = stack.new_service()

    async def body() -> None:
        entered, _ = _gate_store(service, "create_session", lambda a: True)
        login = asyncio.create_task(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
        await entered.wait()
        login.cancel()
        with pytest.raises(asyncio.CancelledError):
            await login

    run(body())
    assert _attempts(stack) == [True]  # the success was recorded before the cancellation
    assert _cancel_events(stack) == ["creating_session"]
    assert _sessions(stack) == 0  # no token was ever produced


def test_cancel_while_reserving(stack: Stack, hasher: None) -> None:
    """Cancelled before the reservation was made: nothing is counted, the event is kept."""
    stack.create_user()
    service = stack.new_service()

    async def body() -> None:
        entered, _ = _gate_store(service, "reserve_attempt", lambda a: True)
        login = asyncio.create_task(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
        await entered.wait()
        login.cancel()
        with pytest.raises(asyncio.CancelledError):
            await login

    run(body())
    assert _attempts(stack) == []
    assert _cancel_events(stack) == ["reserving"]
    assert _sessions(stack) == 0


def test_cancelling_is_not_a_way_around_the_rate_limit(stack: Stack, blocker: Blocker) -> None:
    """Five logins cancelled mid-way still use up the five-failure budget: the sixth attempt,
    even with the right password, is throttled."""
    stack.create_user()
    service = stack.new_service()

    async def body() -> None:
        for i in range(5):
            login = asyncio.create_task(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
            if i == 0:  # the first one hashes (and blocks the only slot)
                await _cancel_when(login, lambda: blocker.ran == 1)
            else:  # the others wait; abandoned waiters keep waiting until the slot frees
                await _cancel_when(login, lambda n=i: passwords.stats()["waiting"] >= n)
        with pytest.raises(TooManyAttemptsError):
            await service.login(OWNER_EMAIL, OWNER_PASSWORD, IP)

    run(body())
    assert _attempts(stack) == [False] * 5
    assert _cancel_events(stack) == ["verifying"] * 5
    assert _sessions(stack) == 0


def test_capacity_and_login_recover_after_cancellations(
    stack: Stack, monkeypatch: pytest.MonkeyPatch, hasher: None
) -> None:
    """T6: after cancelled and finished work, a normal login succeeds without a restart."""
    stack.create_user()
    stack.create_user("second@example.com", "second-account-passphrase")
    service = stack.new_service()
    b = Blocker()
    monkeypatch.setattr(passwords, "_verify", b)

    async def body() -> None:
        login = asyncio.create_task(service.login(OWNER_EMAIL, OWNER_PASSWORD, IP))
        await _cancel_when(login, lambda: b.ran == 1)
        b.release.set()
        await asyncio.to_thread(_wait, lambda: passwords.stats()["pending"] == 0)
        monkeypatch.undo()  # the real Argon2id again
        result = await service.login("second@example.com", "second-account-passphrase", IP)
        assert result.token

    run(body())
    assert passwords.stats() == {"pending": 0, "waiting": 0, "running": 0}
    assert _sessions(stack) == 1
