"""podiumd_minikube.hosts: the chart's Ingress hosts."""

import pytest

from conftest import FakeRun

from podiumd_minikube import chart
from podiumd_minikube import hosts
from podiumd_minikube import manifests


def _ingress(*hostnames: str) -> dict[str, object]:
    rules = [{"host": host} for host in hostnames]
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {"name": "i"},
        "spec": {"rules": rules},
    }


def test_chart_hosts_renders_every_profile(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(chart, "everything_args", lambda: ["--set", "all=true"])
    fake_run.on("helm", "template", stdout=manifests.dump([_ingress("zac.local", "kiss.local"), _ingress("zac.local")]))
    assert hosts.chart_hosts() == ["kiss.local", "zac.local"]
    assert fake_run.calls[0][-2:] == ["--set", "all=true"]


def test_hosts_line_ends_with_the_marker():
    assert hosts.hosts_line("10.0.0.1", ["a.local", "b.local"]) == f"10.0.0.1 a.local b.local  {hosts.MARKER}"
