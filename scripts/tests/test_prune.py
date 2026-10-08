"""lib.prune: which live resources count as orphaned, and the large-prune guard."""

import json

import pytest

from conftest import FakeRun

from lib import prune
from lib.process import UserError


def _items(*names: str, owned: bool = False, labels: dict[str, str] | None = None) -> str:
    labels = {"app.kubernetes.io/managed-by": "Helm"} if labels is None else labels
    owner = {"ownerReferences": [{"kind": "Prometheus"}]} if owned else {}
    return json.dumps({"items": [{"metadata": {"name": name, "labels": labels, **owner}} for name in names]})


def test_orphans_skip_desired_owned_and_unknown_kinds(fake_run: FakeRun):
    fake_run.on("kubectl", "get", stdout=_items())
    fake_run.on("kubectl", "get", "Deployment", stdout=_items("zac", "old-grafana"))
    fake_run.on("kubectl", "get", "StatefulSet", stdout=_items("prometheus-x", owned=True))
    fake_run.on(
        "kubectl",
        "get",
        "Prometheus",
        returncode=1,
        stderr='error: the server doesn\'t have a resource type "Prometheus"',
    )
    desired = [{"kind": "Deployment", "metadata": {"name": "zac"}}]
    assert prune.orphans(desired) == [("Deployment", "old-grafana")]


def test_prune_refuses_a_large_prune_without_force(fake_run: FakeRun):
    names = [f"d{index}" for index in range(prune.LARGE_PRUNE_THRESHOLD + 1)]
    fake_run.on("kubectl", "get", stdout=_items())
    fake_run.on("kubectl", "get", "Deployment", stdout=_items(*names))
    with pytest.raises(UserError, match="refusing to prune 11"):
        prune.prune([], force=False)
    assert fake_run.ran("kubectl", "delete") == []
    prune.prune([], force=True)
    assert len(fake_run.ran("kubectl", "delete")) == len(names)


def test_orphans_never_include_objects_without_this_charts_labels(fake_run: FakeRun):
    fake_run.on("kubectl", "get", stdout=_items())
    fake_run.on(
        "kubectl", "get", "Secret",
        stdout=json.dumps({"items": [
            json.loads(_items("podiumd-tests-credentials", labels={"app.kubernetes.io/managed-by": "podiumd-tests"}))["items"][0],
            json.loads(_items("ptest-unlabelled", labels={}))["items"][0],
            json.loads(_items("old-chart-secret", labels={"app.kubernetes.io/instance": "podiumd-minikube"}))["items"][0],
        ]}),
    )  # fmt: skip
    assert prune.orphans([]) == [("Secret", "old-chart-secret")]


def test_orphans_keep_the_ca_configmap_the_scripts_create(fake_run: FakeRun):
    fake_run.on("kubectl", "get", stdout=_items())
    fake_run.on("kubectl", "get", "ConfigMap", stdout=_items("podiumd-ca", "clamav-clamd"))
    assert prune.orphans([]) == [("ConfigMap", "clamav-clamd")]
