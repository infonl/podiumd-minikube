"""Waiting for a condition with a deadline (never a bare sleep)."""

import time

from collections.abc import Callable


def wait_until[T](probe: Callable[[], T | None], *, timeout: float, interval: float) -> T | None:
    """First truthy probe() result within timeout seconds, or None."""
    deadline = time.monotonic() + timeout
    while True:
        result = probe()
        if result:
            return result
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)
