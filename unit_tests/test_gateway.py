"""podiumd_minikube.gateway: installing NGF and applying the Gateway and the routes from the rendered Ingresses."""

import json

from collections.abc import Callable

import pytest
import yaml

from conftest import FakeRun

from podiumd_minikube import gateway
from podiumd_minikube import manifests
from podiumd_minikube import polling
from podiumd_minikube.paths import EDGE_NAMESPACE
from podiumd_minikube.paths import EDGE_SERVICE
from podiumd_minikube.process import UserError


def _ingress(
    host: str, service: str, port: dict[str, object], annotations: dict[str, str] | None = None
) -> manifests.Doc:
    backend = {"service": {"name": service, "port": port}}
    return {
        "kind": "Ingress",
        "metadata": {"name": host, "annotations": annotations},
        "spec": {"rules": [{"host": host, "http": {"paths": [{"backend": backend}]}}]},
    }


def test_routes_follow_externalspodiumd_and_resolve_named_ports():
    keycloak_service: manifests.Doc = {
        "kind": "Service",
        "metadata": {"name": "keycloak"},
        "spec": {"ports": [{"name": "http", "port": 8080}]},
    }
    docs = [
        keycloak_service,
        _ingress("keycloak.local", "keycloak", {"name": "http"}),
        _ingress("openarchiefbeheer-web.local", "openarchiefbeheer-nginx", {"number": 80}),
        _ingress("openarchiefbeheer-ui.local", "openarchiefbeheer-nginx", {"number": 80}),
    ]
    objects = gateway.route_manifests(docs)
    services = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Service"}
    routes = {o["metadata"]["name"]: o for o in objects if o["kind"] == "HTTPRoute"}
    assert set(services) == {"keycloak", "openarchiefbeheer-nginx"}
    assert services["keycloak"]["spec"]["externalName"] == "keycloak.podiumd.svc.cluster.local"
    assert set(routes) == {"hr-keycloak", "hr-openarchiefbeheer-web", "hr-openarchiefbeheer-ui"}
    rule = routes["hr-keycloak"]["spec"]["rules"][0]
    assert rule["backendRefs"] == [{"name": "keycloak", "port": 8080}]
    assert rule["filters"][0] == {"type": "URLRewrite", "urlRewrite": {"hostname": "keycloak.local"}}


def test_forwarded_host_annotation_replaces_the_host_sent_upstream():
    annotations = {gateway.FORWARDED_HOST_ANNOTATION: "keycloak-admin.local"}
    docs = [_ingress("keycloak.local", "keycloak", {"number": 8080}, annotations)]
    route = next(o for o in gateway.route_manifests(docs) if o["kind"] == "HTTPRoute")
    assert route["spec"]["hostnames"] == ["keycloak.local"]
    rewrite, headers = route["spec"]["rules"][0]["filters"]
    assert rewrite["urlRewrite"]["hostname"] == "keycloak-admin.local"
    assert {"name": "X-Forwarded-Host", "value": "keycloak-admin.local"} in headers["requestHeaderModifier"]["set"]


def test_install_crds_applies_the_gateway_api_release(fake_run: FakeRun):
    gateway.install_crds()
    assert fake_run.ran("kubectl", "apply", "-f")[0][-1].endswith("/v1.5.1/standard-install.yaml")


def test_install_applies_ngfs_crds_then_upgrades_ngf_with_the_cluster_dns(fake_run: FakeRun):
    fake_run.on("helm", "show", "crds", stdout="kind: CustomResourceDefinition\n")
    fake_run.on("kubectl", "get", "svc", "kube-dns", stdout="10.96.0.10")
    gateway.install()
    server_side = fake_run.calls.index(["kubectl", "apply", "--server-side", "--force-conflicts", "-f", "-"])
    assert fake_run.stdins[server_side] == "kind: CustomResourceDefinition\n"
    upgrade = fake_run.calls.index(fake_run.ran("helm", "upgrade")[0])
    assert fake_run.calls[upgrade][3:5] == [gateway.NGF_RELEASE, gateway.NGF_CHART]
    values = yaml.safe_load(fake_run.stdins[upgrade] or "")
    assert values["nginx"]["config"]["dnsResolver"]["addresses"] == [{"type": "IPAddress", "value": "10.96.0.10"}]
    assert values["nginx"]["service"]["patches"][0]["value"]["spec"]["clusterIP"] == gateway.EDGE_CLUSTER_IP


def test_route_to_an_unknown_port_name_is_an_error():
    with pytest.raises(UserError, match="Ingress backend zac:http: no such Service port"):
        gateway.route_manifests([_ingress("zac.local", "zac", {"name": "http"})])


CLUSTER_IP = ("kubectl", "get", "svc", EDGE_SERVICE, "-n", EDGE_NAMESPACE, "-o", "jsonpath={.spec.clusterIP}")
EXTERNAL_IP = (
    "kubectl", "get", "svc", EDGE_SERVICE, "-n", EDGE_NAMESPACE, "-o", "jsonpath={.status.loadBalancer.ingress[0].ip}",
)  # fmt: skip


@pytest.fixture
def edge(fake_run: FakeRun, poll_once: None, monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """NGF installed with the pinned ClusterIP; each wait probes once; only a stale route is live."""
    fake_run.on(*CLUSTER_IP, stdout=gateway.EDGE_CLUSTER_IP)
    fake_run.on(*EXTERNAL_IP, stdout="10.96.0.200")
    for kind in ("service", "proxysettingspolicy", "clientsettingspolicy"):
        fake_run.on("kubectl", "get", kind, "-n", EDGE_NAMESPACE, stdout='{"items": []}')
    live = {"items": [{"metadata": {"name": "hr-zac"}}, {"metadata": {"name": "hr-old"}}]}
    fake_run.on("kubectl", "get", "httproute", "-n", EDGE_NAMESPACE, stdout=json.dumps(live))
    return fake_run


def test_apply_applies_the_edge_and_deletes_routes_no_longer_rendered(
    edge: FakeRun, capsys: pytest.CaptureFixture[str]
):
    gateway.apply([_ingress("zac.local", "zac", {"number": 8080})], ["zac.local"])
    applied = list(yaml.safe_load_all(edge.stdins[edge.calls.index(["kubectl", "apply", "-f", "-"])] or ""))
    assert [doc["kind"] for doc in applied] == [
        "Certificate", "Gateway", "ProxySettingsPolicy", "ClientSettingsPolicy", "Service", "HTTPRoute",
    ]  # fmt: skip
    assert applied[0]["spec"]["dnsNames"] == ["zac.local"]
    assert edge.ran("kubectl", "delete") == [["kubectl", "delete", "httproute", "hr-old", "-n", EDGE_NAMESPACE]]
    assert edge.ran("kubectl", "wait")
    out = capsys.readouterr().out
    assert "deleted HTTPRoute hr-old (no longer rendered)" in out
    assert f"{EDGE_NAMESPACE}/{EDGE_SERVICE}: external IP 10.96.0.200" in out


def test_apply_recreates_an_edge_service_with_another_cluster_ip(edge: FakeRun, monkeypatch: pytest.MonkeyPatch):
    edge.on(*CLUSTER_IP, stdout="10.96.12.34")
    edge.on("kubectl", "delete", "svc", EDGE_SERVICE)
    waits: list[str] = []

    def recreated(probe: Callable[[], object], **_: float) -> object:
        waits.append("wait")
        if len(waits) == 2:
            edge.on(*CLUSTER_IP, stdout=gateway.EDGE_CLUSTER_IP)
        return probe()

    monkeypatch.setattr(polling, "wait_until", recreated)
    gateway.apply([], [])
    assert ["kubectl", "delete", "svc", EDGE_SERVICE, "-n", EDGE_NAMESPACE] in edge.calls
    assert len(waits) == 2


@pytest.mark.parametrize(
    ("absent", "message"),
    [
        (("kubectl", "get", f"deployment/{gateway.NGF_RELEASE}"), "NGINX Gateway Fabric is not in namespace"),
        (("kubectl", "get", f"service/{EDGE_SERVICE}"), "NGF created no Service"),
        (CLUSTER_IP, "did not give public-gateway-nginx ClusterIP 10.96.0.200"),
    ],
)
def test_apply_stops_when_the_edge_does_not_come_up(edge: FakeRun, absent: tuple[str, ...], message: str):
    if absent == CLUSTER_IP:
        edge.on(*CLUSTER_IP, stdout="10.96.12.34")
    else:
        edge.on(*absent, returncode=1)
    with pytest.raises(UserError, match=message):
        gateway.apply([], [])
