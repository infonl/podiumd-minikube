"""podiumd_minikube.gateway: the routes generated from the rendered Ingresses."""

from podiumd_minikube import gateway
from podiumd_minikube import manifests


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
    assert services["keycloak"]["spec"]["externalName"] == "keycloak.podiumd-minikube.svc.cluster.local"
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
