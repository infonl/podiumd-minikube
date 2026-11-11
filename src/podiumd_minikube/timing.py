"""Durations of deploy phases in the log."""

import time

from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def timed(label: str) -> Generator[None]:
    """Print label with the seconds the block took, when it ends without an error."""
    start = time.monotonic()
    yield
    print(f"==== {label}: {time.monotonic() - start:.0f} s ====", flush=True)
