"""podiumd_minikube.memory: the node's wanted memory and its warnings."""

import pytest

from conftest import FakeRun

from podiumd_minikube import memory


def test_wanted_is_half_the_host_unless_overridden():
    assert memory.wanted_mb(96 * 1024, None) == 48 * 1024
    assert memory.wanted_mb(96 * 1024, "20480") == 20480


GIB = 1024**3


@pytest.mark.parametrize(("returncode", "stdout", "mb"), [(0, f"{8 * GIB}\n", 8192), (0, "0\n", 0), (1, "", 0)])
def test_node_mb_is_the_profiles_cap(returncode: int, stdout: str, mb: int, fake_run: FakeRun):
    fake_run.on("docker", "inspect", returncode=returncode, stdout=stdout)
    assert memory.node_mb() == mb


def test_wanted_is_half_of_what_docker_sees(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MINIKUBE_MEMORY", raising=False)
    fake_run.on("docker", "info", stdout=f"{64 * GIB}\n")
    assert memory.wanted() == 32 * 1024


@pytest.mark.parametrize(("full", "cap_gib", "warned"), [(True, 8, True), (True, 17, False), (False, 8, False)])
def test_check_warns_only_for_a_small_full_node(
    fake_run: FakeRun, capsys: pytest.CaptureFixture[str], *, full: bool, cap_gib: int, warned: bool
):
    fake_run.on("docker", "inspect", stdout=str(cap_gib * GIB))
    fake_run.on("docker", "info", stdout=str(64 * GIB))
    memory.check(full=full)
    err = capsys.readouterr().err
    assert ("docker update --memory=32g --memory-swap=-1 minikube" in err) is warned
    assert ("has 8 GiB" in err) is warned
