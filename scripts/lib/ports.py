"""How host traffic reaches each app: one edge IP, routed by Host header."""

import sys

from collections import Counter
from dataclasses import dataclass
from typing import Any

from lib import kube
from lib.paths import NAMESPACE

TRAFFIC_TYPE = {
    "zac.local": "Web app UI - redirects unauthenticated requests to Keycloak (OIDC)",
    "keycloak.local": "Identity provider - OIDC login/token endpoints + its own admin console",
    "openzaak.local": "ZGW REST API + Django admin",
    "openklant.local": "ZGW REST API + Django admin",
    "pabc.local": "REST API (authorization/role-mapping data ZAC queries)",
    "solr.local": "Search engine - redirects / to its own /solr/ admin UI",
    "objecten.local": "Objects API (REST) + Django admin",
    "objecttypen.local": "Objecttypes API (REST) + Django admin - classic objecten shape only",
    "opennotificaties.local": "Notifications API (REST) + Django admin",
    "openarchiefbeheer-web.local": "SPA (served by the shared nginx sidecar backend)",
    "openarchiefbeheer-ui.local": "Same nginx sidecar as -web.local, second hostname for compose parity",
    "openformulieren-nginx.local": "Forms SPA/API (served by the shared nginx sidecar backend)",
    "openformulieren-web.local": "Same nginx sidecar as -nginx.local, second hostname for compose parity",
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
                found.add(Route(rule.get("host", ""), backend["name"], str(port.get("number") or port.get("name"))))
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
    print("== Host entry point ==")
    if ip:
        print(
            f"Edge (NGINX Gateway Fabric) LoadBalancer IP: {ip} "
            "(reachable while 'minikube tunnel' runs, see scripts/setup-tunnel)"
        )
    else:
        print("The edge has no external IP yet - 'minikube tunnel' isn't running (see scripts/setup-tunnel).")
    print("  port 80  (web)        - HTTP; every hostname below is routed by Host header")
    print("  port 443 (websecure)  - listener without TLS: no Ingress sets spec.tls\n")
    services = {item["metadata"]["name"]: item for item in kube.get_json("svc", "-n", NAMESPACE)["items"]}
    found = routes(kube.get_json("ingress", "-n", NAMESPACE)["items"])
    print(f"{'HOST':<30} {'BACKEND (svc:port -> real port)':<32} TRAFFIC TYPE")
    for route in found:
        backend = f"{route.service}:{route.port} -> {port_number(route, services)}"
        print(f"{route.host:<30} {backend:<32} {TRAFFIC_TYPE.get(route.host, '(no description on file)')}")
    # Two Ingresses for one host (left over after toggling monitoringLogging): two routes claim it.
    duplicates = sorted(host for host, count in Counter(route.host for route in found).items() if count > 1)
    if duplicates:
        print(
            "\nWARNING: hostname(s) claimed by more than one Ingress (the edge may route to either):", file=sys.stderr
        )
        print("\n".join(f"  {host}" for host in duplicates), file=sys.stderr)
