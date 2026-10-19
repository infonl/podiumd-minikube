"""podiumd_minikube.services: the Postgres port-forward and flushing Redis."""

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import kube
from podiumd_minikube import process
from podiumd_minikube import services
from podiumd_minikube.process import UserError

EXEC = ["kubectl", "exec", "-n", "podiumd-minikube"]


@pytest.fixture
def cluster(fake_run: FakeRun, poll_once: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRun:
    """minikube context, a pod per selector and a port-forward log in tmp_path."""
    monkeypatch.setattr(kube, "require_minikube_context", lambda: None)
    monkeypatch.setattr(kube, "first_pod", lambda selector: f"pod-{selector}")
    monkeypatch.setattr(services, "FORWARD_LOG", tmp_path / "forward.log")
    return fake_run


def test_expose_postgres_reuses_a_running_forward(cluster: FakeRun, capsys: pytest.CaptureFixture[str]):
    cluster.on("pgrep", stdout="42\n")
    cluster.on("kubectl", "exec", stdout="openzaak\nzac\n")
    services.expose_postgres(15432)
    out = capsys.readouterr().out
    assert "Already forwarding localhost:15432 -> postgres:5432 (PID 42)." in out
    assert "  - openzaak\n  - zac\n" in out
    assert cluster.ran("kubectl", "exec")[0][:5] == [*EXEC, "pod-app=postgres"]


def test_expose_postgres_starts_a_forward(cluster: FakeRun, monkeypatch: pytest.MonkeyPatch):
    cluster.on("pgrep", returncode=1)
    spawned = []

    def spawn(args: list[str], log: Path) -> None:
        spawned.append(args)
        log.write_text("Forwarding from 127.0.0.1:5432 -> 5432\n", encoding="utf-8")

    monkeypatch.setattr(process, "spawn", spawn)
    services.expose_postgres(5432)
    assert spawned == [["kubectl", "port-forward", "svc/postgres", "5432:5432", "-n", "podiumd-minikube"]]


def test_expose_postgres_fails_when_the_forward_does_not_come_up(cluster: FakeRun, monkeypatch: pytest.MonkeyPatch):
    cluster.on("pgrep", returncode=1)
    monkeypatch.setattr(process, "spawn", lambda args, log: None)
    with pytest.raises(UserError, match="port-forward did not come up within 10s"):
        services.expose_postgres(5432)


def _redis_commands(cluster: FakeRun) -> list[list[str]]:
    return [
        call[len(EXEC) + 3 :] for call in cluster.ran(*EXEC, "pod-app=redis-ha,redis-role=master", "--", "redis-cli")
    ]


def test_flush_redis_one_db(cluster: FakeRun, capsys: pytest.CaptureFixture[str]):
    cluster.on("kubectl", "exec", stdout="7\n")
    services.flush_redis(3)
    assert _redis_commands(cluster) == [["-n", "3", "DBSIZE"], ["-n", "3", "FLUSHDB"], ["-n", "3", "DBSIZE"]]
    assert capsys.readouterr().out == "DB 3: 7 key(s) before flush.\nDB 3: 7 key(s) after flush.\n"


def test_flush_redis_all_dbs(cluster: FakeRun):
    services.flush_redis(None)
    sizes = [["-n", str(number), "DBSIZE"] for number in services.REDIS_DBS]
    assert _redis_commands(cluster) == [*sizes, ["FLUSHALL"], *sizes]
