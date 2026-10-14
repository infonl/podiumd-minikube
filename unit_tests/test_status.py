"""podiumd_minikube.status: the pure parts of the cluster status."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta

from podiumd_minikube import status


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
    assert status._cores(first, {"attributes": {"id": "x"}}) == 0.0
    assert status._cores(first, None) == 0.0
