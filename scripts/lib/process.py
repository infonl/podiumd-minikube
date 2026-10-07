"""The one place that starts external processes (kubectl, helm, minikube, docker)."""

import subprocess  # nosec B404
import sys

from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path


class UserError(Exception):
    """A failure the user can fix; printed without a traceback."""


class ProcessError(UserError):
    """A command exited non-zero."""

    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{' '.join(args)} failed ({returncode}): {stderr.strip()}")


def run(
    args: Sequence[str],
    *,
    stdin: str | None = None,
    check: bool = True,
    capture: bool = True,
    merge_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Runs args; captured output is text, merge_stderr puts stderr in stdout in order.

    check raises ProcessError on a non-zero exit.
    """
    pipe = subprocess.PIPE if capture else None
    result = subprocess.run(  # nosec B603  # noqa: S603 - argv lists, never a shell
        list(args),
        input=stdin,
        stdout=pipe,
        stderr=subprocess.STDOUT if capture and merge_stderr else pipe,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise ProcessError(args, result.returncode, result.stderr or "")
    return result


def output(args: Sequence[str], *, stdin: str | None = None) -> str:
    """Stdout of args; raises ProcessError on a non-zero exit."""
    return run(args, stdin=stdin).stdout


def succeeds(args: Sequence[str]) -> bool:
    """Whether args exits 0; output is discarded."""
    return run(args, check=False).returncode == 0


def running(pattern: str) -> str:
    """PID of the first process whose command line matches pattern, or ""."""
    result = run(["pgrep", "-f", pattern], check=False)
    return result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else ""


def spawn(args: Sequence[str], log: Path) -> None:
    """Starts args detached from this terminal, output to log; it outlives this script."""
    with log.open("w", encoding="utf-8") as out:
        # Not waited for: it keeps running after this script exits.
        subprocess.Popen(  # pylint: disable=consider-using-with  # nosec B603  # noqa: S603
            list(args), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True
        )


def main(entry: Callable[[], int | None]) -> None:
    """Runs a script's entry point, printing a UserError as one line on stderr."""
    # Child processes write straight to the terminal; keep print() in order with them.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(line_buffering=True)
    try:
        code = entry()
    except UserError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(code or 0)
