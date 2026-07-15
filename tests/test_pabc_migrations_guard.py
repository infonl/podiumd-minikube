"""
Verifies scripts/apply-pabc-migrations.sh actually prevents the destructive
scenario it exists for: the pabc-migrations Job clears PABC's database
before reloading its seed dataset every time it's created (confirmed live,
not idempotent), so recreating it against an already-seeded database must
be refused unless --force is passed.

Unlike the rest of this suite, `test_guard_refuses_to_recreate_job_when_data_exists`
genuinely mutates cluster state - it deletes the real pabc-migrations-1 Job
to reach the scenario the guard protects against, then restores it via
--force in a `finally` block either way. This is safe to repeat: the
restore reloads the exact same vendored seed dataset, so the end state
(row count) is identical to what existed before the test ran, confirmed by
the assertions themselves.
"""

import os
import subprocess

import pytest

from conftest import NAMESPACE, kubectl

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "apply-pabc-migrations.sh"
)
JOB_NAME = "pabc-migrations-1"


def _postgres_pod_name():
    return kubectl(
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        "app=postgres",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ).strip()


def _mapping_row_count():
    postgres_pod = _postgres_pod_name()
    result = kubectl(
        "exec",
        "-n",
        NAMESPACE,
        postgres_pod,
        "--",
        "psql",
        "-U",
        "postgres",
        "-d",
        "Pabc",
        "-t",
        "-A",
        "-c",
        "SELECT count(*) FROM mapping;",
    )
    return int(result.strip())


def _run_guard_script(*args):
    return subprocess.run(
        ["bash", SCRIPT_PATH, *args],
        capture_output=True,
        text=True,
        timeout=150,
    )


@pytest.fixture(autouse=True)
def _skip_if_pabc_not_deployed(pods):
    if not any(p["name"].startswith("pabc") for p in pods):
        pytest.skip("pabc is not deployed")


def test_guard_leaves_succeeded_job_alone():
    """The steady state: Job already succeeded - the script should be a
    read-only no-op, not touch the database at all."""
    status = kubectl(
        "get", "job", JOB_NAME, "-n", NAMESPACE, "-o", "jsonpath={.status.succeeded}"
    ).strip()
    if status != "1":
        pytest.skip(f"{JOB_NAME} is not currently in a succeeded state")

    before = _mapping_row_count()
    result = _run_guard_script()
    after = _mapping_row_count()

    assert result.returncode == 0
    assert "leaving it alone" in result.stdout
    assert before == after, "the guard mutated data on what should have been a no-op"


def test_guard_refuses_to_recreate_job_when_data_exists():
    """
    The actual dangerous scenario this guard exists for: the Job has been
    deleted (e.g. for troubleshooting, exactly as happened twice already
    during this project's own live verification) but the database still
    has real data. Running the script without --force must refuse and
    leave that data untouched; only --force may recreate it.
    """
    before = _mapping_row_count()
    assert before > 0, "test assumes the Pabc database is already seeded"

    kubectl("delete", "job", JOB_NAME, "-n", NAMESPACE)
    try:
        result = _run_guard_script()
        assert result.returncode == 1
        assert "Refusing" in result.stdout
        assert _mapping_row_count() == before, "the guard should not have touched the data"
    finally:
        # Restore: recreate the Job with --force. This reloads the exact
        # same vendored seed dataset the database already had, so the row
        # count returns to `before` either way - not a net change.
        restore = _run_guard_script("--force")
        assert restore.returncode == 0
        assert _mapping_row_count() == before
