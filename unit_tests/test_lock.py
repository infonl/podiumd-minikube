"""podiumd_minikube.lock: the shared cluster's lock file."""

import fcntl
import subprocess
import sys

from collections.abc import Generator
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest

from podiumd_minikube import lock
from podiumd_minikube.process import UserError


@pytest.mark.usefixtures("private_lock")
def test_take_writes_who_what_and_time_and_release_removes_it():
    assert lock.take("deploy --full")
    hold = lock.current()
    assert hold is not None
    assert (hold.who, hold.what) == ("podiumd-minikube", "deploy --full")
    lock.release()
    assert lock.current() is None


def test_a_held_lock_refuses_with_its_holder(private_lock: Path):
    private_lock.write_text("podiumd-tests run full minikube 2026-10-08T12:00:00+00:00\n", encoding="utf-8")
    with pytest.raises(UserError, match=r"locked by podiumd-tests \(run full minikube\)"):
        lock.take("deploy")


def test_a_nested_hold_of_the_same_process_does_not_block():
    with lock.held("deploy"):
        assert not lock.take("flush-redis")
        assert lock.current() is not None
    assert lock.current() is None


def test_release_held_by_only_removes_that_holders_lock(private_lock: Path):
    private_lock.write_text("podiumd-tests bootstrap 2026-10-08T12:00:00+00:00\n", encoding="utf-8")
    assert lock.release_held_by("kees") is not None
    assert private_lock.exists()
    lock.release_held_by("podiumd-tests")
    assert not private_lock.exists()


def test_describe_shows_the_age_and_hints_at_a_stale_lock():
    now = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
    fresh = lock.parse(f"kees live cleanup {(now - timedelta(minutes=7)).isoformat()}")
    old = lock.parse(f"kees live cleanup {(now - timedelta(hours=3)).isoformat()}")
    assert lock.describe(fresh, now) == "kees (live cleanup) for 7m"
    assert lock.describe(old, now).endswith("for 3h00m; if its holder is gone: podiumd-minikube cluster-lock break")


@pytest.mark.usefixtures("private_lock")
def test_run_marks_the_hold_for_its_child():
    child = [sys.executable, "-c", f"import os; assert os.environ[{lock.HOLD_ENV!r}].startswith('kees rebuild ')"]
    assert lock.run("rebuild", child, who="kees") == 0
    assert lock.current() is None


@pytest.mark.usefixtures("private_lock")
def test_a_taken_hold_is_flocked_until_release():
    assert lock.take("deploy")
    with lock.LOCK_FILE.open(encoding="utf-8") as handle, pytest.raises(BlockingIOError):
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    hold = lock.current()
    assert hold is not None
    assert hold.live
    assert "running" in lock.describe(hold)
    lock.release()
    assert lock.current() is None


@pytest.fixture
def running_holder(private_lock: Path) -> Generator[None]:
    """Another process writes a podiumd-tests line to the lock file and keeps it flock-ed."""
    with subprocess.Popen([sys.executable, "-c", FLOCK_AND_WAIT, str(private_lock)], stdout=subprocess.PIPE) as child:
        assert child.stdout is not None
        child.stdout.readline()
        yield
        child.kill()


@pytest.mark.usefixtures("running_holder")
def test_break_refuses_a_running_holder_unless_forced(private_lock: Path):
    hold = lock.current()
    assert hold is not None
    with pytest.raises(UserError, match="held by a running process"):
        lock.break_lock(hold)
    assert lock.break_lock(hold, force=True) == hold
    assert not private_lock.exists()


def test_break_leaves_a_hold_taken_after_it_was_read(private_lock: Path):
    private_lock.write_text("kees upgrade 2026-10-08T12:00:00+00:00\n", encoding="utf-8")
    seen = lock.current()
    assert seen is not None
    private_lock.write_text("podiumd-tests run full minikube 2026-10-08T13:00:00+00:00\n", encoding="utf-8")
    assert lock.break_lock(seen) is None
    assert private_lock.exists()


# Writes a podiumd-tests line to the lock file named in argv, flocks it and holds it until killed.
FLOCK_AND_WAIT = r"""
import fcntl, sys, time
handle = open(sys.argv[1], "w")
fcntl.flock(handle, fcntl.LOCK_EX)
handle.write("podiumd-tests run full minikube 2026-10-08T12:00:00+00:00\n")
handle.flush()
print("held", flush=True)
time.sleep(60)
"""
