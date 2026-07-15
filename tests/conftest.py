"""
Shared fixtures for the live-cluster test suite.

These are integration/smoke tests against a real, already-deployed minikube
cluster - not unit tests. They assume:

  - `kubectl` is configured against the cluster (current context)
  - the chart is deployed to the `podiumd-minikube` namespace
  - Traefik has a real LoadBalancer external IP (via `minikube tunnel` -
    see ../scripts/setup-tunnel.sh)

Requests are made by IP with an explicit Host header rather than through
`/etc/hosts`-resolved hostnames, so the suite runs without needing any
local `/etc/hosts` edits (useful for CI or a fresh checkout).
"""

import json
import subprocess

import pytest

NAMESPACE = "podiumd-minikube"
TRAEFIK_NAMESPACE = "traefik"
TRAEFIK_SERVICE = "traefik"
REQUEST_TIMEOUT = 10


def kubectl(*args):
    """Run kubectl and return stdout, raising if it fails."""
    result = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr}")
    return result.stdout


@pytest.fixture(scope="session")
def traefik_ip():
    """The Traefik LoadBalancer's external IP, or skip the whole suite."""
    try:
        ip = kubectl(
            "get",
            "svc",
            TRAEFIK_SERVICE,
            "-n",
            TRAEFIK_NAMESPACE,
            "-o",
            "jsonpath={.status.loadBalancer.ingress[0].ip}",
        ).strip()
    except (RuntimeError, FileNotFoundError) as exc:
        pytest.skip(f"could not reach the cluster via kubectl: {exc}")
    if not ip:
        pytest.skip(
            "Traefik has no external IP yet - is `minikube tunnel` running? "
            "See scripts/setup-tunnel.sh."
        )
    return ip


@pytest.fixture(scope="session")
def pods(traefik_ip):
    """All pods in the chart's namespace, as a list of (name, phase) dicts."""
    raw = kubectl("get", "pods", "-n", NAMESPACE, "-o", "json")
    data = json.loads(raw)
    return [
        {
            "name": item["metadata"]["name"],
            "phase": item["status"]["phase"],
            "container_statuses": item["status"].get("containerStatuses", []),
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
        "metrics": any_pod_named("grafana"),
        "itest": any_pod_named("greenmail"),
    }


def host_url(traefik_ip, path="/"):
    return f"http://{traefik_ip}{path}"


def host_headers(hostname):
    return {"Host": hostname}
