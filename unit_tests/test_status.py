"""podiumd_minikube.status: the pure parts of the cluster status."""

import json
import time

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import hosts
from podiumd_minikube import memory
from podiumd_minikube import status
from podiumd_minikube import tunnel
from podiumd_minikube.paths import NAMESPACE


def test_enabled_profiles_match_pod_name_prefixes():
    pods = ["contact-web-6f6d-r2q96", "clamav-0", "podiumd-minikube-grafana-68fb-m7sm2", "zac-cc96-m52qj"]
    profiles = status.enabled_profiles(pods)
    assert profiles["kiss"] and profiles["clamav"] and profiles["metrics"]
    assert not profiles["openinwoner"]


def test_workloads_name_a_deployments_pod_after_the_deployment():
    pods = [
        {"metadata": {"namespace": "ns", "name": "zac-cc96-m52qj", "ownerReferences": [{"kind": "ReplicaSet", "name": "zac-cc96"}]}},
        {"metadata": {"namespace": "ns", "name": "clamav-0", "ownerReferences": [{"kind": "StatefulSet", "name": "clamav"}]}},
        {"metadata": {"namespace": "kube-system", "name": "etcd-minikube"}},
    ]  # fmt: skip
    assert status.workloads(pods) == {
        "ns/zac-cc96-m52qj": "zac",
        "ns/clamav-0": "clamav",
        "kube-system/etcd-minikube": "etcd-minikube",
    }


def test_problems_list_unready_pods_recent_restarts_and_failed_jobs():
    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

    def pod(name: str, *, ready: bool, finished: str | None) -> dict[str, object]:
        last = {"terminated": {"finishedAt": finished}} if finished else {}
        container = {"name": "app", "ready": ready, "lastState": last}
        return {
            "metadata": {"namespace": "ns", "name": name},
            "status": {"phase": "Running", "containerStatuses": [container]},
        }

    pods = [
        pod("ok", ready=True, finished=None),
        pod("rebooted", ready=True, finished=old),
        pod("flapping", ready=True, finished=recent),
        pod("down", ready=False, finished=None),
    ]
    jobs = [{"metadata": {"name": "openzaak-config"}, "status": {"conditions": [{"type": "Failed", "status": "True"}]}}]
    assert status.problems(pods, jobs) == [
        "ns/flapping Running 1/1 restarted: app",
        "ns/down Running 0/1",
        "job openzaak-config Failed",
    ]


def test_grown_allows_twenty_percent_plus_slack():
    assert not status.grown(1200 + 64, 1000)
    assert status.grown(1200 + 65, 1000)


def test_cores_is_zero_for_a_container_without_a_cpu_counter():
    first = {"cpu": {"usageCoreNanoSeconds": {"value": "1000000000"}}}
    assert status.sampled_cores(first, {"attributes": {"id": "x"}}) == 0.0
    assert status.sampled_cores(first, None) == 0.0


@pytest.mark.parametrize(
    ("line", "usage"),
    [
        ("1.5GiB / 16GiB|250.00%\n", status.Usage(mib=1536, cpu=2.5)),
        ("512MiB / 8GiB|0.50%", status.Usage(mib=512, cpu=0.005)),
        ("2048KiB / 8GiB|0%", status.Usage(mib=2, cpu=0.0)),
    ],
)
def test_node_usage_reads_docker_stats(fake_run: FakeRun, line: str, usage: status.Usage):
    fake_run.on("docker", "stats", stdout=line)
    assert status.node_usage() == usage
    assert fake_run.calls[0][:3] == ["docker", "stats", "minikube"]


def _container(cid: str, pod: str, name: str = "app") -> dict[str, object]:
    labels = {
        "io.kubernetes.pod.namespace": "ns",
        "io.kubernetes.pod.name": pod,
        "io.kubernetes.container.name": name,
    }
    return {"id": cid, "labels": labels}


def _stat(cid: str, mib: int, cpu_ns: int | None) -> dict[str, object]:
    cpu = {"usageCoreNanoSeconds": {"value": str(cpu_ns)}} if cpu_ns is not None else {}
    return {"attributes": {"id": cid}, "memory": {"workingSetBytes": {"value": str(mib * status.MIB)}}, "cpu": cpu}


def _crictl(fake_run: FakeRun, what: str, payload: dict[str, object]) -> None:
    fake_run.on("minikube", "ssh", "-p", "minikube", "--", f"sudo crictl {what} -o json", stdout=json.dumps(payload))


def test_container_usage_adds_replicas_and_samples_cpu(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    owner = [{"kind": "ReplicaSet", "name": "zac-cc96"}]
    items = [
        {"metadata": {"namespace": "ns", "name": "zac-cc96-a", "ownerReferences": owner}},
        {"metadata": {"namespace": "ns", "name": "zac-cc96-b", "ownerReferences": owner}},
    ]
    _crictl(
        fake_run,
        "ps",
        {"containers": [_container("1", "zac-cc96-a"), _container("2", "zac-cc96-b"), _container("3", "etcd")]},
    )
    _crictl(
        fake_run,
        "stats",
        {"stats": [_stat("1", 100, 0), _stat("2", 50, 0), _stat("3", 10, None), _stat("gone", 99, 0)]},
    )
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        _crictl(fake_run, "stats", {"stats": [_stat("1", 100, 500_000_000), _stat("2", 50, 250_000_000)]})

    monkeypatch.setattr(time, "sleep", sleep)
    assert status.container_usage(items) == {
        "ns/etcd/app": status.Usage(10, 0.0),
        "ns/zac/app": status.Usage(150, 0.75),
    }
    assert slept == [status.CPU_SAMPLE_SECONDS]


def test_container_usage_without_cpu_takes_one_sample(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(time, "sleep", pytest.fail)
    _crictl(fake_run, "ps", {"containers": [_container("1", "clamav-0")]})
    _crictl(fake_run, "stats", {"stats": [_stat("1", 19, 5)]})
    assert status.container_usage([], cpu=False) == {"ns/clamav-0/app": status.Usage(19, 0.0)}
    assert len(fake_run.ran("minikube", "ssh")) == 2


def test_youngest_container_seconds_skips_containers_that_do_not_run():
    started = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    older = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    items = [
        {"status": {"containerStatuses": [{"state": {"running": {"startedAt": older}}}]}},
        {"status": {"containerStatuses": [{"state": {"running": {"startedAt": started}}}, {"state": {"waiting": {}}}]}},
        {"status": {}},
    ]
    assert 590 < status.youngest_container_seconds(items) < 610


@pytest.mark.parametrize(
    ("profiles", "budget"),
    [({"kiss": False}, memory.DEFAULT_BUDGET_MB), ({"kiss": False, "omc": True}, memory.FULL_BUDGET_MB)],
)
def test_budget_is_fulls_when_a_profile_runs(profiles: dict[str, bool], budget: int):
    assert status.budget_mib(profiles) == budget


def test_problems_skip_pods_of_jobs():
    pod = {
        "metadata": {"namespace": "ns", "name": "config-x", "ownerReferences": [{"kind": "Job", "name": "config"}]},
        "status": {"phase": "Failed", "containerStatuses": [{"name": "app", "ready": False}]},
    }
    assert status.problems([pod], [{"metadata": {"name": "config"}, "status": {}}]) == []


@pytest.fixture
def cluster(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRun:
    """A fake cluster: one zac pod started an hour ago, a tunnel and an /etc/hosts without our line."""
    started = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    pod = {
        "metadata": {"namespace": NAMESPACE, "name": "contact-web-6f6d-r2q96"},
        "status": {
            "phase": "Running",
            "containerStatuses": [{"name": "web", "ready": True, "state": {"running": {"startedAt": started}}}],
        },
    }
    baseline = tmp_path / "memory-baseline.json"
    baseline.write_text(json.dumps({"containers": {"ns/big/app": 100, "ns/small/app": 20}}), encoding="utf-8")
    monkeypatch.setattr(status, "BASELINE", baseline)
    etc_hosts = tmp_path / "hosts"
    etc_hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    monkeypatch.setattr(tunnel, "ETC_HOSTS", etc_hosts)
    monkeypatch.setattr(hosts, "chart_hosts", lambda: ["zac.local"])
    usage = {
        "ns/big/app": status.Usage(300, 0.5),
        "ns/small/app": status.Usage(20, 0.0),
        "ns/fresh/app": status.Usage(10, 0.0),
    }
    monkeypatch.setattr(status, "container_usage", lambda _items: usage)
    fake_run.on("kubectl", "get", "pods", "-A", stdout=json.dumps({"items": [pod]}))
    fake_run.on("kubectl", "get", "jobs", "-A", stdout=json.dumps({"items": []}))
    fake_run.on("docker", "stats", stdout="4GiB / 16GiB|150%")
    fake_run.on("docker", "inspect", "minikube", "--format", "{{.HostConfig.Memory}}", stdout=str(16 * 1024**3))
    fake_run.on("docker", "inspect", "minikube", "--format", "{{.HostConfig.NanoCpus}}", stdout="6000000000\n")
    fake_run.on("docker", "info", stdout=str(tmp_path))
    fake_run.on("pgrep", "-f", tunnel.TUNNEL_PATTERN, stdout="4242\n")
    fake_run.on("kubectl", "get", "svc", stdout="10.96.0.200")
    return fake_run


def test_report_shows_node_profiles_access_and_containers_against_the_baseline(
    cluster: FakeRun, capsys: pytest.CaptureFixture[str]
):
    status.report(top=2)
    out = capsys.readouterr().out
    assert "Node        memory 4.0 / 16.0 GiB cap, budget 17.0 GiB, containers 0.3 GiB" in out
    assert "CPU 1.5 / 6 cores" in out
    assert "Profiles    kiss\n" in out
    assert "youngest container 60 min ago (settled)" in out
    assert "Access      tunnel PID 4242, edge 10.96.0.200; Run podiumd-minikube update-hosts" in out
    assert "Lock        free" in out
    assert "Problems    none" in out
    assert "    300    +200!      0.50  ns/big/app" in out
    assert "     20      +0       0.00  ns/small/app" in out
    assert "ns/fresh/app" not in out
    assert "... 1 more (--all)" in out


def test_report_without_tunnel_cap_or_baseline(
    cluster: FakeRun, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(status, "BASELINE", tmp_path / "missing.json")
    cluster.on("pgrep", "-f", returncode=1)
    cluster.on("docker", "inspect", "minikube", "--format", "{{.HostConfig.Memory}}", stdout="0")
    cluster.on("docker", "inspect", "minikube", "--format", "{{.HostConfig.NanoCpus}}", stdout="0")
    cluster.on("kubectl", "get", "svc", returncode=1)
    status.report(top=0)
    out = capsys.readouterr().out
    assert "/ ? GiB cap" in out
    assert "/ all host cores" in out
    assert "Access      tunnel not running\n" in out
    assert "     10     new       0.00  ns/fresh/app" in out
    assert "more (--all)" not in out
