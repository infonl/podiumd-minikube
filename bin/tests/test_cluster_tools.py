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


def test_serving_pod_skips_terminating_and_unready_pods():
    def pod(name: str, *, ready: bool = True, deleting: bool = False) -> dict[str, object]:
        metadata = {"name": name, **({"deletionTimestamp": "2026-01-01T00:00:00Z"} if deleting else {})}
        status = {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}
        return {"metadata": metadata, "status": status}

    assert kube.serving_pod([pod("old", deleting=True), pod("starting", ready=False), pod("new")]) == "new"
    assert kube.serving_pod([pod("old", deleting=True)]) == ""


def test_route_args_cover_the_service_network_via_the_node():
    assert tunnel.route_args("replace", "192.168.49.2") == [
        "sudo", "ip", "route", "replace", "10.96.0.0/12", "via", "192.168.49.2",
    ]  # fmt: skip


def test_replace_hosts_line_is_unchanged_when_the_line_is_current():
    line = "10.0.0.1 zac.local  # podiumd-minikube"
    text = f"127.0.0.1 localhost\n{line}\n"
    assert tunnel.replace_hosts_line(text, line) == text


STATIC_POD = """apiVersion: v1
kind: Pod
metadata:
  name: etcd
spec:
  containers:
  - command:
    - etcd
    name: etcd
"""


def test_with_control_plane_env_adds_gogc_once():
    tuned = provision.with_control_plane_env(STATIC_POD)
    assert tuned is not None and "GOGC" in tuned and "- etcd" in tuned
    assert provision.with_control_plane_env(tuned) is None


def test_tune_control_plane_leaves_tuned_static_pods_alone(fake_run: FakeRun):
    fake_run.on("minikube", "ssh", stdout=provision.with_control_plane_env(STATIC_POD) or "")
    provision.tune_control_plane()
    assert not fake_run.ran("minikube", "cp")


def test_cpuset_args_pin_the_node_to_its_first_cpus_only_when_the_host_has_more():
    assert provision.cpuset_args(6, 24) == ["--cpuset-cpus=0-5"]
    assert not provision.cpuset_args(6, 6)
