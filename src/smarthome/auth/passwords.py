"""Password policy and Argon2id hashing (ADR 0005, LLD D-07-5).

Hashing and verification are CPU and memory heavy (about 100 ms, 64 MiB), so they always run in
a worker thread: with one worker process (ADR 0001) a blocked event loop would also block device
control. At most ``max_concurrent`` of them run at once (each needs ~64 MiB): F2's per-email and
per-IP limits do not bound the total when many different emails arrive together (R3). A caller
that cannot get a slot within ``wait_s``, or arrives when ``max_pending`` callers are already
in flight or waiting, gets HasherBusyError at once instead of joining an unbounded queue. The
limit is per process, which is the whole deployment (one worker, one instance: ADR 0001).
The common-password list is generated from base words and the usual suffixes; it is a
guard against the obvious choices, not a breach corpus.
"""

import asyncio
import threading
import unicodedata
from collections.abc import Callable
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 128

_HASHER = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


class HasherBusyError(RuntimeError):
    """Too many password hashes are running or waiting; try again shortly. Says nothing about
    the account, so it is safe to return for any email."""

    def __init__(self) -> None:
        super().__init__("password hashing is busy")


_limits = {"max_concurrent": 2, "max_pending": 16, "wait_s": 5.0}
_slots = threading.BoundedSemaphore(2)
_gate = threading.Lock()
_pending = 0


def configure(max_concurrent: int, max_pending: int, wait_s: float) -> None:
    """Set the process-wide limits (called once at startup from Settings)."""
    global _slots
    if max_concurrent < 1 or max_pending < max_concurrent or wait_s <= 0:
        raise ValueError("need max_concurrent >= 1, max_pending >= max_concurrent, wait_s > 0")
    with _gate:
        _limits.update(max_concurrent=max_concurrent, max_pending=max_pending, wait_s=wait_s)
        _slots = threading.BoundedSemaphore(max_concurrent)


def limits() -> dict[str, float]:
    return dict(_limits)


def _bounded(fn: Callable[..., object], slots: threading.BoundedSemaphore, *args: str) -> object:
    """Runs in a worker thread: wait for a slot (bounded), then hash."""
    if not slots.acquire(timeout=_limits["wait_s"]):
        raise HasherBusyError
    try:
        return fn(*args)
    finally:
        slots.release()


async def _run(fn: Callable[..., object], *args: str) -> object:
    global _pending
    with _gate:
        if _pending >= _limits["max_pending"]:
            raise HasherBusyError
        _pending += 1
        slots = _slots
    try:
        return await asyncio.to_thread(_bounded, fn, slots, *args)
    finally:
        with _gate:
            _pending -= 1


_BASES_TEXT = (
    "password passw0rd p4ssword pa55word qwerty qwertyuiop asdfgh asdfghjkl "
    "zxcvbnm letmein welcome admin administrator root iloveyou monkey dragon "
    "football baseball basketball master sunshine princess shadow superman batman "
    "michael jessica charlie trustno1 starwars login abc123 secret changeme "
    "default guest test testing temp temporary hello hunter freedom whatever "
    "ginger summer winter spring autumn flower cookie internet computer samsung "
    "google facebook linkedin youtube gaming soccer hockey killer ninja pokemon "
    "naruto matrix batman2 cheese banana orange purple yellow silver golden "
    "diamond smarthome smarthomefuture homeassistant mysmarthome myhouse myhome "
    "home house family owner member guest123 vietnam hanoi saigon danang vinuni "
    "iotdevice zigbee lightbulb"
)
_BASES = _BASES_TEXT.split()
_SUFFIXES = (
    "",
    "1",
    "12",
    "123",
    "1234",
    "12345",
    "123456",
    "1234567",
    "!",
    "!!",
    "@123",
    "#1",
    "01",
    "007",
    "2020",
    "2021",
    "2022",
    "2023",
    "2024",
    "2025",
    "2026",
    "!@#",
)
_PATTERNS_TEXT = (
    "123456789012 1234567890123 12345678901234 111111111111 000000000000 "
    "123123123123 abcdefghijkl abcdefghijklm qwertyuiopas qwertyuiop12 "
    "1q2w3e4r5t6y 1qaz2wsx3edc zaq12wsxcde3 asdfghjkl123 iloveyou1234 letmein12345 "
    "passwordpassword adminadminadmin password1234 password12345 qwerty123456 "
    "welcome12345 changeme1234 trustno1trustno1"
)
_PATTERNS = _PATTERNS_TEXT.split()


@lru_cache(maxsize=1)
def common_passwords() -> frozenset[str]:
    words = {base + suffix for base in _BASES for suffix in _SUFFIXES}
    words |= {base.capitalize() + suffix for base in _BASES for suffix in _SUFFIXES}
    words |= set(_PATTERNS)
    return frozenset(w.lower() for w in words)


class WeakPasswordError(ValueError):
    """The password breaks the policy. ``reason`` is safe to show to the user."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def normalize(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


def check_policy(password: str, email: str | None = None) -> str:
    """Return the normalised password, or raise WeakPasswordError."""
    pw = normalize(password)
    if len(pw) < MIN_LENGTH:
        raise WeakPasswordError(f"Mật khẩu cần ít nhất {MIN_LENGTH} ký tự.")
    if len(pw) > MAX_LENGTH:
        raise WeakPasswordError(f"Mật khẩu tối đa {MAX_LENGTH} ký tự.")
    lowered = pw.lower()
    if lowered in common_passwords():
        raise WeakPasswordError("Mật khẩu này quá phổ biến.")
    if email:
        address = email.strip().lower()
        if lowered in {address, address.split("@")[0]}:
            raise WeakPasswordError("Mật khẩu không được trùng email.")
    return pw


def _hash(password: str) -> str:
    return _HASHER.hash(normalize(password))


def _verify(stored_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(stored_hash, normalize(password))
    except (VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    """Raises HasherBusyError when the hashing capacity is exhausted."""
    return await _run(_hash, password)  # type: ignore[return-value]


async def verify_password(stored_hash: str, password: str) -> bool:
    """Raises HasherBusyError when the hashing capacity is exhausted."""
    return await _run(_verify, stored_hash, password)  # type: ignore[return-value]


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    """A valid hash of a random value, verified when the account does not exist so that unknown
    and known emails cost the same time (no account enumeration by timing)."""
    return _hash("dummy-password-for-timing-equalisation")


def parameters() -> dict[str, int]:
    return {
        "time_cost": _HASHER.time_cost,
        "memory_cost_kib": _HASHER.memory_cost,
        "parallelism": _HASHER.parallelism,
    }
