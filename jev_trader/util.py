"""Small helpers that keep numbers and JSON identical to what the JS version produced."""
import asyncio
import json
import math
import sys
import time
from typing import Any, Coroutine

_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine) -> asyncio.Task:
    """Fire and forget, like an un-awaited promise. Holds a reference so the task is not collected mid-flight."""
    task = asyncio.ensure_future(coro)
    _tasks.add(task)

    def done(t: asyncio.Task):
        _tasks.discard(t)
        if not t.cancelled() and t.exception():
            print(f"background task failed: {t.exception()!r}", file=sys.stderr)

    task.add_done_callback(done)
    return task


def js_round(x: float, d: int = 0) -> int | float:
    """Math.round(x * 10 ** d) / 10 ** d: halves round up (toward +inf), not to even."""
    if d == 0:
        return math.floor(x + 0.5)
    p = 10**d
    return math.floor(x * p + 0.5) / p


def total(xs) -> float:
    """Left-to-right sum like Array.reduce. Python's sum() compensates float error since 3.12, which would change results."""
    s = 0
    for x in xs:
        s += x
    return s


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def jsnum(x: int | float) -> str:
    """How JS prints a number in a template string: 200, not 200.0."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def dumps(x: Any) -> str:
    """Compact JSON, like JSON.stringify."""
    return json.dumps(x, separators=(",", ":"))


def now_ms() -> int:
    return int(time.time() * 1000)


def perf_ms() -> float:
    return time.perf_counter() * 1000
