"""podiumd_minikube.disk: the free-space verdict."""

from typing import NamedTuple

import pytest

from conftest import FakeRun

from podiumd_minikube import disk
from podiumd_minikube.process import UserError

GIB = disk.GIB


class Usage(NamedTuple):
    total: int
    used: int
    free: int


def test_verdict_fails_below_the_minimum_and_warns_when_tight():
    need = disk.Need(fail_gib=3, warn_gib=10)
    assert disk.verdict(100 * GIB, 2 * GIB, need)[0] == "fail"
    assert disk.verdict(100 * GIB, 5 * GIB, need)[0] == "warn"
    assert disk.verdict(100 * GIB, 12 * GIB, need)[0] == "warn"  # 88% used
    assert disk.verdict(100 * GIB, 24 * GIB, need)[0] == "ok"


@pytest.mark.parametrize(("free_gib", "out", "err"), [(30, "Disk /d: 30.0 GiB free", ""), (20, "", "WARNING: disk /d")])
def test_check_reports_on_dockers_data_directory(
    fake_run: FakeRun,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    free_gib: int,
    out: str,
    err: str,
):
    fake_run.on("docker", "info", stdout="/d\n")
    monkeypatch.setattr("shutil.disk_usage", lambda path: Usage(100 * GIB, 0, free_gib * GIB) if path == "/d" else None)
    disk.check(disk.PROVISION)
    captured = capsys.readouterr()
    assert out in captured.out and err in captured.err
    assert fake_run.calls == [["docker", "info", "--format", "{{.DockerRootDir}}"]]


def test_check_stops_below_the_minimum(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    fake_run.on("docker", "info", stdout="/d\n")
    monkeypatch.setattr("shutil.disk_usage", lambda _path: Usage(100 * GIB, 0, 10 * GIB))
    with pytest.raises(UserError, match=r"disk /d: .*need at least 15 GiB"):
        disk.check(disk.PROVISION)
