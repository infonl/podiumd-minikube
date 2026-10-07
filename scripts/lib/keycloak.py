"""Changes to the live Keycloak realm that a realm re-import cannot make.

--import-realm skips a realm that already exists, and Keycloak persists to
Postgres, so a changed vendored realm never reaches a provisioned cluster.
The same fixes as lib.manifests.fix_realm are applied to the live clients,
and clients added to the vendored realm are created.
"""

import json
import sys

from typing import Any

from lib import kube
from lib import manifests
from lib import polling
from lib import process
from lib.paths import NAMESPACE
from lib.paths import VENDOR_DIR

REALM = "zaakafhandelcomponent"
ZAC_CLIENT_ID = "zaakafhandelcomponent"
PKCE_ATTRIBUTE = "pkce.code.challenge.method"
# templates/keycloak/deployment.yaml's KC_BOOTSTRAP_ADMIN_USERNAME/PASSWORD.
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin"  # nosec B105  # noqa: S105 - dev-only default from the template
STARTUP_TIMEOUT = 90
REALM_FILE = VENDOR_DIR / "keycloak" / "zaakafhandelcomponent-realm.json"


def _kcadm(pod: str) -> list[str]:
    """kcadm.sh in pod; one pod for login and updates, kcadm keeps its session in a file there."""
    return ["kubectl", "exec", "-i", "-n", NAMESPACE, pod, "--", "/opt/keycloak/bin/kcadm.sh"]


def _clients(pod: str) -> list[Any] | None:
    """The realm's clients after a fresh kcadm login in pod; None while Keycloak is still starting."""
    login = [
        *_kcadm(pod), "config", "credentials", "--server", "http://localhost:8080",
        "--realm", "master", "--user", ADMIN_USER, "--password", ADMIN_PASSWORD,
    ]  # fmt: skip
    if not process.succeeds(login):
        return None
    fields = "id,clientId,redirectUris,webOrigins,attributes"
    result = process.run([*_kcadm(pod), "get", "clients", "-r", REALM, "--fields", fields], check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout) or None


def client_changes(client: dict[str, Any], *, zac_pkce: bool) -> list[str]:
    """kcadm `-s` settings that bring client in line with lib.manifests.fix_realm; [] when it is."""
    changes: list[str] = []
    for field in ("redirectUris", "webOrigins"):
        current: list[str] = client.get(field) or []
        wanted = manifests.with_https(current)
        if wanted != current:
            changes += ["-s", f"{field}={json.dumps(wanted)}"]
    if client.get("clientId") == ZAC_CLIENT_ID:
        target = "S256" if zac_pkce else ""
        attributes: dict[str, str] = client.get("attributes") or {}
        if attributes.get(PKCE_ATTRIBUTE, "") != target:
            changes += ["-s", f'attributes."{PKCE_ATTRIBUTE}"={target}']
    return changes


def missing_clients(live: list[dict[str, Any]], vendored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Vendored clients the live realm lacks, with the https URI twins of lib.manifests.fix_realm."""
    present = {client.get("clientId") for client in live}
    return [
        {
            **client,
            "redirectUris": manifests.with_https(client.get("redirectUris", [])),
            "webOrigins": manifests.with_https(client.get("webOrigins", [])),
        }
        for client in vendored
        if client.get("clientId") not in present
    ]


def sync_realm(*, zac_pkce: bool) -> None:
    """Applies the realm fixes to the live clients."""
    if not kube.exists("deployment/keycloak"):
        print("'keycloak' not found - skipping the realm sync.")
        return
    print(f"Reconciling Keycloak's live '{REALM}' realm clients...")
    # A changed Keycloak spec rolls out a new pod; exec into the new one only.
    kube.kubectl_shown("rollout", "status", "deployment/keycloak", "-n", NAMESPACE, "--timeout=300s")
    pod = kube.kubectl("get", "pod", "-n", NAMESPACE, "-l", "app=keycloak", "-o", "jsonpath={.items[0].metadata.name}")
    clients = polling.wait_until(lambda: _clients(pod), timeout=STARTUP_TIMEOUT, interval=3)
    if not clients:
        print(
            f"WARNING: Keycloak realm '{REALM}' not reachable after {STARTUP_TIMEOUT}s; "
            "realm not synced - re-run deploy once Keycloak is up.",
            file=sys.stderr,
        )
        return
    vendored: list[dict[str, Any]] = json.loads(REALM_FILE.read_text(encoding="utf-8"))["clients"]
    for client in missing_clients(clients, vendored):
        process.output([*_kcadm(pod), "create", "clients", "-r", REALM, "-f", "-"], stdin=json.dumps(client))
        print(f"  created client {client['clientId']}")
    for client in clients:
        changes = client_changes(client, zac_pkce=zac_pkce)
        if changes:
            process.output([*_kcadm(pod), "update", f"clients/{client['id']}", "-r", REALM, *changes])
            print(f"  updated client {client['clientId']}")
    print(f"Keycloak's live '{REALM}' realm clients are in sync.")
