"""lib.lock: the shared cluster's lock file."""

import sys

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest

from lib import lock
from lib.process import UserError


def test_take_writes_who_what_and_time_and_release_removes_it(private_lock: Path):
    assert lock.take("deploy --full")
    hold = lock.current()
    assert hold is not None and (hold.who, hold.what) == ("podiumd-minikube", "deploy --full")
    lock.release()
    assert lock.current() is None


def test_a_held_lock_refuses_with_its_holder(private_lock: Path):
    private_lock.write_text("podiumd-tests run full minikube 2026-10-08T12:00:00+00:00\n", encoding="utf-8")
    with pytest.raises(UserError, match=r"locked by podiumd-tests \(run full minikube\)"):
        lock.take("deploy")


def test_a_nested_hold_of_the_same_process_does_not_block():
    with lock.held("deploy"):
        assert not lock.take("apply-pabc-migrations")
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
    assert lock.describe(old, now).endswith("for 3h00m; if its holder is gone: ./scripts/cluster-lock break")


def test_run_marks_the_hold_for_its_child(private_lock: Path):
    child = [sys.executable, "-c", f"import os; assert os.environ['{lock.HOLD_ENV}'].startswith('kees rebuild ')"]
    assert lock.run("rebuild", child, who="kees") == 0
    assert lock.current() is None
