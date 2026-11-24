"""R4: hasher capacity stays correct when the awaiting task is cancelled (T1, T2, T3, T4, T6).

The awaiting coroutine and the worker thread have separate lifetimes: cancelling the coroutine
does not stop a thread that is already waiting for a slot or hashing. Capacity must follow the
*worker*, not the coroutine. A fake hash function that blocks on a threading.Event makes every
stage deterministic: the tests wait for observable states instead of sleeping.
"""

import asyncio
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from smarthome.auth import passwords


class Blocker:
    """Stands in for the Argon2id call: blocks until released, counts what really ran."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Semaphore(0)
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.ran = 0

    def __call__(self, stored: str, password: str) -> bool:
        with self.lock:
            self.running += 1
            self.ran += 1
            self.peak = max(self.peak, self.running)
        self.started.release()
        try:
            self.release.wait(10)
            return False
        finally:
            with self.lock:
                self.running -= 1

    def wait_started(self, n: int = 1) -> None:
        for _ in range(n):
            assert self.started.acquire(timeout=5), "worker did not start"


@pytest.fixture
def blocker(monkeypatch: pytest.MonkeyPatch) -> Iterator[Blocker]:
    saved = passwords.limits()
    b = Blocker()
    monkeypatch.setattr(passwords, "_verify", b)
    yield b
    b.release.set()
    _wait(lambda: passwords._pending == 0)
    passwords.configure(**saved)  # type: ignore[arg-type]


def _wait(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll a state predicate (the state changes in other threads)."""
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.005)


async def _until_waiting(n: int) -> None:
    """Until n worker threads are blocked waiting for a slot."""
    if hasattr(passwords, "stats"):
        await asyncio.to_thread(_wait, lambda: passwords.stats()["waiting"] >= n)
    else:  # code before the R4 fix exposes no state: give the thread time to block
        await asyncio.sleep(0.3)


def _verify() -> "asyncio.Task[bool]":
    return asyncio.create_task(passwords.verify_password("stored", "pw"))


def test_t1_cancelled_waiter_keeps_its_capacity_until_its_worker_ends(blocker: Blocker) -> None:
    """One slot, room for two. A runs, B waits for the slot, B's caller is cancelled. B's worker
    thread still exists, so a third caller must be refused; once everything ends, all capacity
    is back and B never hashed."""
    passwords.configure(max_concurrent=1, max_pending=2, wait_s=10.0)

    async def body() -> None:
        a = _verify()
        await asyncio.to_thread(blocker.wait_started)
        b = _verify()
        await _until_waiting(1)
        b.cancel()
        with pytest.raises(asyncio.CancelledError):
            await b
        assert passwords._pending == 2, "capacity released while B's worker still waits"
        with pytest.raises(passwords.HasherBusyError):
            await passwords.verify_password("stored", "pw")  # C: must not get in
        blocker.release.set()
        assert await a is False
        await asyncio.to_thread(_wait, lambda: passwords._pending == 0)

    asyncio.run(body())
    assert blocker.ran == 1  # B's worker gave up its slot without hashing
    assert blocker.peak == 1


def test_t2_cancelled_while_hashing_counts_until_the_hash_finishes(blocker: Blocker) -> None:
    passwords.configure(max_concurrent=1, max_pending=1, wait_s=10.0)

    async def body() -> None:
        a = _verify()
        await asyncio.to_thread(blocker.wait_started)
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        assert blocker.running == 1
        assert passwords._pending == 1, "capacity released while the hash is still running"
        with pytest.raises(passwords.HasherBusyError):
            await passwords.verify_password("stored", "pw")
        blocker.release.set()
        await asyncio.to_thread(_wait, lambda: passwords._pending == 0)

    asyncio.run(body())


def test_t3_repeated_cancellation_never_breaks_the_bounds(blocker: Blocker) -> None:
    """Thirty rounds of six callers, cancelled before their worker starts, while waiting for a
    slot, or while hashing. A sampler thread watches the counters the whole time: never negative,
    never above max_pending, never more than max_concurrent hashing; nothing leaks."""
    passwords.configure(max_concurrent=2, max_pending=4, wait_s=10.0)
    samples: list[dict[str, int]] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            samples.append(passwords.stats())
            time.sleep(0.001)

    watcher = threading.Thread(target=sampler)
    watcher.start()

    async def body() -> None:
        for round_ in range(30):
            tasks = [_verify() for _ in range(6)]  # 2 of 6 are refused: queue holds 4
            await asyncio.sleep(0)
            stage = round_ % 3
            if stage == 1:  # some are hashing
                await asyncio.to_thread(_wait, lambda: passwords.stats()["running"] >= 1)
            elif stage == 2:  # some wait for a slot behind the two hashing
                await asyncio.to_thread(_wait, lambda: passwords.stats()["waiting"] >= 1)
            for t in tasks[::2]:
                t.cancel()
            blocker.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.to_thread(_wait, lambda: passwords.stats()["pending"] == 0)
            blocker.release.clear()

    try:
        asyncio.run(body())
    finally:
        stop.set()
        watcher.join()
    assert samples
    assert min(x["pending"] for x in samples) >= 0
    assert max(x["pending"] for x in samples) <= 4
    assert max(x["running"] for x in samples) <= 2
    assert blocker.peak <= 2
    assert passwords.stats() == {"pending": 0, "waiting": 0, "running": 0}


def test_t4_limits_and_timeout_still_hold(blocker: Blocker) -> None:
    """Full queue -> busy at once; a waiter past wait_s -> busy; capacity comes back after."""
    passwords.configure(max_concurrent=1, max_pending=2, wait_s=0.3)

    async def body() -> None:
        a = _verify()
        await asyncio.to_thread(blocker.wait_started)
        waiter = _verify()
        with pytest.raises(passwords.HasherBusyError):  # queue full (A + waiter)
            await passwords.verify_password("stored", "pw")
        with pytest.raises(passwords.HasherBusyError):  # waiter times out after 0.3 s
            await waiter
        assert passwords._pending == 1  # only A remains
        blocker.release.set()
        await a
        await asyncio.to_thread(_wait, lambda: passwords._pending == 0)

    asyncio.run(body())
    assert blocker.ran == 1


def test_t6_capacity_recovers_without_restart(blocker: Blocker) -> None:
    """After cancellations and timeouts, the next normal callers are served."""
    passwords.configure(max_concurrent=1, max_pending=2, wait_s=10.0)

    async def body() -> None:
        a = _verify()
        await asyncio.to_thread(blocker.wait_started)
        b = _verify()
        await _until_waiting(1)
        a.cancel()
        b.cancel()
        await asyncio.gather(a, b, return_exceptions=True)
        blocker.release.set()
        await asyncio.to_thread(_wait, lambda: passwords._pending == 0)
        results = await asyncio.gather(
            *(passwords.verify_password("stored", "pw") for _ in range(2))
        )
        assert results == [False, False]

    asyncio.run(body())
    assert passwords._pending == 0
