"""
Shared fixtures for the live-cluster test suite.

These are integration/smoke tests against a real, already-deployed minikube
cluster - not unit tests. They assume:

  - `kubectl` is configured against the cluster (current context)
  - the chart is deployed to the `podiumd-minikube` namespace
  - the edge (NGINX Gateway Fabric) has a real LoadBalancer external IP (via `minikube tunnel` -
    see ../scripts/setup-tunnel)

Requests are made by IP with an explicit Host header rather than through
`/etc/hosts`-resolved hostnames, so the suite runs without needing any
local `/etc/hosts` edits (useful for CI or a fresh checkout).
"""

import json
import os
import socket
import subprocess

from pathlib import Path

import pytest

NAMESPACE = "podiumd-minikube"
# The edge: NGINX Gateway Fabric's Service for Gateway public-gateway (scripts/lib/paths.py).
EDGE_NAMESPACE = "ingress-basic"
EDGE_SERVICE = "public-gateway-nginx"
REQUEST_TIMEOUT = 10
# The local CA that signs every ingress host's certificate (scripts/lib/pki.py);
# requests verifies against it everywhere in this suite.
CA_FILE = str(Path(__file__).resolve().parents[1] / ".pki" / "ca.crt")
os.environ["REQUESTS_CA_BUNDLE"] = CA_FILE


def pytest_addoption(parser):
    """--update-memory-baseline: test_memory writes the measured memory to memory-baseline.json."""
    parser.addoption("--update-memory-baseline", action="store_true", help="rewrite tests/memory-baseline.json")


def kubectl(*args):
    """Run kubectl and return stdout, raising if it fails."""
    result = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        msg = f"kubectl {' '.join(args)} failed: {result.stderr}"
        raise RuntimeError(msg)
    return result.stdout


@pytest.fixture(scope="session")
def edge_ip():
    """The edge LoadBalancer's external IP (EDGE), or skip the whole suite."""
    try:
        ip = kubectl(
            "get",
            "svc",
            EDGE_SERVICE,
            "-n",
            EDGE_NAMESPACE,
            "-o",
            "jsonpath={.status.loadBalancer.ingress[0].ip}",
        ).strip()
    except (RuntimeError, FileNotFoundError) as exc:
        pytest.skip(f"could not reach the cluster via kubectl: {exc}")
    if not ip:
        pytest.skip(f"{EDGE_NAMESPACE}/{EDGE_SERVICE} has no external IP yet - is `minikube tunnel` running?")
    _resolve_local_hosts_to(ip)
    return ip


def _resolve_local_hosts_to(ip):
    """Resolves every *.local host to the edge in this process, so https URLs get the right SNI."""
    original = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if isinstance(host, str) and host.endswith(".local"):
            host = ip
        return original(host, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo


@pytest.fixture(scope="session")
def pods(edge_ip):
    """All pods in the chart's namespace: name, phase, container_statuses, from_job, job (its Job's name or None)."""
    raw = kubectl("get", "pods", "-n", NAMESPACE, "-o", "json")
    data = json.loads(raw)
    return [
        {
            "name": item["metadata"]["name"],
            "phase": item["status"]["phase"],
            "container_statuses": item["status"].get("containerStatuses", []),
            # Job pods (CronJob runs included) end not-ready by design.
            "from_job": any(ref.get("kind") == "Job" for ref in item["metadata"].get("ownerReferences", [])),
            "job": next(
                (ref["name"] for ref in item["metadata"].get("ownerReferences", []) if ref.get("kind") == "Job"), None
            ),
        }
        for item in data["items"]
    ]


@pytest.fixture(scope="session")
def enabled_profiles(pods):
    """
    Which optional profile groups are actually deployed right now, derived
    from pod name prefixes rather than values.yaml - so the suite adapts to
    whatever's really running instead of assuming every profile is on.
    """
    names = {p["name"] for p in pods}

    def any_pod_named(prefix):
        return any(n == prefix or n.startswith(prefix + "-") for n in names)

    return {
        "objecten": any_pod_named("objecten"),
        "objecttypen": any_pod_named("objecttypen"),
        "opennotificaties": any_pod_named("opennotificaties"),
        "openarchiefbeheer": any_pod_named("openarchiefbeheer"),
        "openformulieren": any_pod_named("openformulieren"),
        "openinwoner": any_pod_named("openinwoner"),
        "ita": any_pod_named("ita-web"),
        "kiss": any_pod_named("contact-web"),
        "omc": any_pod_named("omc"),
        "referentielijsten": any_pod_named("referentielijsten"),
        "openbeheer": any_pod_named("openbeheer"),
        "frankgateway": any_pod_named("frankgateway-outway"),
        "metrics": any_pod_named("grafana"),
        # The monitoring-logging dependency's own subcharts are all prefixed
        # with the release name ("podiumd-minikube-grafana", not plain
        # "grafana" like templates/metrics/grafana.yaml's raw template) -
        # exactly what tells the two mutually-exclusive "metrics" profile
        # implementations apart (see values.yaml's own monitoringLogging
        # comment). Deliberately its own key, not folded into "metrics"
        # above: a test that only makes sense against one implementation
        # (e.g. Loki-specific checks) needs to skip on the *other*
        # implementation too, not just when the whole profile is off.
        "monitoringLogging": any_pod_named("podiumd-minikube-grafana"),
    }


def host_url(hostname, path="/"):
    """https URL of an ingress host; the edge_ip fixture resolves *.local to the edge."""
    return f"https://{hostname}{path}"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, edge_ip):
    """
    Extends pytest-playwright's own fixture: makes the browser resolve
    every *.local hostname straight to the edge's IP (Chromium's own
    --host-resolver-rules), so browser-based tests can navigate to real
    URLs like https://zac.local/ with no `/etc/hosts` edit needed - same
    "no local hosts-file changes required" property as the rest of this
    suite, just done at the browser level instead of a manual Host header.
    """
    return {
        **browser_type_launch_args,
        "args": [
            f"--host-resolver-rules=MAP zac.local {edge_ip},MAP keycloak.local {edge_ip},MAP mailpit.local {edge_ip}",
        ],
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Accepts the local CA's certificates: Chromium would need it in an NSS database."""
    return {**browser_context_args, "ignore_https_errors": True}
