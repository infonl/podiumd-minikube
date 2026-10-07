"""lib.pabc: the guard around the destructive pabc-migrations Job."""

import pytest

from conftest import FakeRun

from lib import pabc
from lib.process import UserError

JOB_STATUS = ("kubectl", "get", "job", "pabc-migrations-1")
PSQL = ("kubectl", "exec", "-n", "podiumd-minikube", "deploy/postgres")


def test_a_succeeded_job_is_left_alone(fake_run: FakeRun):
    fake_run.on(*JOB_STATUS, stdout="1")
    pabc.apply_migrations(force=False)
    assert fake_run.ran("kubectl", "delete") == []


def test_existing_mappings_are_protected_without_force(fake_run: FakeRun):
    fake_run.on(*JOB_STATUS, returncode=1)
    fake_run.on(*PSQL, stdout="42\n")
    with pytest.raises(UserError, match="42 row"):
        pabc.apply_migrations(force=False)
    assert fake_run.ran("kubectl", "delete") == []


def _expect_recreated(fake_run: FakeRun) -> None:
    assert fake_run.ran("kubectl", "delete", "job", "pabc-migrations-1")
    assert "--show-only" in fake_run.ran("helm", "template")[0]
    assert fake_run.ran("kubectl", "wait")


@pytest.mark.parametrize(("psql_exit", "rows"), [(2, ""), (0, "0")])
def test_the_job_is_recreated_when_pabc_has_no_data(fake_run: FakeRun, psql_exit: int, rows: str):
    fake_run.on(*JOB_STATUS, returncode=1)
    fake_run.on(*PSQL, returncode=psql_exit, stdout=rows)
    pabc.apply_migrations(force=False)
    _expect_recreated(fake_run)


def test_force_recreates_the_job_over_existing_data(fake_run: FakeRun):
    fake_run.on(*JOB_STATUS, stdout="1")
    fake_run.on(*PSQL, stdout="42\n")
    pabc.apply_migrations(force=True)
    _expect_recreated(fake_run)
