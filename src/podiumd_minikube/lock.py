"""The lock on the shared cluster: whoever changes it (a script here, podiumd-tests' CLI, a person) holds it first.

One line "<who> <what> <ISO start time>", created exclusively (O_EXCL), the
format podiumd-tests' lock.py writes too. A running holder keeps an flock on
the file, so a live hold is told from a leftover line, also across PID
namespaces; taking, releasing and breaking a hold run under a flock on the
guard file, as in podiumd-tests. A hold is marked in the environment, so
scripts started inside it (`cluster-lock run`, deploy calling pabc) do not
block on their own lock.
"""

import fcntl
import functools
import getpass
import os

from collections.abc import Callable
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TextIO

from podiumd_minikube import process
from podiumd_minikube.paths import CHART_DIR

# Next to the podiumd-minikube and podiumd-tests checkouts (podiumd-tests' envs/minikube.yaml lock_file).
LOCK_FILE = Path(os.environ.get("MINIKUBE_LOCK_FILE", str(CHART_DIR.parent / ".minikube-lock")))
HOLD_ENV = "MINIKUBE_LOCK_HOLD"
HOLDER = "podiumd-minikube"
# A hold older than this is mentioned as possibly stale; a full test tier can take an hour.
HOUR = 3600
STALE_SECONDS = 2 * HOUR
# who, what and the ISO start time
HOLD_FIELDS = 3
# The open lock file per path while this process holds it: its flock marks the hold as live.
_FLOCKED: dict[Path, TextIO] = {}


@dataclass(frozen=True)
class Hold:
    """The lock file's line, parsed."""

    line: str
    who: str
    what: str
    since: datetime | None
    live: bool = False


def parse(line: str) -> Hold:
    """Return a Hold from "<who> <what> <ISO start time>"; what may contain spaces."""
    parts = line.split()
    since = None
    if len(parts) >= HOLD_FIELDS:
        try:
            since = datetime.fromisoformat(parts[-1])
        except ValueError:
            since = None
    what = " ".join(parts[1:-1] if since else parts[1:])
    return Hold(line=line, who=parts[0] if parts else "unknown", what=what, since=since)


def current(path: Path | None = None) -> Hold | None:
    """Return the hold, or None when the lock is free."""
    path = path or LOCK_FILE
    try:
        line = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return replace(parse(line), live=_is_flocked(path))


def _is_flocked(path: Path) -> bool:
    try:
        handle = path.open(encoding="utf-8")
    except FileNotFoundError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


@contextmanager
def _guarded(path: Path) -> Generator[None]:
    """Hold the guard file's flock while the block runs; other takers, releasers and breakers wait."""
    with path.with_name(f"{path.name}.guard").open("w", encoding="utf-8") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        yield


def describe(hold: Hold, now: datetime | None = None) -> str:
    """'<who> (<what>) for <age>', with a hint when it is old and no process holds it."""
    text = f"{hold.who} ({hold.what}{', running' if hold.live else ''})"
    if hold.since is None:
        return text
    age = int(((now or datetime.now(UTC)) - hold.since).total_seconds())
    hours, seconds = divmod(age, HOUR)
    duration = f"{hours}h{seconds // 60:02d}m" if hours else f"{age // 60}m"
    stale = age > STALE_SECONDS and not hold.live
    hint = "; if its holder is gone: podiumd-minikube cluster-lock break" if stale else ""
    return f"{text} for {duration}{hint}"


def _is_ours(hold: Hold | None) -> bool:
    return hold is not None and os.environ.get(HOLD_ENV) == hold.line


def take(what: str, *, who: str = HOLDER, path: Path | None = None) -> bool:  # noqa: FNE005 - takes the lock
    """Take the lock; False when this process already holds it. UserError names the holder when busy."""
    path = path or LOCK_FILE
    if _is_ours(current(path)):
        return False
    line = f"{who} {what} {datetime.now(UTC).isoformat(timespec='seconds')}"
    with _guarded(path):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            hold = current(path)
            msg = f"the cluster is locked by {describe(hold) if hold else 'unknown'}: wait, or ask its holder ({path})"
            raise process.UserError(msg) from None
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.write(f"{line}\n")
        handle.flush()
    _FLOCKED[path] = handle
    os.environ[HOLD_ENV] = line
    return True


def release(path: Path | None = None) -> None:
    """Remove the lock if this process holds it."""
    path = path or LOCK_FILE
    with _guarded(path):
        if _is_ours(current(path)):
            path.unlink(missing_ok=True)
    handle = _FLOCKED.pop(path, None)
    if handle is not None:
        handle.close()
    os.environ.pop(HOLD_ENV, None)


@contextmanager
def held(what: str, *, who: str = HOLDER, path: Path | None = None) -> Generator[None]:
    """Hold the lock while the block runs; a nested hold of the same process is a no-op."""
    path = path or LOCK_FILE
    taken = take(what, who=who, path=path)
    try:
        yield
    finally:
        if taken:
            release(path)


def holding[**P, R](what: str) -> Callable[[Callable[P, R]], Callable[P, R]]:  # noqa: WPS111 - type parameters
    """Return a decorator that runs the function while holding the lock (see held)."""

    def wrap(function: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(function)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            with held(what):
                return function(*args, **kwargs)

        return inner

    return wrap


def person() -> str:
    """Return the holder name for a lock taken by hand."""
    return getpass.getuser()


def release_held_by(who: str, path: Path | None = None) -> Hold | None:
    """Remove the lock when who holds it (taken by hand, in another process); return the hold found."""
    path = path or LOCK_FILE
    with _guarded(path):
        hold = current(path)
        if hold is not None and hold.who == who:
            path.unlink(missing_ok=True)
    return hold


def break_lock(expected: Hold, *, force: bool = False, path: Path | None = None) -> Hold | None:
    """Remove the expected hold and return it; None when the lock changed. UserError while a process holds it."""
    path = path or LOCK_FILE
    with _guarded(path):
        hold = current(path)
        if hold is None or hold.line != expected.line:
            return None
        if hold.live and not force:
            msg = f"the lock of {describe(hold)} is held by a running process: stop it, or break with --force"
            raise process.UserError(msg)
        path.unlink(missing_ok=True)
    return hold


def run(what: str, command: list[str], *, who: str, path: Path | None = None) -> int:
    """Run command while holding the lock; scripts it starts see the hold as their own."""
    path = path or LOCK_FILE
    with held(what, who=who, path=path):
        return process.run(command, check=False, capture=False).returncode
