"""How host traffic reaches each app: one edge IP, routed by Host header."""

import sys

from collections import Counter
from dataclasses import dataclass
from typing import Any

from podiumd_minikube import kube
from podiumd_minikube.paths import NAMESPACE

TRAFFIC_TYPE = {
    "zac.local": "Web app UI - redirects unauthenticated requests to Keycloak (OIDC)",
    "keycloak.local": "Identity provider - OIDC login/token endpoints",
    "keycloak-admin.local": "Same Keycloak: its admin console (KC_HOSTNAME_ADMIN), ITA's OIDC authority",
    "openzaak.local": "ZGW REST API + Django admin",
    "openklant.local": "ZGW REST API + Django admin",
    "pabc.local": "REST API (authorization/role-mapping data ZAC queries)",
    "objecten.local": "Objects API (REST) + Django admin",
    "objecttypen.local": "Objecttypes API (REST) + Django admin; Open Object's on podiumd >= 4.10",
    "opennotificaties.local": "Notifications API (REST) + Django admin",
    "openarchiefbeheer-ui.local": "SPA and backend through the nginx sidecar",
    "openformulieren-nginx.local": "Forms SPA/API (served by the shared nginx sidecar backend)",
    "grafana.local": "Metrics dashboard UI (own auth)",
    "mailpit.local": "SMTP test server's own web UI - no auth",
}


@dataclass(frozen=True)
class Route:
    """One Ingress host -> Service:port."""

    host: str
    service: str
    port: str


def routes(ingresses: list[Any]) -> list[Route]:
    """Sorted unique routes of the Ingress items (port is a number or a port name)."""
    found: set[Route] = set()
    for ingress in ingresses:
        for rule in ingress["spec"].get("rules", []):
            for path in rule.get("http", {}).get("paths", []):
                backend = path["backend"]["service"]
                port = backend["port"]
                number = str(port.get("number") or port.get("name"))
                found.add(Route(rule.get("host", ""), backend["name"], number))
    return sorted(found, key=lambda route: (route.host, route.service, route.port))


def port_number(route: Route, services: dict[str, Any]) -> str:
    """route's numeric port; a port name is looked up in the Service."""
    if route.port.isdigit():
        return route.port
    for port in services.get(route.service, {}).get("spec", {}).get("ports", []):
        if port.get("name") == route.port:
            return str(port["port"])
    return "?"


def show_port_mappings() -> None:
    """Prints the edge's entry point and every live Ingress host's backend (the routes follow the Ingresses)."""
    kube.require_minikube_context()
    ip = kube.edge_ip()
    edge = (
        f"Edge (NGINX Gateway Fabric) LoadBalancer IP: {ip} "
        "(reachable while 'minikube tunnel' runs, see podiumd-minikube setup-tunnel)"
        if ip
        else "The edge has no external IP yet - 'minikube tunnel' isn't running (see podiumd-minikube setup-tunnel)."
    )
    print(
        "== Host entry point ==",
        edge,
        "  port 80  (web)        - HTTP; every hostname below is routed by Host header",
        "  port 443 (websecure)  - listener without TLS: no Ingress sets spec.tls\n",
        sep="\n",
    )
    services = {item["metadata"]["name"]: item for item in kube.get_json("svc", "-n", NAMESPACE)["items"]}
    found = routes(kube.get_json("ingress", "-n", NAMESPACE)["items"])
    print(f"{'HOST':<30} {'BACKEND (svc:port -> real port)':<32} TRAFFIC TYPE")
    for route in found:
        backend = f"{route.service}:{route.port} -> {port_number(route, services)}"
        print(f"{route.host:<30} {backend:<32} {TRAFFIC_TYPE.get(route.host, '(no description on file)')}")
    # Two Ingresses for one host (a leftover of an earlier render): two routes claim it.
    counts = Counter(route.host for route in found)
    duplicates = sorted(host for host, count in counts.items() if count > 1)
    if duplicates:
        print(
            "\nWARNING: hostname(s) claimed by more than one Ingress (the edge may route to either):", file=sys.stderr
        )
        print("\n".join(f"  {host}" for host in duplicates), file=sys.stderr)
