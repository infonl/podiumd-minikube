"""The WAF in front of NGF, as podiumd-infra's stand-in for ExternalsPodiumD's Application Gateway (WAF_v2).

Ported from podiumd-infra (branch feat/aag-equivalent, src/podiumd_infra/waf.py, docs/waf.md): nginx with
ModSecurity v3 and OWASP CRS 3.3 in DetectionOnly, always on. Its limits come from NGF's policies in gateway.py,
so the WAF never refuses what NGF accepts. Deviations: one replica, and no preStop sleep or minReadySeconds
(those wait for Azure's load balancer probes, which minikube tunnel does not have).
"""

import hashlib
import json
import re

from podiumd_minikube import gateway
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube import tls
from podiumd_minikube.objects import Doc
from podiumd_minikube.paths import EDGE_NAMESPACE
from podiumd_minikube.paths import EDGE_SERVICE

# CRS 3.3 is the nearest maintained line (LTS) to the Application Gateway's OWASP 3.2 ruleset.
IMAGE = "owasp/modsecurity-crs:3.3.10-nginx-202610180210-lts"
# The image's user is named (nginx); runAsNonRoot can only verify a number.
IMAGE_UID = 101
# The image's nginx runs unprivileged, so it listens on high ports.
HTTP_PORT = 8080
HTTPS_PORT = 8443
HEALTH_PORT = 8081
NGF = f"{gateway.NGF_SERVICE}.{EDGE_NAMESPACE}.svc.cluster.local"
# The image renders this template into its server config.
TEMPLATE = "default.conf.template"
TEMPLATE_PATH = f"/etc/nginx/templates/conf.d/{TEMPLATE}"
# The image's ModSecurity body limit (12.5 MiB), for an NGF without one.
IMAGE_BODY_LIMIT = 13107200
EXCLUSIONS_FILE = "REQUEST-900-EXCLUSION-RULES-BEFORE-CRS.conf"
EXCLUSIONS_PATH = f"/etc/modsecurity.d/owasp-crs/rules/{EXCLUSIONS_FILE}"
# podiumd-infra's false positives on PodiumD traffic (kees00 full tier, 2026-11-11), on this cluster's hosts.
EXCLUSIONS = """\
# ZAC's BPMN and form definitions: XML and JSON with ${...} expressions in json.content read as XSS, SQLi and RCE.
SecRule REQUEST_HEADERS:Host "@beginsWith zac." \\
    "id:10001,phase:1,pass,nolog,chain"
    SecRule REQUEST_FILENAME "@rx ^/rest/bpmn-process-definitions(?:/[^/]+/forms)?$" \\
        "ctl:ruleRemoveTargetByTag=attack-xss;ARGS:json.content,\\
        ctl:ruleRemoveTargetByTag=attack-sqli;ARGS:json.content,\\
        ctl:ruleRemoveTargetByTag=attack-rce;ARGS:json.content"

# Grafana's datasource proxy: PromQL in query reads as PHP injection.
SecRule REQUEST_HEADERS:Host "@beginsWith grafana." \\
    "id:10002,phase:1,pass,nolog,chain"
    SecRule REQUEST_FILENAME "@beginsWith /api/datasources/proxy/" \\
        "ctl:ruleRemoveTargetByTag=attack-injection-php;ARGS:query"

# Open Inwoner's document upload: htmx adds the unnamed submit button as a part with name="", which
# ModSecurity's multipart parser reports as "Final boundary missing" (200002, 200003).
SecRule REQUEST_HEADERS:Host "@beginsWith openinwoner." \\
    "id:10003,phase:1,pass,nolog,chain"
    SecRule REQUEST_FILENAME "@rx ^/mijn-zaken/[^/]+/[^/]+/status/document-form/$" \\
        "ctl:ruleRemoveById=200002,ctl:ruleRemoveById=200003"
"""
TLS_DIR = "/etc/nginx/tls"
# nginx's string quote.
QUOTE = "'"
# proxy_set_header with an empty value sends no header.
REMOVED = '""'
# The client certificate as the Application Gateway's passthrough mode forwards it (IN-3287), unchecked: the leaf
# as URL-encoded PEM, its SHA-1 in lower-case hex, subject and issuer as RFC 2253. The inway decides on the
# fingerprint. Every other server removes them.
CLIENT_CERT_HEADERS = {
    "X-Client-Cert": "$ssl_client_escaped_cert",
    "X-Client-Cert-Fingerprint": "$ssl_client_fingerprint",
    "X-Client-Cert-Subject": "$ssl_client_s_dn",
    "X-Client-Cert-Issuer": "$ssl_client_i_dn",
}
# The hosts of Ingresses to this Service ask for a client certificate (podiumd-infra's waf.mtls.hosts: [inway]).
MTLS_SERVICE = "frankgateway-inway"
# nginx's defaults, which NGF keeps where its ClientSettingsPolicy sets nothing.
NGINX_BODY_MAX_SIZE = "1m"
NGINX_BODY_TIMEOUT = "60s"
NGINX_KEEPALIVE_TIMEOUT = "75s"
_SIZE = re.compile(r"(\d+)([kKmMgG]?)")
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
# The access log's fields, a JSON line each, as the Application Gateway's access log has them.
LOG_FIELDS = {
    "time": "$time_iso8601",
    "client": "$remote_addr",
    "host": "$host",
    "request": "$request",
    "status": "$status",
    "bytes": "$body_bytes_sent",
    "seconds": "$request_time",
    "upstream": "$upstream_addr",
    "upstream_status": "$upstream_status",
    # Not in the Application Gateway's log: tells the WAF's own time from NGF's and the apps'.
    "upstream_seconds": "$upstream_response_time",
    "body_bytes": "$request_length",
    "worker": "$pid",
    "sni": "$ssl_server_name",
    "client_fingerprint": "$ssl_client_fingerprint",
}
# TLS 1.2: NCSC's good and sufficient suites only (ECDHE with AES-GCM or ChaCha20-Poly1305); nginx's default
# HIGH:!aNULL:!MD5 also accepts static RSA, CAMELLIA and ARIA. TLS 1.3's suites are all good.
TLS12_CIPHERS = (
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
)
# libmodsecurity 3 reads a request body that nginx buffered to a file whole into memory before it applies
# SecRequestBodyLimit (Transaction::requestBodyFromFile), so an upload costs its size in one worker: room for both
# workers (NGINX_WORKER_PROCESSES) taking a ZAC upload of its 500 MB maximum at once.
RESOURCES = {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1536Mi"}}
# Not gateway.LABELS: gateway.apply deletes every labelled Service it did not render.
LABELS = {"app.kubernetes.io/name": EDGE_SERVICE}


def size_bytes(size: str) -> int:
    """Return an nginx size (0, 128k, 600m) in bytes."""
    found = _SIZE.fullmatch(size)
    if not found:
        msg = f"{size!r} is no nginx size such as 0, 128k or 600m"
        raise process.UserError(msg)
    return int(found.group(1)) * _UNITS[found.group(2).lower()]


def _client_settings() -> Doc:
    return gateway.client_settings_manifest()["spec"]


def body_max_size() -> str:
    """Return NGF's client body limit (ClientSettingsPolicy body.maxSize; 0 is unlimited)."""
    return str(_client_settings().get("body", {}).get("maxSize", NGINX_BODY_MAX_SIZE))


def _own_response_headers() -> list[str]:
    """Return gateway.RESPONSE_HEADERS for a response the WAF makes itself (413, 502).

    NGF's routes set them on what they answer; an empty value adds nothing, so those get no second copy.
    """
    lines: list[str] = []
    for number, (name, value) in enumerate(gateway.RESPONSE_HEADERS.items()):
        variable = f"$waf_own_header_{number}"
        lines += [
            f'map $upstream_status {variable} {{ "" {QUOTE}{value}{QUOTE}; default ""; }}',
            f"add_header {name} {variable} always;",
        ]
    return lines


def _http_directives() -> list[str]:
    """Return the http-level directives that keep the WAF's limits and buffers at NGF's."""
    buffering: Doc = gateway.proxy_settings_manifest()["spec"]["buffering"]
    buffers: Doc = buffering["buffers"]
    log_line = json.dumps(LOG_FIELDS, separators=(",", ":"))
    return [
        *_own_response_headers(),
        "map $http_upgrade $connection_upgrade { default upgrade; '' close; }",
        # nginx quotes the format in single quotes; the JSON holds none.
        f"log_format waf_json escape=json {QUOTE}{log_line}{QUOTE};",
        "access_log /dev/stdout waf_json;",
        f"client_max_body_size {body_max_size()};",
        f"proxy_buffer_size {buffering['bufferSize']};",
        f"proxy_buffers {buffers['number']} {buffers['size']};",
        f"proxy_busy_buffers_size {buffering['busyBuffersSize']};",
        "proxy_http_version 1.1;",
        "ssl_protocols TLSv1.2 TLSv1.3;",
        f"ssl_ciphers {':'.join(TLS12_CIPHERS)};",
        "ssl_prefer_server_ciphers on;",
    ]


def _indented(lines: list[str]) -> list[str]:
    return [f"    {line}" for line in lines]


def _server(listen: str, lines: list[str]) -> list[str]:
    return ["server {", *_indented([f"listen {listen};", *lines]), "}"]


def _location(scheme: str, port: int, headers: dict[str, str]) -> list[str]:
    """Return location / to NGF's listener of scheme, with the Application Gateway's X-Forwarded headers and headers.

    An empty value in headers removes that header.
    """
    upstream = [f"proxy_pass {scheme}://{NGF}:{port};"]
    if scheme == "https":
        upstream += ["proxy_ssl_server_name on;", "proxy_ssl_name $host;"]
    forwarded = {
        "Host": "$host",
        "X-Forwarded-For": "$proxy_add_x_forwarded_for",
        "X-Forwarded-Proto": scheme,
        "X-Forwarded-Port": str(port),
        "X-Original-Host": "$host",
        "Upgrade": "$http_upgrade",
        "Connection": "$connection_upgrade",
        **headers,
    }
    sets = [f"proxy_set_header {name} {value or REMOVED};" for name, value in forwarded.items()]
    return ["location / {", *_indented([*upstream, *sets]), "}"]


def mtls_hosts(docs: list[Doc]) -> list[str]:
    """Return the hosts that ask for a client certificate: those of the rendered Ingresses to MTLS_SERVICE."""
    return manifests.ingress_hosts(docs, MTLS_SERVICE)


def _mtls_server(host: str, tls_files: list[str]) -> list[str]:
    """Return the server of host: any client certificate or none, forwarded in the headers."""
    verify = [f"server_name {host};", *tls_files, "ssl_verify_client optional_no_ca;"]
    return _server(f"{HTTPS_PORT} ssl", [*verify, *_location("https", 443, CLIENT_CERT_HEADERS)])


def config(client_certificate_hosts: list[str]) -> str:
    """Return the WAF's nginx server config: port 80 and 443 to NGF's, a server per mTLS host, and a health endpoint
    of its own.
    """
    health = ["modsecurity off;", "access_log off;", 'location = /healthz { return 200 "OK"; }']
    cleared: dict[str, str] = dict.fromkeys(CLIENT_CERT_HEADERS, "")
    tls_files = [f"ssl_certificate {TLS_DIR}/tls.crt;", f"ssl_certificate_key {TLS_DIR}/tls.key;"]
    blocks = [
        _http_directives(),
        _server(str(HEALTH_PORT), health),
        # No redirect to https here: NGF redirects, as behind the Application Gateway.
        _server(f"{HTTP_PORT} default_server", _location("http", 80, cleared)),
        _server(f"{HTTPS_PORT} ssl default_server", [*tls_files, *_location("https", 443, cleared)]),
        *(_mtls_server(host, tls_files) for host in client_certificate_hosts),
    ]
    return "\n\n".join("\n".join(block) for block in blocks)


def modsecurity_env() -> dict[str, str]:
    """Return the image's settings of ModSecurity, the CRS and nginx that differ from its defaults."""
    client = _client_settings()
    inspected = str(size_bytes(body_max_size()) or IMAGE_BODY_LIMIT)
    return {
        "MODSEC_RULE_ENGINE": "DetectionOnly",
        "BLOCKING_PARANOIA": "1",
        "MODSEC_AUDIT_LOG": "/dev/stdout",
        "MODSEC_AUDIT_LOG_FORMAT": "JSON",
        # Without part B (request headers): Authorization and Cookie values would land in the logs.
        "MODSEC_AUDIT_LOG_PARTS": "AHZ",
        # The ZGW APIs and ZAC update with PUT, PATCH and DELETE; CRS 3.3 allows only GET HEAD POST OPTIONS.
        "ALLOWED_METHODS": "GET HEAD POST OPTIONS PUT PATCH DELETE",
        # The Application Gateway inspects requests only.
        "MODSEC_RESP_BODY_ACCESS": "Off",
        # client_max_body_size refuses what NGF refuses; ModSecurity inspects the first part of a larger body.
        "MODSEC_REQ_BODY_LIMIT_ACTION": "ProcessPartial",
        "MODSEC_REQ_BODY_LIMIT": inspected,
        # JSON with an inline document: the image's 128 KB failed ZAC's PATCH on Open Zaak with rule 200002.
        "MODSEC_REQ_BODY_NOFILES_LIMIT": inspected,
        "CLIENT_BODY_TIMEOUT": str(client.get("body", {}).get("timeout", NGINX_BODY_TIMEOUT)),
        "KEEPALIVE_TIMEOUT": str(client.get("keepAlive", {}).get("timeout", {}).get("server", NGINX_KEEPALIVE_TIMEOUT)),
        "ACCESSLOG": "/dev/null",
        "ERRORLOG": "/dev/stderr",
        "NGINX_WORKER_PROCESSES": "2",
    }


def _meta(name: str) -> Doc:
    return {"name": name, "namespace": EDGE_NAMESPACE, "labels": LABELS}


def _probe() -> Doc:
    return {"httpGet": {"path": "/healthz", "port": HEALTH_PORT}, "periodSeconds": 10}


def _container() -> Doc:
    mounts = [
        {"name": "config", "mountPath": TEMPLATE_PATH, "subPath": TEMPLATE},
        {"name": "config", "mountPath": EXCLUSIONS_PATH, "subPath": EXCLUSIONS_FILE},
        {"name": "tls", "mountPath": TLS_DIR, "readOnly": True},
    ]
    ports = {"http": HTTP_PORT, "https": HTTPS_PORT, "health": HEALTH_PORT}
    return {
        "name": "waf",
        "image": IMAGE,
        "env": [{"name": name, "value": value} for name, value in modsecurity_env().items()],
        "ports": [{"name": name, "containerPort": port} for name, port in ports.items()],
        "readinessProbe": _probe(),
        "livenessProbe": _probe(),
        "resources": RESOURCES,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "runAsNonRoot": True,
            "runAsUser": IMAGE_UID,
            "runAsGroup": IMAGE_UID,
        },
        "volumeMounts": mounts,
    }


def _deployment(text: str) -> Doc:
    """Return the Deployment; the hash of its config restarts it when the config changes."""
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    volumes = [
        {"name": "config", "configMap": {"name": EDGE_SERVICE}},
        {"name": "tls", "secret": {"secretName": tls.CERTIFICATE}},
    ]
    template = {
        "metadata": {"labels": LABELS, "annotations": {"checksum/config": digest}},
        "spec": {
            # Service variables would land in the image's envsubst of its templates.
            "enableServiceLinks": False,
            "containers": [_container()],
            "volumes": volumes,
        },
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(EDGE_SERVICE),
        "spec": {"replicas": 1, "selector": {"matchLabels": LABELS}, "template": template},
    }


def _service() -> Doc:
    """Return the edge's LoadBalancer at gateway.EDGE_CLUSTER_IP; Local keeps the client's address."""
    ports = [{"name": "http", "port": 80, "targetPort": "http"}, {"name": "https", "port": 443, "targetPort": "https"}]
    spec = {
        "type": "LoadBalancer",
        "clusterIP": gateway.EDGE_CLUSTER_IP,
        "externalTrafficPolicy": "Local",
        "selector": LABELS,
        "ports": ports,
    }
    return {"apiVersion": "v1", "kind": "Service", "metadata": _meta(EDGE_SERVICE), "spec": spec}


def rendered(client_certificate_hosts: list[str]) -> list[Doc]:
    """Return the WAF's ConfigMap, Deployment and Service."""
    text = config(client_certificate_hosts)
    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(EDGE_SERVICE),
        "data": {TEMPLATE: text, EXCLUSIONS_FILE: EXCLUSIONS},
    }
    return [configmap, _deployment(text + EXCLUSIONS), _service()]


def apply(docs: list[Doc]) -> None:
    """Apply the WAF for the rendered docs after gateway.apply (NGF's Service and the certificate it mounts exist) and
    wait for it.
    """
    print("\nApplying the WAF in front of NGINX Gateway Fabric (waf.py)...")
    kube.apply(manifests.dump(rendered(mtls_hosts(docs))))
    kube.kubectl_shown("rollout", "status", f"deployment/{EDGE_SERVICE}", "-n", EDGE_NAMESPACE, "--timeout=300s")
    ip = kube.edge_ip()
    shown = ip or "<none - is minikube tunnel running?>"
    print(f"Edge (WAF) Service {EDGE_NAMESPACE}/{EDGE_SERVICE}: external IP {shown}")
