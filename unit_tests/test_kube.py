"""podiumd_minikube.kube: the edge IP and waiting for a serving pod."""

import json

from collections.abc import Callable

import pytest

from conftest import FakeRun

from podiumd_minikube import kube
from podiumd_minikube import polling
from podiumd_minikube.process import UserError


@pytest.mark.parametrize(("returncode", "ip"), [(0, "10.96.0.200"), (1, "")])
def test_edge_ip_is_empty_without_the_service(fake_run: FakeRun, returncode: int, ip: str):
    fake_run.on("kubectl", "get", "svc", returncode=returncode, stdout="10.96.0.200\n")
    assert kube.edge_ip() == ip
    assert fake_run.calls[0][-1] == "jsonpath={.status.loadBalancer.ingress[0].ip}"


@pytest.mark.parametrize(("ready", "name"), [("True", "keycloak-0"), ("False", "")])
def test_first_pod_waits_for_a_ready_pod(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, ready: str, name: str):
    timeouts: list[float] = []

    def once(probe: Callable[[], str], *, timeout: float, interval: float) -> str | None:
        timeouts.append(timeout)
        return probe() or None

    monkeypatch.setattr(polling, "wait_until", once)
    pod = {"metadata": {"name": "keycloak-0"}, "status": {"conditions": [{"type": "Ready", "status": ready}]}}
    fake_run.on("kubectl", "get", "pod", stdout=json.dumps({"items": [pod]}))
    if name:
        assert kube.first_pod("app=keycloak") == name
    else:
        with pytest.raises(UserError, match="no Ready pod with labels app=keycloak in namespace podiumd"):
            kube.first_pod("app=keycloak")
    assert timeouts == [kube.POD_TIMEOUT]
    assert fake_run.calls[0][:7] == ["kubectl", "get", "pod", "-n", "podiumd", "-l", "app=keycloak"]
