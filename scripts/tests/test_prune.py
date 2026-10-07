"""lib.prune: which live resources count as orphaned, and the large-prune guard."""

import json

import pytest

from conftest import FakeRun

from lib import prune
from lib.process import UserError


def _items(*names: str, owned: bool = False) -> str:
    metadata = [{"name": name, **({"ownerReferences": [{"kind": "Prometheus"}]} if owned else {})} for name in names]
    return json.dumps({"items": [{"metadata": m} for m in metadata]})


def test_orphans_skip_desired_owned_and_unknown_kinds(fake_run: FakeRun):
    fake_run.on("kubectl", "get", "Deployment", stdout=_items("zac", "old-grafana"))
    fake_run.on("kubectl", "get", "StatefulSet", stdout=_items("prometheus-x", owned=True))
    fake_run.on(
        "kubectl",
        "get",
        "Prometheus",
        returncode=1,
        stderr='error: the server doesn\'t have a resource type "Prometheus"',
    )
    for kind in ("DaemonSet", "PrometheusRule", "ServiceMonitor", "PodMonitor", "Service", "Secret", "Ingress"):
        fake_run.on("kubectl", "get", kind, stdout=_items())
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
