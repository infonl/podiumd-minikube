"""podiumd_minikube.deployed: the deploy record and the running containers per chart."""

import json

from typing import Any

import pytest
import yaml

from conftest import FakeRun

from podiumd_minikube import dependency
from podiumd_minikube import deployed
from podiumd_minikube import manifests
from podiumd_minikube.paths import NAMESPACE

CHARTS = {"Deployment/openzaak": "podiumd", "Prometheus/podiumd-minikube-prometheus": "monitoring-logging"}


def _pod(name: str, owner: dict[str, str] | None, states: list[str], namespace: str = NAMESPACE) -> dict[str, Any]:
    return {
        "metadata": {"namespace": namespace, "name": name, "ownerReferences": [owner] if owner else []},
        "status": {"containerStatuses": [{"state": {state: {}}} for state in states]},
    }


def _owner(kind: str, name: str, api_version: str = "apps/v1") -> dict[str, str]:
    return {"apiVersion": api_version, "kind": kind, "name": name}


@pytest.fixture
def owners(fake_run: FakeRun) -> FakeRun:
    """Live owners: a ReplicaSet of openzaak, and the StatefulSet the Prometheus operator built."""
    replica_sets = [{"metadata": {"name": "openzaak-6f6d", "ownerReferences": [_owner("Deployment", "openzaak")]}}]
    prometheus = _owner("Prometheus", "podiumd-minikube-prometheus", "monitoring.coreos.com/v1")
    stateful_sets = [{"metadata": {"name": "prometheus-podiumd-minikube-prometheus", "ownerReferences": [prometheus]}}]
    fake_run.on("kubectl", "get", "ReplicaSet.apps", stdout=json.dumps({"items": replica_sets}))
    fake_run.on("kubectl", "get", "StatefulSet.apps", stdout=json.dumps({"items": stateful_sets}))
    fake_run.on("kubectl", "get", "Deployment.apps", stdout=json.dumps({"items": [{"metadata": {"name": "mock"}}]}))
    return fake_run


PODS = [
    _pod("openzaak-6f6d-r2q96", _owner("ReplicaSet", "openzaak-6f6d"), ["running", "running"]),
    _pod("prometheus-0", _owner("StatefulSet", "prometheus-podiumd-minikube-prometheus"), ["running", "waiting"]),
    _pod("mock-5b7c-x1y2z", _owner("ReplicaSet", "mock-5b7c"), ["running"]),
    _pod("job-abc", _owner("Job", "openzaak-config", "batch/v1"), ["terminated"]),
    _pod("cilium-x", None, ["running"], namespace="kube-system"),
]


def test_running_containers_follow_owners_to_the_rendered_object(owners: FakeRun):
    owners.on("kubectl", "get", "ReplicaSet.apps", stdout=json.dumps({"items": [
        {"metadata": {"name": "openzaak-6f6d", "ownerReferences": [_owner("Deployment", "openzaak")]}},
        {"metadata": {"name": "mock-5b7c", "ownerReferences": [_owner("Deployment", "mock")]}},
    ]}))  # fmt: skip
    assert deployed.running_containers(PODS, CHARTS) == {"podiumd": 2, "monitoring-logging": 1, "other": 1}
    fetched = [call[2] for call in owners.calls]
    assert fetched == ["ReplicaSet.apps", "StatefulSet.apps", "Deployment.apps"]


def test_selected_names_a_version_or_a_checkout_with_its_commit(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    choices = {"podiumd": dependency.Selection("path", "/src/charts/podiumd"), "monitoringLogging": None}
    monkeypatch.setattr(dependency, "selection", lambda key: choices[key])
    fake_run.on("git", "-C", "/src/charts/podiumd", "rev-parse", "--abbrev-ref", stdout="main\n")
    fake_run.on("git", "-C", "/src/charts/podiumd", "rev-parse", "--short", stdout="abc1234\n")
    fake_run.on("git", "-C", "/src/charts/podiumd", "status", stdout=" M Chart.yaml\n")
    assert deployed.selected("podiumd") == "checkout /src/charts/podiumd (main abc1234, uncommitted changes)"
    assert deployed.selected("monitoringLogging") == "none"
    choices["podiumd"] = dependency.Selection("version", "4.10.0")
    assert deployed.selected("podiumd") == "4.10.0"


def test_record_writes_the_configmap(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(deployed, "selected", lambda key: f"{key} 1.0")
    monkeypatch.setattr(deployed, "checkout", lambda _directory: "main abc1234")
    deployed.record("deploy --full", manifests.Render(docs=[], large_configmaps=[], charts=CHARTS))
    assert fake_run.calls == [["kubectl", "apply", "--server-side", "--force-conflicts", "-n", NAMESPACE, "-f", "-"]]
    doc = yaml.safe_load(fake_run.stdins[0] or "")
    assert doc["metadata"] == {"name": deployed.CONFIGMAP}
    data = doc["data"]
    assert (data["command"], data["podiumd"], data["monitoring-logging"]) == (
        "deploy --full",
        "podiumd 1.0",
        "monitoringLogging 1.0",
    )
    assert data["podiumd-minikube"] == "main abc1234"
    assert json.loads(data["charts"]) == CHARTS


def test_lines_show_the_record_a_changed_selection_and_the_containers(owners: FakeRun, monkeypatch: pytest.MonkeyPatch):
    record = {
        "time": "2026-10-26T08:35:00+00:00", "command": "deploy --full", "podiumd-minikube": "main abc1234",
        "podiumd": "checkout /src/charts/podiumd (main f054876)", "monitoring-logging": "1.0.19",
        "charts": json.dumps(CHARTS),
    }  # fmt: skip
    owners.on("kubectl", "get", f"configmap/{deployed.CONFIGMAP}", stdout=json.dumps({"data": record}))
    monkeypatch.setattr(deployed, "selected", {"podiumd": "4.10.0", "monitoringLogging": "1.0.19"}.get)
    lines = deployed.lines(PODS)
    assert lines[0].startswith("Deployed    2026-10-26 ")
    assert lines[0].endswith(": deploy --full (podiumd-minikube main abc1234)")
    assert lines[1:3] == [
        "            podiumd checkout /src/charts/podiumd (main f054876); selected now: 4.10.0",
        "            monitoring-logging 1.0.19",
    ]
    assert lines[3] == "Running     containers: podiumd 2, monitoring-logging 1, podiumd-minikube 0, other 1"


def test_lines_without_a_record_count_all_running_containers(fake_run: FakeRun):
    fake_run.on("kubectl", "get", f"configmap/{deployed.CONFIGMAP}", returncode=1)
    assert deployed.lines(PODS) == [
        f"Deployed    no configmap/{deployed.CONFIGMAP}: the next deploy writes it",
        "Running     containers: 4",
    ]
