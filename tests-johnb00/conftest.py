"""
Shared fixtures for the johnb00 live-cluster test suite - adapted from
../tests/ (which targets a local minikube cluster) to run against the real
johnb00 AKS environment (podiumd-infra repo, values/johnb00/).

Structural differences from ../tests/conftest.py that drive every fixture
here:

  - Real DNS + a real, browser-trusted Let's Encrypt production cert for
    every app (https://<host>.johnb00.pd.test-rig.nl) - no Traefik
    LoadBalancer IP + explicit Host header trick needed, and no Chromium
    --host-resolver-rules fixture either. `requests`/Playwright just hit
    the real URL directly.
  - Namespace is `podiumd` (not `podiumd-minikube`).
  - Postgres is an external Azure Postgres Flexible Server
    (podiumd-johnb00-pg.postgres.database.azure.com), not an in-cluster
    pod - there is nothing to `kubectl exec` into for psql. test_database.py
    and test_zgw_service_reachability.py instead exec into each app's own
    pod and use `manage.py shell` (Django's own DB connection), which needs
    no separate credential at all.
"""

import json
import os
import subprocess

import pytest

NAMESPACE = "podiumd"
DOMAIN = "johnb00.pd.test-rig.nl"
REQUEST_TIMEOUT = 10
_ENV_FILE = os.path.join(os.path.dirname(__file__), ".env.johnb00")


def kubectl(*args):
    """Run kubectl and return stdout, raising if it fails."""
    result = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr}")
    return result.stdout


@pytest.fixture(scope="session")
def pods():
    """All pods in the podiumd namespace, as a list of (name, phase) dicts."""
    try:
        raw = kubectl("get", "pods", "-n", NAMESPACE, "-o", "json")
    except (RuntimeError, FileNotFoundError) as exc:
        pytest.skip(f"could not reach the cluster via kubectl: {exc}")
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
    from pod name prefixes - same approach as ../tests/conftest.py, but
    against johnb00's real pod names. Two johnb00-specific renames vs. the
    minikube reference:

      - opennotificaties' Helm release uses `fullnameOverride: notificaties`
        (see values/johnb00/podiumd.yaml) - its pods/Services are literally
        named "notificaties", not "opennotificaties". Detected by prefix
        here so the profile key stays "opennotificaties" (matching every
        other test file's expectations); the *Service*/*Ingress* host is
        still "notificaties", handled in test_reachability.py instead.
      - openarchiefbeheer is confirmed `enabled: false` on johnb00 (unlike
        minikube, where it's an optional-but-usually-on profile) - no pods,
        so this correctly reports False and every openarchiefbeheer test
        auto-skips.
    """
    names = {p["name"] for p in pods}

    def any_pod_named(prefix):
        return any(n == prefix or n.startswith(prefix + "-") for n in names)

    return {
        "objecten": any_pod_named("objecten"),
        "objecttypen": any_pod_named("objecttypen"),
        "opennotificaties": any_pod_named("notificaties"),
        "openarchiefbeheer": any_pod_named("openarchiefbeheer"),
        "openformulieren": any_pod_named("openformulieren"),
        # johnb00 has neither metrics implementation deployed
        # (grafana-deploy was left at its default 'false') - both always
        # False here, so test_metrics.py/test_monitoring_logging.py both
        # auto-skip, same mutual-exclusion contract as the minikube suite.
        "metrics": any_pod_named("grafana"),
        "monitoringLogging": any_pod_named("podiumd-grafana"),
    }


def app_url(host, path="/"):
    return f"https://{host}.{DOMAIN}{path}"


@pytest.fixture(scope="session")
def keycloak_test_user():
    """
    (username, password) for the real "johnb00" podiumd-realm user. The
    password isn't in Key Vault-readable form from outside CI - it was set
    directly via the Keycloak Admin API (same maneuver as the minikube
    suite's own dev-user story - see ../tests/test_login_flow.py's
    docstring) using the master-realm admin credential in the
    `keycloak-podiumd-admin` k8s Secret, and stored only in the gitignored
    .env.johnb00 next to this file. See tests-johnb00/README.md to reset it.
    """
    if not os.path.exists(_ENV_FILE):
        pytest.skip(
            f"{_ENV_FILE} not found - see tests-johnb00/README.md to set the "
            "johnb00 Keycloak test user's password via the Admin API first"
        )
    values = {}
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key] = value
    return values["KEYCLOAK_TEST_USERNAME"], values["KEYCLOAK_TEST_PASSWORD"]
