"""NGINX Gateway Fabric, ExternalsPodiumD's edge, built next to Traefik until the switch.

As ExternalsPodiumD (pipelines/includes/haven/gateway-api-*.yml, pipelines/
values/ngf.yml, gateway-api/gateway.yml, dim1 ssl-gateway.yml and
services-gateway.yml): Gateway API CRDs v1.5.1 standard, NGF 2.6.7 in
ingress-basic, Gateway public-gateway with http and https listeners, and per
host an HTTPRoute to an ExternalName Service in ingress-basic that names the
app's Service. No ClientSettingsPolicy, so nginx's 1 MB request-body limit
applies, as in ExternalsPodiumD. The routes come from the rendered Ingresses,
so a host is defined once.
"""

from typing import Any

import yaml

from lib import kube
from lib import manifests
from lib import process
from lib import tls
from lib.paths import NAMESPACE

GATEWAY_API_VERSION = "v1.5.1"
NGF_CHART = "oci://ghcr.io/nginx/charts/nginx-gateway-fabric"
NGF_VERSION = "2.6.7"
NGF_RELEASE = "nginx-gateway-fabric"
EDGE_NAMESPACE = "ingress-basic"
GATEWAY = "public-gateway"
GATEWAY_CLASS = "nginx"
# NGF names a Gateway's data-plane Service <gateway>-<gatewayclass>.
SERVICE = f"{GATEWAY}-{GATEWAY_CLASS}"
LABELS = {"app.kubernetes.io/part-of": "podiumd-minikube", "app.kubernetes.io/component": "gateway"}
_SELECTOR = ",".join(f"{key}={value}" for key, value in LABELS.items())


def ngf_values(dns_ip: str) -> dict[str, Any]:
    """ExternalsPodiumD's ngf.yml without its AKS parts (ACR images, nodeSelector, fixed IP, client-IP rewrite)."""
    return {
        "nginxGateway": {
            "gatewayClassName": GATEWAY_CLASS,
            "gatewayControllerName": "gateway.nginx.org/nginx-gateway-controller",
            "replicas": 1,
            "snippets": {"enable": False},
        },
        "nginx": {
            "replicas": 1,
            # Needed for ExternalName backends.
            "config": {
                "dnsResolver": {
                    "addresses": [{"type": "IPAddress", "value": dns_ip}],
                    "timeout": "5s",
                    "cacheTTL": "30s",
                    "disableIPv6": False,
                },
            },
            "service": {"type": "LoadBalancer", "externalTrafficPolicy": "Cluster"},
        },
    }


def install() -> None:
    """Installs the Gateway API CRDs and NGF unless NGF is there."""
    if kube.exists(f"deployment/{NGF_RELEASE}", EDGE_NAMESPACE):
        print(f"NGINX Gateway Fabric already installed in namespace '{EDGE_NAMESPACE}' - skipping.")
        return
    print(f"Installing the Gateway API CRDs {GATEWAY_API_VERSION} and NGINX Gateway Fabric {NGF_VERSION}...")
    crds = (
        f"https://github.com/kubernetes-sigs/gateway-api/releases/download/{GATEWAY_API_VERSION}/standard-install.yaml"
    )
    kube.kubectl_shown("apply", "-f", crds)
    dns_ip = kube.kubectl("get", "svc", "kube-dns", "-n", "kube-system", "-o", "jsonpath={.spec.clusterIP}")
    process.run(
        ["helm", "upgrade", "--install", NGF_RELEASE, NGF_CHART, "--version", NGF_VERSION,
         "-n", EDGE_NAMESPACE, "--create-namespace", "--wait", "-f", "-"],
        stdin=yaml.safe_dump(ngf_values(dns_ip)),
        capture=False,
    )  # fmt: skip


def gateway_manifest() -> dict[str, Any]:
    """ExternalsPodiumD's public-gateway, without its Azure load balancer annotations."""
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "Gateway",
        "metadata": {"name": GATEWAY, "namespace": EDGE_NAMESPACE, "labels": LABELS},
        "spec": {
            "gatewayClassName": GATEWAY_CLASS,
            "listeners": [
                {"name": "http", "protocol": "HTTP", "port": 80, "allowedRoutes": {"namespaces": {"from": "Same"}}},
                {
                    "name": "https",
                    "protocol": "HTTPS",
                    "port": 443,
                    "tls": {"mode": "Terminate", "certificateRefs": [{"kind": "Secret", "name": tls.CERTIFICATE}]},
                    "allowedRoutes": {"namespaces": {"from": "Same"}},
                },
            ],
        },
    }


def _service_port(docs: list[manifests.Doc], service: str, port: Any) -> int:
    """The number of a backend port, which may be a Service port name."""
    if isinstance(port, int):
        return port
    for doc in docs:
        if doc.get("kind") == "Service" and manifests.name_of(doc) == service:
            entries: list[manifests.Doc] = manifests.section(doc, "spec").get("ports") or []
            for entry in entries:
                if entry.get("name") == port:
                    return int(entry["port"])
    msg = f"Ingress backend {service}:{port}: no such Service port in the render"
    raise process.UserError(msg)


def route_manifests(docs: list[manifests.Doc]) -> list[dict[str, Any]]:
    """Per Ingress host an HTTPRoute in ExternalsPodiumD's shape, plus the ExternalName Services they target."""
    services: dict[str, dict[str, Any]] = {}
    routes: list[dict[str, Any]] = []
    for doc in docs:
        if doc.get("kind") != "Ingress":
            continue
        rules: list[manifests.Doc] = manifests.section(doc, "spec").get("rules") or []
        for rule in rules:
            host = str(rule["host"])
            paths: list[manifests.Doc] = manifests.section(rule, "http").get("paths") or []
            backend: manifests.Doc = manifests.section(manifests.section(paths[0], "backend"), "service")
            ports = manifests.section(backend, "port")
            port = _service_port(docs, str(backend["name"]), ports.get("number", ports.get("name")))
            services[backend["name"]] = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": backend["name"], "namespace": EDGE_NAMESPACE, "labels": LABELS},
                "spec": {
                    "type": "ExternalName",
                    "externalName": f"{backend['name']}.{NAMESPACE}.svc.cluster.local",
                    "ports": [{"port": port, "targetPort": port}],
                },
            }
            headers = [
                {"name": "X-Forwarded-Host", "value": host},
                {"name": "X-Forwarded-Proto", "value": "https"},
                {"name": "X-Forwarded-Port", "value": "443"},
            ]
            routes.append({
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "HTTPRoute",
                "metadata": {
                    "name": f"hr-{host.removesuffix('.local')}", "namespace": EDGE_NAMESPACE, "labels": LABELS,
                },
                "spec": {
                    "parentRefs": [{"name": GATEWAY, "namespace": EDGE_NAMESPACE}],
                    "hostnames": [host],
                    "rules": [{
                        "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                        "filters": [
                            {"type": "URLRewrite", "urlRewrite": {"hostname": host}},
                            {"type": "RequestHeaderModifier", "requestHeaderModifier": {"set": headers}},
                        ],
                        "backendRefs": [{"name": backend["name"], "port": port}],
                    }],
                },
            })  # fmt: skip
    return [*services.values(), *routes]


def apply(docs: list[manifests.Doc], hosts: list[str]) -> None:
    """Applies the certificate, Gateway and routes when NGF is installed; deletes routes no longer rendered."""
    if not kube.exists(f"deployment/{NGF_RELEASE}", EDGE_NAMESPACE):
        return
    print("\nApplying the NGINX Gateway Fabric Gateway and routes (see scripts/lib/gateway.py)...")
    objects = [tls.certificate(EDGE_NAMESPACE, hosts), gateway_manifest(), *route_manifests(docs)]
    kube.kubectl("apply", "-f", "-", stdin=manifests.dump(objects))
    wanted = {(doc["kind"], manifests.name_of(doc)) for doc in objects}
    for kind in ("HTTPRoute", "Service"):
        live = kube.get_json(kind.lower(), "-n", EDGE_NAMESPACE, "-l", _SELECTOR)["items"]
        for item in live:
            if (kind, item["metadata"]["name"]) not in wanted:
                kube.kubectl("delete", kind.lower(), item["metadata"]["name"], "-n", EDGE_NAMESPACE)
                print(f"  deleted {kind} {item['metadata']['name']} (no longer rendered)")
    kube.kubectl_shown(
        "wait", "--for=condition=Ready", f"certificate/{tls.CERTIFICATE}", "-n", EDGE_NAMESPACE, "--timeout=120s"
    )
    ip = kube.kubectl(
        "get", "svc", SERVICE, "-n", EDGE_NAMESPACE, "-o", "jsonpath={.status.loadBalancer.ingress[0].ip}"
    )  # fmt: skip
    print(f"Gateway Service {EDGE_NAMESPACE}/{SERVICE}: external IP {ip or '<none - is minikube tunnel running?>'}")
