"""Argon2id benchmark for M-07-1: run it on the machine that will host the backend.

    python scripts/bench_argon2.py [--runs 30] [--warmup 3] [--json]

Measures hash and verify latency (p50, p95, min, max) with the production parameters, and the
worst event-loop delay while five verifications run concurrently (they run in worker threads, so
the loop should stay responsive). Uses a random throwaway password; never prints a password or
a hash. Not a test: the numbers depend on the hardware, so nothing here passes or fails, and the
parameters are never changed automatically. LLD P0-07 expects 100 ms to 1 s per verification on
the target machine.
"""

import argparse
import asyncio
import json
import os
import platform
import secrets
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smarthome.auth import passwords  # noqa: E402


def _percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def _summary(samples: list[float]) -> dict[str, float]:
    ms = [s * 1000 for s in samples]
    return {
        "p50_ms": round(statistics.median(ms), 1),
        "p95_ms": round(_percentile(ms, 0.95), 1),
        "min_ms": round(min(ms), 1),
        "max_ms": round(max(ms), 1),
    }


def _memory_gib() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong),
                    ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_ext", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.total / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (AttributeError, OSError, ValueError):
        return None


async def _loop_delay(hashed: str, password: str) -> float:
    """Worst lateness of a 10 ms ticker while five verifications run at once."""
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

    task = asyncio.create_task(ticker())
    await asyncio.gather(*(passwords.verify_password(hashed, password) for _ in range(5)))
    done.set()
    await task
    return worst


async def main(runs: int, warmup: int) -> dict[str, object]:
    password = secrets.token_urlsafe(18)  # throwaway; never printed
    for _ in range(warmup):
        await passwords.verify_password(await passwords.hash_password(password), password)
    hash_times, verify_times = [], []
    hashed = ""
    for _ in range(runs):
        start = time.perf_counter()
        hashed = await passwords.hash_password(password)
        hash_times.append(time.perf_counter() - start)
        start = time.perf_counter()
        assert await passwords.verify_password(hashed, password)
        verify_times.append(time.perf_counter() - start)
    return {
        "parameters": passwords.parameters(),
        "runs": runs,
        "warmup": warmup,
        "hash": _summary(hash_times),
        "verify": _summary(verify_times),
        "event_loop_worst_delay_ms": round(await _loop_delay(hashed, password) * 1000, 1),
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "logical_cpus": os.cpu_count(),
            "memory_gib": _memory_gib(),
            "python": platform.python_version(),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(main(args.runs, args.warmup))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
