"""The lock on the shared cluster: whoever changes it (a script here, podiumd-tests' CLI, a person) holds it first.

One line "<who> <what> <ISO start time>", created exclusively (O_EXCL), the
format podiumd-tests' lock.py writes too. A hold is marked in the environment,
so scripts started inside it (`cluster-lock run`, deploy calling pabc) do not
block on their own lock.
"""

import functools
import getpass
import os

from collections.abc import Callable
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

from lib import process
from lib.paths import CHART_DIR

# Next to the podiumd-minikube and podiumd-tests checkouts (podiumd-tests' envs/minikube.yaml lock_file).
LOCK_FILE = Path(os.environ.get("MINIKUBE_LOCK_FILE", str(CHART_DIR.parent / ".minikube-lock")))
HOLD_ENV = "MINIKUBE_LOCK_HOLD"
HOLDER = "podiumd-minikube"
# A hold older than this is mentioned as possibly stale; a full test tier can take an hour.
STALE_SECONDS = 2 * 3600


@dataclass(frozen=True)
class Hold:
    """The lock file's line, parsed."""

    line: str
    who: str
    what: str
    since: datetime | None


def parse(line: str) -> Hold:
    """A Hold from "<who> <what> <ISO start time>"; what may contain spaces."""
    parts = line.split()
    since = None
    if len(parts) >= 3:
        try:
            since = datetime.fromisoformat(parts[-1])
        except ValueError:
            since = None
    what = " ".join(parts[1:-1] if since else parts[1:])
    return Hold(line=line, who=parts[0] if parts else "unknown", what=what, since=since)


def current(path: Path | None = None) -> Hold | None:
    """The hold, or None when the lock is free."""
    path = path or LOCK_FILE
    try:
        line = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return parse(line)


def describe(hold: Hold, now: datetime | None = None) -> str:
    """'<who> (<what>) for <age>', with a hint when it may be stale."""
    text = f"{hold.who} ({hold.what})"
    if hold.since is None:
        return text
    age = int(((now or datetime.now(UTC)) - hold.since).total_seconds())
    text += f" for {age // 3600}h{age % 3600 // 60:02d}m" if age >= 3600 else f" for {age // 60}m"
    if age > STALE_SECONDS:
        text += "; if its holder is gone: ./scripts/cluster-lock break"
    return text


def _ours(hold: Hold | None) -> bool:
    return hold is not None and os.environ.get(HOLD_ENV) == hold.line


def take(what: str, *, who: str = HOLDER, path: Path | None = None) -> bool:
    """Takes the lock; False when this process already holds it. UserError names the holder when busy."""
    path = path or LOCK_FILE
    if _ours(current(path)):
        return False
    line = f"{who} {what} {datetime.now(UTC).isoformat(timespec='seconds')}"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        hold = current(path)
        msg = f"the cluster is locked by {describe(hold) if hold else 'unknown'}: wait, or ask its holder ({path})"
        raise process.UserError(msg) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(line + "\n")
    os.environ[HOLD_ENV] = line
    return True


def release(path: Path | None = None) -> None:
    """Removes the lock if this process holds it."""
    path = path or LOCK_FILE
    if _ours(current(path)):
        path.unlink(missing_ok=True)
    os.environ.pop(HOLD_ENV, None)


@contextmanager
def held(what: str, *, who: str = HOLDER, path: Path | None = None) -> Generator[None]:
    """Holds the lock while the block runs; a nested hold of the same process is a no-op."""
    path = path or LOCK_FILE
    taken = take(what, who=who, path=path)
    try:
        yield
    finally:
        if taken:
            release(path)


def holding[**P, R](what: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: the function runs while holding the lock (see held)."""

    def wrap(function: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(function)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            with held(what):
                return function(*args, **kwargs)

        return inner

    return wrap


def person() -> str:
    """The holder name for a lock taken by hand."""
    return getpass.getuser()


def release_held_by(who: str, path: Path | None = None) -> Hold | None:
    """Removes the lock when who holds it (taken by hand, in another process); returns the hold found."""
    path = path or LOCK_FILE
    hold = current(path)
    if hold is not None and hold.who == who:
        path.unlink(missing_ok=True)
    return hold


def break_lock(path: Path | None = None) -> Hold | None:
    """Removes the lock whoever holds it; returns the hold that was removed."""
    path = path or LOCK_FILE
    hold = current(path)
    path.unlink(missing_ok=True)
    return hold


def run(what: str, command: list[str], *, who: str, path: Path | None = None) -> int:
    """Runs command while holding the lock; scripts it starts see the hold as their own."""
    path = path or LOCK_FILE
    with held(what, who=who, path=path):
        return process.run(command, check=False, capture=False).returncode
