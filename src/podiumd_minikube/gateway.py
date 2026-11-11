"""NGINX Gateway Fabric, the edge, as ExternalsPodiumD runs it.

As ExternalsPodiumD (pipelines/includes/haven/gateway-api-*.yml, pipelines/
values/ngf.yml, gateway-api/gateway.yml, dim1 ssl-gateway.yml and
services-gateway.yml): Gateway API CRDs v1.5.1 standard, NGF 2.6.7 and its
CRDs in ingress-basic, Gateway public-gateway with http and https listeners, and per
host an HTTPRoute to an ExternalName Service in ingress-basic that names the
app's Service. Its ProxySettingsPolicy and ClientSettingsPolicy exist only
live (dimp test and ontw, read 2026-10-13), not in its git. The routes come from the rendered Ingresses, so a
host is defined once.
"""

from podiumd_minikube import cni
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import polling
from podiumd_minikube import process
from podiumd_minikube import tls
from podiumd_minikube.objects import Doc
from podiumd_minikube.objects import serialize_yaml
from podiumd_minikube.paths import EDGE_NAMESPACE
from podiumd_minikube.paths import NAMESPACE

GATEWAY_API_VERSION = "v1.5.1"
NGF_CHART = "oci://ghcr.io/nginx/charts/nginx-gateway-fabric"
NGF_VERSION = "2.6.7"
NGF_RELEASE = "nginx-gateway-fabric"
NGF_CHART_ARGS = [NGF_RELEASE, NGF_CHART, "--version", NGF_VERSION]
GATEWAY = "public-gateway"
GATEWAY_CLASS = "nginx"
GATEWAY_REF = {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": GATEWAY}
# NGF's data-plane Service for public-gateway; the WAF (EDGE_SERVICE) proxies to it.
NGF_SERVICE = f"{GATEWAY}-nginx"
LABELS = {"app.kubernetes.io/part-of": "podiumd-minikube", "app.kubernetes.io/component": "gateway"}
_SELECTOR = ",".join(f"{key}={value}" for key, value in LABELS.items())
# minikube tunnel exposes a LoadBalancer Service at its ClusterIP, which Kubernetes picks
# at random per Service; fixed, the /etc/hosts line survives a rebuild. In the static band
# (the first 256 addresses of 10.96.0.0/12) that the allocator leaves to fixed ClusterIPs.
# The WAF's Service holds it.
EDGE_CLUSTER_IP = "10.96.0.200"
# On an Ingress: the host its routes send to the backend instead of their own.
FORWARDED_HOST_ANNOTATION = "podiumd-minikube/forwarded-host"
# ExternalsPodiumD's ngf.yml, for the controller and nginx alike.
NGF_RESOURCES = {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"cpu": "200m", "memory": "256Mi"}}


def ngf_values(dns_ip: str) -> Doc:
    """ExternalsPodiumD's ngf.yml without its AKS parts (ACR images, nodeSelector, fixed internal IP).

    Behind the WAF, as behind ExternalsPodiumD's Application Gateway: NGF takes the client address from
    X-Forwarded-For set by the pods' network, and its Service is a ClusterIP.
    """
    return {
        "nginxGateway": {
            "gatewayClassName": GATEWAY_CLASS,
            "gatewayControllerName": "gateway.nginx.org/nginx-gateway-controller",
            "replicas": 1,
            "resources": NGF_RESOURCES,
            "snippets": {"enable": False},
        },
        "nginx": {
            "replicas": 1,
            "container": {"resources": NGF_RESOURCES},
            # Needed for ExternalName backends.
            "config": {
                "dnsResolver": {
                    "addresses": [{"type": "IPAddress", "value": dns_ip}],
                    "timeout": "5s",
                    "cacheTTL": "30s",
                    "disableIPv6": False,
                },
                "rewriteClientIP": {
                    "mode": "XForwardedFor",
                    "setIPRecursively": True,
                    "trustedAddresses": [{"type": "CIDR", "value": cni.POD_CIDR}],
                },
            },
            "service": {"type": "ClusterIP"},
        },
    }


def install_crds() -> None:
    """Apply the Gateway API CRDs, which NGF and cert-manager (enableGatewayAPI) both watch."""
    print(f"Installing the Gateway API CRDs {GATEWAY_API_VERSION}...")
    crds = (
        f"https://github.com/kubernetes-sigs/gateway-api/releases/download/{GATEWAY_API_VERSION}/standard-install.yaml"
    )
    kube.kubectl_shown("apply", "-f", crds)


def install() -> None:
    """Installs or upgrades NGF's CRDs and NGF (an upgrade keeps its values current); needs install_crds().

    Helm installs a chart's crds/ only once, so NGF's CRDs are applied first, as ExternalsPodiumD does.
    """
    print(f"Installing NGINX Gateway Fabric {NGF_VERSION}...")
    ngf_crds = process.fetch(["helm", "show", "crds", NGF_CHART, "--version", NGF_VERSION])
    kube.apply(ngf_crds)
    dns_ip = kube.kubectl("get", "svc", "kube-dns", "-n", "kube-system", "-o", "jsonpath={.spec.clusterIP}")
    process.fetch(
        ["helm", "upgrade", "--install", *NGF_CHART_ARGS,
         "-n", EDGE_NAMESPACE, "--create-namespace", "--wait", "-f", "-"],
        stdin=serialize_yaml(ngf_values(dns_ip)),
        shown=True,
    )  # fmt: skip


def ngf_template() -> str:
    """Return the NGF chart rendered with its defaults; ngf_values() sets no image."""
    return process.fetch(["helm", "template", *NGF_CHART_ARGS])


def gateway_manifest() -> Doc:
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


def _gateway_policy(kind: str, name: str, spec: Doc) -> Doc:
    """Return an NGF policy on public-gateway."""
    return {
        "apiVersion": "gateway.nginx.org/v1alpha1",
        "kind": kind,
        "metadata": {"name": name, "namespace": EDGE_NAMESPACE, "labels": LABELS},
        "spec": spec,
    }


def proxy_settings_manifest() -> Doc:
    """ExternalsPodiumD's live default-proxy-settings: response buffers far above nginx's 4 KB default.

    KISS's /signin-oidc answer carries about 5 KB of headers; with 4 KB nginx
    answers 502 "upstream sent too big header".
    """
    buffering = {"bufferSize": "128k", "buffers": {"number": 8, "size": "128k"}, "busyBuffersSize": "256k"}
    return _gateway_policy(
        "ProxySettingsPolicy", "default-proxy-settings", {"targetRefs": [GATEWAY_REF], "buffering": buffering}
    )


def client_settings_manifest() -> Doc:
    """ExternalsPodiumD's live default-client-settings: no request-body limit (maxSize 0)."""
    return _gateway_policy(
        "ClientSettingsPolicy", "default-client-settings", {"targetRef": GATEWAY_REF, "body": {"maxSize": "0"}}
    )


def _service_port(docs: list[Doc], service: str, port: int | str) -> int:
    """Return the number of a backend port, which may be a Service port name."""
    if isinstance(port, int):
        return port
    found = manifests.find(docs, "Service", service)
    entries: list[Doc] = manifests.section(found, "spec").get("ports") or [] if found else []
    named = next((entry for entry in entries if entry.get("name") == port), None)
    if named is not None:
        return int(named["port"])
    msg = f"Ingress backend {service}:{port}: no such Service port in the render"
    raise process.UserError(msg)


def route_manifests(docs: list[Doc]) -> list[Doc]:
    """Per Ingress host an HTTPRoute in ExternalsPodiumD's shape, plus the ExternalName Services they target.

    An Ingress with FORWARDED_HOST_ANNOTATION sends that host upstream for each of its hosts.
    """
    services: dict[str, Doc] = {}
    routes: list[Doc] = []
    for doc in docs:
        if doc.get("kind") != "Ingress":
            continue
        rules: list[Doc] = manifests.section(doc, "spec").get("rules") or []
        annotations: Doc = manifests.section(manifests.section(doc, "metadata"), "annotations")
        namespace = manifests.section(doc, "metadata").get("namespace", NAMESPACE)
        for rule in rules:
            forwarded = str(annotations.get(FORWARDED_HOST_ANNOTATION, rule["host"]))
            service, route = _service_and_route(docs, rule, forwarded, namespace)
            services[manifests.name_of(service)] = service
            routes.append(route)
    return [*services.values(), *routes]


# ExternalsPodiumD's routes set these on every response, replacing an app's own.
RESPONSE_HEADERS = {"Strict-Transport-Security": "max-age=31536000", "X-Content-Type-Options": "nosniff"}


def _service_and_route(docs: list[Doc], rule: Doc, forwarded: str, namespace: str) -> tuple[Doc, Doc]:
    """Return the ExternalName Service and the HTTPRoute for an Ingress rule of namespace."""
    host = str(rule["host"])
    paths: list[Doc] = manifests.section(rule, "http").get("paths") or []
    backend: Doc = manifests.section(manifests.section(paths[0], "backend"), "service")
    ports = manifests.section(backend, "port")
    number: int | str = ports.get("number") or str(ports.get("name"))
    port = _service_port(docs, str(backend["name"]), number)
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": backend["name"], "namespace": EDGE_NAMESPACE, "labels": LABELS},
        "spec": {
            "type": "ExternalName",
            "externalName": f"{backend['name']}.{namespace}.svc.cluster.local",
            "ports": [{"port": port, "targetPort": port}],
        },
    }
    headers = [
        {"name": "X-Forwarded-Host", "value": forwarded},
        {"name": "X-Forwarded-Proto", "value": "https"},
        {"name": "X-Forwarded-Port", "value": "443"},
    ]
    route = {
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
                    {"type": "URLRewrite", "urlRewrite": {"hostname": forwarded}},
                    {"type": "RequestHeaderModifier", "requestHeaderModifier": {"set": headers}},
                    {"type": "ResponseHeaderModifier", "responseHeaderModifier": {"set": [
                        {"name": name, "value": value} for name, value in RESPONSE_HEADERS.items()
                    ]}},
                ],
                "backendRefs": [{"name": backend["name"], "port": port}],
            }],
        },
    }  # fmt: skip
    return service, route


def _delete_stale(wanted: dict[tuple[str, str], Doc]) -> None:
    """Delete the edge's labelled objects not in wanted ((kind, name) -> spec) or with another externalName."""
    kinds = ("HTTPRoute", "Service", "ProxySettingsPolicy", "ClientSettingsPolicy")
    live = [
        (kind, item)
        for kind in kinds
        for item in kube.get_json(kind.lower(), "-n", EDGE_NAMESPACE, "-l", _SELECTOR)["items"]
    ]
    for kind, item in live:
        spec = wanted.get((kind, item["metadata"]["name"]))
        target = manifests.section(item, "spec").get("externalName")
        if spec is None or target != spec.get("externalName"):
            kube.kubectl("delete", kind.lower(), item["metadata"]["name"], "-n", EDGE_NAMESPACE)
            print(f"  deleted {kind} {item['metadata']['name']} ({'retargeted' if spec else 'no longer rendered'})")


def free_edge_ip() -> None:
    """Delete NGF's Service once while it holds EDGE_CLUSTER_IP (from before the WAF); NGF recreates it."""
    live = kube.kubectl("get", "svc", NGF_SERVICE, "-n", EDGE_NAMESPACE, "-o", "jsonpath={.spec.clusterIP}").strip()
    if live != EDGE_CLUSTER_IP:
        return
    print(f"  {NGF_SERVICE} holds the edge's ClusterIP {EDGE_CLUSTER_IP}: recreating it for the WAF")
    kube.kubectl("delete", "svc", NGF_SERVICE, "-n", EDGE_NAMESPACE)
    if not polling.wait_until(
        lambda: kube.is_present(f"service/{NGF_SERVICE}", EDGE_NAMESPACE), timeout=120, interval=3
    ):
        msg = f"NGF did not recreate {NGF_SERVICE} within 120s: run `podiumd-minikube provision-cluster`"
        raise process.UserError(msg)


def apply(docs: list[Doc], hosts: list[str]) -> None:
    """Apply the certificate, Gateway and routes; delete routes no longer rendered."""
    if not kube.is_present(f"deployment/{NGF_RELEASE}", EDGE_NAMESPACE):
        msg = f"NGINX Gateway Fabric is not in namespace {EDGE_NAMESPACE!r}: run `podiumd-minikube provision-cluster`"
        raise process.UserError(msg)
    print("\nApplying the NGINX Gateway Fabric Gateway and routes (gateway.py)...")
    objects = [
        tls.certificate(tls.CERTIFICATE, EDGE_NAMESPACE, hosts),
        gateway_manifest(),
        proxy_settings_manifest(),
        client_settings_manifest(),
        *route_manifests(docs),
    ]
    # NGF keeps an ExternalName Service's old target when it changes in place: such a Service is recreated.
    wanted = {(doc["kind"], manifests.name_of(doc)): manifests.section(doc, "spec") for doc in objects}
    _delete_stale(wanted)
    kube.apply(manifests.dump(objects))
    if not polling.wait_until(
        lambda: kube.is_present(f"service/{NGF_SERVICE}", EDGE_NAMESPACE), timeout=120, interval=3
    ):
        msg = f"NGF created no Service {EDGE_NAMESPACE}/{NGF_SERVICE} for Gateway {GATEWAY} within 120s"
        raise process.UserError(msg)
    kube.kubectl_shown(
        "wait", "--for=condition=Ready", f"certificate/{tls.CERTIFICATE}", "-n", EDGE_NAMESPACE, "--timeout=120s"
    )
    free_edge_ip()
