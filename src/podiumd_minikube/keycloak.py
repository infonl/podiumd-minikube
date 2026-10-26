"""The repair podiumd's realm import needs, as podiumd-infra's scripts/ensure-keycloak-default-client-scopes.sh.

The chart's realm config lists only `optionalClientScopes` per client, and
keycloak-config-cli then creates every client without default client scopes:
logins get no profile, email or roles (KISS: invalid_scope, ZAC: no roles).
podiumd<4.10: the chart's keycloak-repair-client-scopes Job does this since.
"""

import base64
import json
import shlex

from typing import Any

from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import polling
from podiumd_minikube import process
from podiumd_minikube.paths import NAMESPACE

REALM = "podiumd"
POD = "keycloak-0"
ADMIN_SECRET = "keycloak-podiumd-admin"  # nosec B105  # noqa: S105 - a Secret name
IMPORT_JOB_PREFIX = "import-podiumd-realm-job"
# podiumd>=4.10 renders this Job, which does the same repair (and survives a Keycloak restart).
CHART_REPAIR_JOB_PREFIX = "keycloak-repair-client-scopes"
IMPORT_TIMEOUT = 900
# Keycloak's own clients keep their scopes.
BUILTIN_CLIENTS = {"account", "account-console", "admin-cli", "broker", "realm-management", "security-admin-console"}
_CONFIG = "/tmp/kcadm-podiumd-minikube.cfg"  # nosec B108  # noqa: S108 - inside the Keycloak container


def kcadm(*commands: list[str]) -> str:
    """Output of kcadm.sh commands in the Keycloak pod, logged in as the chart's bootstrap admin (read from stdin)."""
    secret = kube.get_json("secret", ADMIN_SECRET, "-n", NAMESPACE)["data"]
    credentials = "".join(f"{base64.b64decode(secret[key]).decode()}\n" for key in ("username", "password"))
    kc = "/opt/keycloak/bin/kcadm.sh"
    login = f"{kc} config credentials --config {_CONFIG} --server http://localhost:8080 --realm master"
    script = "\n".join([
        "set -e", "read -r u", "read -r p", f"trap 'rm -f {_CONFIG}' EXIT",
        f'{login} --user "$u" --password "$p" >/dev/null',
        *(f"{kc} {shlex.join(command)} --config {_CONFIG}" for command in commands),
    ])  # fmt: skip
    return kube.kubectl(
        "exec", "-i", "-n", NAMESPACE, POD, "-c", "keycloak", "--", "bash", "-c", script, stdin=credentials
    )


def missing_default_scopes(clients: list[dict[str, Any]], defaults: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Client id -> ids of the realm default scopes of its protocol, for each non-builtin client without any."""
    return {
        client["id"]: [s["id"] for s in defaults if s.get("protocol") == client.get("protocol", "openid-connect")]
        for client in clients
        if client["clientId"] not in BUILTIN_CLIENTS and not client.get("defaultClientScopes")
    }


def _import_done(render: manifests.Render) -> bool:
    imports = [name for name in render.jobs if name.startswith(IMPORT_JOB_PREFIX)]
    return all(kube.get_json("job", name, "-n", NAMESPACE).get("status", {}).get("succeeded") for name in imports)


def repair_default_client_scopes(render: manifests.Render) -> None:
    """Gives every chart client without default client scopes the realm's, once the realm import has run.

    Skipped when the chart's own repair Job is rendered: an exec here failed when the deploy replaced keycloak-0.
    """
    if any(name.startswith(CHART_REPAIR_JOB_PREFIX) for name in render.jobs) or not kube.exists(f"pod/{POD}"):
        return
    print("Repairing the podiumd realm's client scopes (keycloak.py)...")
    if not polling.wait_until(lambda: _import_done(render), timeout=IMPORT_TIMEOUT, interval=10):
        msg = f"the {IMPORT_JOB_PREFIX} Job did not complete within {IMPORT_TIMEOUT}s: check its logs"
        raise process.UserError(msg)
    defaults = json.loads(kcadm(["get", f"realms/{REALM}/default-default-client-scopes", "--fields", "id,protocol"]))
    clients = json.loads(kcadm(["get", "clients", "-r", REALM, "--fields", "id,clientId,protocol,defaultClientScopes"]))
    missing = missing_default_scopes(clients, defaults)
    updates = [
        ["update", f"clients/{client_id}/default-client-scopes/{scope_id}", "-r", REALM]
        for client_id, scope_ids in missing.items()
        for scope_id in scope_ids
    ]
    if updates:
        kcadm(*updates)
    print(f"Clients given the realm's default client scopes: {len(missing)}")
