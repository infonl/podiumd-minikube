"""podiumd_minikube.restart: telling a failed ZAC boot from one still running."""

import json

from datetime import datetime
from typing import Any

import pytest

from conftest import FakeRun

from podiumd_minikube import deploy
from podiumd_minikube import provision
from podiumd_minikube import restart
from podiumd_minikube import tunnel
from podiumd_minikube.process import UserError


def _workload(name: str, ready: int) -> dict[str, Any]:
    status = {"observedGeneration": 1, "replicas": 1, "updatedReplicas": 1, "readyReplicas": ready}
    return {"kind": "Deployment", "metadata": {"name": name, "generation": 1}, "status": status}


def _pod(name: str, ready_since: str, owner: str = "ReplicaSet") -> dict[str, Any]:
    return {
        "metadata": {"name": name, "ownerReferences": [{"kind": owner}]},
        "status": {"conditions": [{"type": "Ready", "status": "True", "lastTransitionTime": ready_since}]},
    }


def test_stale_pods_are_those_ready_since_before_the_node_started():
    boot = datetime.fromisoformat("2026-10-08T15:56:15.304156778Z")
    pods = [
        _pod("zac", "2026-10-08T14:05:00Z"),
        _pod("openzaak", "2026-10-08T15:57:00Z"),
        _pod("seed", "2026-10-08T14:05:00Z", owner="Job"),
    ]
    assert restart.stale_pods(pods, boot) == ["zac"]


BOOT = "2026-10-08T15:56:15Z"


def test_node_started_reads_the_node_containers_start_time(fake_run: FakeRun):
    fake_run.on("docker", "inspect", stdout=f"{BOOT}\n")
    assert restart.node_started() == datetime.fromisoformat(BOOT)
    assert fake_run.calls == [["docker", "inspect", "minikube", "--format", "{{.State.StartedAt}}"]]


@pytest.fixture
def started(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """start() with a node booted at BOOT, no waiting, and the tunnel recorded."""
    calls: list[str] = []
    monkeypatch.setattr(provision, "start_node", lambda: calls.append("start_node"))
    monkeypatch.setattr(tunnel, "setup_tunnel", lambda *, ask: calls.append("setup_tunnel"))
    monkeypatch.setattr(deploy, "READY_TIMEOUT", 0)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    fake_run.on("docker", "inspect", stdout=BOOT)
    fake_run.on("kubectl", "get", "pods", stdout=json.dumps({"items": [_pod("zac", "2026-10-08T16:00:00Z")]}))
    workloads = [_workload("openzaak", 1), _workload("zac", 1)]
    fake_run.on("kubectl", "get", "deployment,statefulset", stdout=json.dumps({"items": workloads}))
    fake_run.on("kubectl", "get", "deployment/zac", stdout=json.dumps(_workload("zac", 1)))
    return calls


def test_start_waits_for_the_workloads_then_starts_the_tunnel(fake_run: FakeRun, started: list[str]):
    restart.start()
    assert started == ["start_node", "setup_tunnel"]
    assert not fake_run.ran("kubectl", "rollout")


@pytest.mark.parametrize(
    ("answer", "items", "error"),
    [
        ("pods", [_pod("zac", "2026-10-08T14:05:00Z")], "before the start after 0s: zac"),
        ("deployment,statefulset", [_workload("openzaak", 0), _workload("zac", 1)], "not ready after 0s: openzaak:"),
    ],
)
def test_start_names_what_is_not_ready(
    fake_run: FakeRun, started: list[str], answer: str, items: list[dict[str, Any]], error: str
):
    fake_run.on("kubectl", "get", answer, stdout=json.dumps({"items": items}))
    with pytest.raises(UserError, match=error):
        restart.start()
    assert started == ["start_node"]


@pytest.mark.parametrize(("states", "recovered"), [(["failed", "ready"], True), (["failed", "failed"], False)])
def test_start_restarts_zac_once_after_a_failed_boot(
    fake_run: FakeRun, started: list[str], monkeypatch: pytest.MonkeyPatch, *, states: list[str], recovered: bool
):
    answers = iter(states)
    monkeypatch.setattr(deploy, "zac_state", lambda: next(answers))
    if recovered:
        restart.start()
    else:
        with pytest.raises(UserError, match="ZAC not ready after 0s"):
            restart.start()
    assert fake_run.ran("kubectl", "rollout") == [["kubectl", "rollout", "restart", "deployment/zac", "-n", "podiumd"]]
    assert started[-1] == ("setup_tunnel" if recovered else "start_node")
