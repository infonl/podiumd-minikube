"""podiumd_minikube.ports: the edge's entry point and the live Ingress routes."""

import json

import pytest

from conftest import FakeRun

from podiumd_minikube import ports


def _ingress(host: str, service: str, port: dict[str, object]) -> dict[str, object]:
    backend = {"service": {"name": service, "port": port}}
    return {"spec": {"rules": [{"host": host, "http": {"paths": [{"backend": backend}]}}]}}


def test_port_number_is_unknown_without_a_matching_service_port():
    route = ports.Route("zac.local", "zac", "http")
    assert ports.port_number(route, {}) == "?"
    assert ports.port_number(route, {"zac": {"spec": {"ports": [{"name": "metrics", "port": 9990}]}}}) == "?"


@pytest.mark.parametrize(("edge", "entry"), [(0, "LoadBalancer IP: 10.96.0.200 "), (1, "has no external IP yet")])
def test_show_port_mappings_lists_routes_and_warns_about_hosts_claimed_twice(
    fake_run: FakeRun, capsys: pytest.CaptureFixture[str], edge: int, entry: str
):
    services = {"items": [{"metadata": {"name": "keycloak"}, "spec": {"ports": [{"name": "http", "port": 8080}]}}]}
    ingresses = {
        "items": [
            _ingress("zac.local", "zac", {"number": 80}),
            _ingress("zac.local", "zac-old", {"number": 80}),
            _ingress("keycloak.local", "keycloak", {"name": "http"}),
            _ingress("extra.local", "extra", {"number": 8000}),
        ]
    }
    fake_run.on("kubectl", "config", "current-context", stdout="minikube")
    fake_run.on("kubectl", "get", "svc", "public-gateway-nginx", returncode=edge, stdout="10.96.0.200")
    fake_run.on("kubectl", "get", "svc", "-n", "podiumd-minikube", stdout=json.dumps(services))
    fake_run.on("kubectl", "get", "ingress", stdout=json.dumps(ingresses))
    ports.show_port_mappings()
    out, err = capsys.readouterr()
    assert entry in out
    assert f"{'keycloak.local':<30} {'keycloak:http -> 8080':<32} {ports.TRAFFIC_TYPE['keycloak.local']}" in out
    assert f"{'extra.local':<30} {'extra:8000 -> 8000':<32} (no description on file)" in out
    assert err.endswith("more than one Ingress (the edge may route to either):\n  zac.local\n")
