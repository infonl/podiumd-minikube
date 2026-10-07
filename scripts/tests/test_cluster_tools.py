"""Context guard, /etc/hosts, port mappings, teardown, provision and polling helpers."""

import pytest

from conftest import FakeRun

from lib import hosts
from lib import kube
from lib import polling
from lib import ports
from lib import provision
from lib import teardown
from lib import tunnel
from lib.process import UserError


def test_require_minikube_context_refuses_another_context(fake_run: FakeRun):
    fake_run.on("kubectl", "config", "current-context", stdout="podiumd-aks\n")
    with pytest.raises(UserError, match="'podiumd-aks', not 'minikube'"):
        kube.require_minikube_context()
    fake_run.on("kubectl", "config", "current-context", stdout="minikube\n")
    kube.require_minikube_context()


def test_replace_hosts_line_drops_earlier_lines_of_this_project():
    text = "127.0.0.1 localhost\n10.0.0.1 zac.local keycloak.local\n"
    line = hosts.hosts_line("10.0.0.2", ["zac.local", "pabc.local"])
    assert tunnel.replace_hosts_line(text, line) == f"127.0.0.1 localhost\n{line}\n"
    assert line == f"10.0.0.2 zac.local pabc.local  {hosts.MARKER}"


def test_routes_resolve_named_ports_through_the_service():
    ingress = {
        "spec": {
            "rules": [
                {
                    "host": "zac.local",
                    "http": {"paths": [{"backend": {"service": {"name": "zac", "port": {"name": "http"}}}}]},
                },
                {
                    "host": "pabc.local",
                    "http": {"paths": [{"backend": {"service": {"name": "pabc", "port": {"number": 80}}}}]},
                },
            ]
        }
    }
    services = {"zac": {"spec": {"ports": [{"name": "http", "port": 8080}]}}}
    found = ports.routes([ingress, ingress])
    assert [(route.host, ports.port_number(route, services)) for route in found] == [
        ("pabc.local", "80"),
        ("zac.local", "8080"),
    ]


def test_bound_volumes_selects_by_claim_namespace():
    volumes = [
        {"metadata": {"name": "mine"}, "spec": {"claimRef": {"namespace": "podiumd-minikube"}}},
        {"metadata": {"name": "other"}, "spec": {"claimRef": {"namespace": "kube-system"}}},
        {"metadata": {"name": "unbound"}, "spec": {}},
    ]
    assert teardown.bound_volumes(volumes) == ["mine"]


def test_confirmed_aborts_unless_yes_is_typed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert not teardown.confirmed("summary", yes=False)
    assert teardown.confirmed("summary", yes=True)


def test_missing_images_match_by_suffix():
    loaded = ["docker.io/library/busybox:1.38.0", "registry/zac:5"]
    assert provision.missing_images(["busybox:1.38.0", "zac:6"], loaded) == ["zac:6"]


def test_wait_until_returns_the_first_truthy_result_or_none():
    answers = iter(["", "", "10.0.0.1"])
    assert polling.wait_until(lambda: next(answers), timeout=5, interval=0) == "10.0.0.1"
    assert polling.wait_until(lambda: None, timeout=0, interval=0) is None
