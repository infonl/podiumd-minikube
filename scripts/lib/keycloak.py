"""Changes to the live Keycloak realm that a realm re-import cannot make.

--import-realm skips a realm that already exists, and Keycloak persists to
Postgres, so a changed vendored realm never reaches a provisioned cluster.
The same fixes as lib.manifests.fix_realm are applied to the live clients;
clients, redirect URIs, protocol mappers, client roles and top-level groups
added to the vendored realm are added, never removed.
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
# The podiumd chart's realm config declares it: ita/kiss map it into a claim,
# and users may see but not edit it.
SAMACCOUNTNAME = {
    "name": "samaccountname",
    "displayName": "${samaccountname}",
    "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
    "multivalued": False,
}


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
    fields = "id,clientId,redirectUris,webOrigins,attributes,protocolMappers(name)"
    result = process.run([*_kcadm(pod), "get", "clients", "-r", REALM, "--fields", fields], check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout) or None


def client_changes(client: dict[str, Any], vendored: dict[str, Any], *, zac_pkce: bool) -> list[str]:
    """kcadm `-s` settings that bring client in line with vendored and lib.manifests.fix_realm; [] when it is."""
    changes: list[str] = []
    for field in ("redirectUris", "webOrigins"):
        current: list[str] = client.get(field) or []
        wanted = manifests.with_https(current + [uri for uri in vendored.get(field, []) if uri not in current])
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


def missing_by_name(live: list[dict[str, Any]], vendored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The vendored protocol mappers or roles whose name is not live, without their ids."""
    present = {item.get("name") for item in live}
    return [
        {key: value for key, value in item.items() if key not in ("id", "containerId")}
        for item in vendored
        if item.get("name") not in present
    ]


def profile_with_samaccountname(profile: dict[str, Any]) -> dict[str, Any] | None:
    """profile with SAMACCOUNTNAME appended; None when it already has it."""
    attributes: list[dict[str, Any]] = profile.get("attributes", [])
    if any(attribute.get("name") == SAMACCOUNTNAME["name"] for attribute in attributes):
        return None
    return {**profile, "attributes": [*attributes, SAMACCOUNTNAME]}


def _sync_client_parts(pod: str, client: dict[str, Any], vendored: dict[str, Any], roles: list[dict[str, Any]]) -> None:
    """Adds the vendored protocol mappers and client roles that client lacks."""
    path = f"clients/{client['id']}"
    for mapper in missing_by_name(client.get("protocolMappers") or [], vendored.get("protocolMappers", [])):
        process.output(
            [*_kcadm(pod), "create", f"{path}/protocol-mappers/models", "-r", REALM, "-f", "-"],
            stdin=json.dumps(mapper),
        )
        print(f"  added mapper {mapper['name']} to client {client['clientId']}")
    if roles:
        live = json.loads(process.output([*_kcadm(pod), "get", f"{path}/roles", "-r", REALM, "--fields", "name"]))
        for role in missing_by_name(live, roles):
            process.output([*_kcadm(pod), "create", f"{path}/roles", "-r", REALM, "-f", "-"], stdin=json.dumps(role))
            print(f"  added role {role['name']} to client {client['clientId']}")


def _sync_groups(pod: str, vendored: list[dict[str, Any]]) -> None:
    """Creates the vendored top-level groups the live realm lacks, with their client roles."""
    live = json.loads(process.output([*_kcadm(pod), "get", "groups", "-r", REALM, "--fields", "name"]))
    for group in missing_by_name(live, vendored):
        process.output([*_kcadm(pod), "create", "groups", "-r", REALM, "-s", f"name={group['name']}"])
        client_roles: dict[str, list[str]] = group.get("clientRoles") or {}
        for client_id, roles in client_roles.items():
            role_args = [arg for role in roles for arg in ("--rolename", role)]
            process.output(
                [*_kcadm(pod), "add-roles", "-r", REALM, "--gname", group["name"], "--cclientid", client_id, *role_args]
            )
        print(f"  created group {group['name']}")


def sync_realm(*, zac_pkce: bool) -> None:
    """Applies the realm fixes to the live clients."""
    if not kube.exists("deployment/keycloak"):
        print("'keycloak' not found - skipping the realm sync.")
        return
    print(f"Reconciling Keycloak's live '{REALM}' realm clients...")
    # A changed Keycloak spec rolls out a new pod; exec into the new one only.
    kube.kubectl_shown("rollout", "status", "deployment/keycloak", "-n", NAMESPACE, "--timeout=300s")
    pod = kube.first_pod("app=keycloak")
    clients = polling.wait_until(lambda: _clients(pod), timeout=STARTUP_TIMEOUT, interval=3)
    if not clients:
        print(
            f"WARNING: Keycloak realm '{REALM}' not reachable after {STARTUP_TIMEOUT}s; "
            "realm not synced - re-run deploy once Keycloak is up.",
            file=sys.stderr,
        )
        return
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    vendored = {client["clientId"]: client for client in realm["clients"]}
    created = missing_clients(clients, list(vendored.values()))
    for client in created:
        process.output([*_kcadm(pod), "create", "clients", "-r", REALM, "-f", "-"], stdin=json.dumps(client))
        print(f"  created client {client['clientId']}")
    if created:
        clients = _clients(pod) or clients
    client_roles: dict[str, list[dict[str, Any]]] = realm["roles"]["client"]
    for client in clients:
        wanted = vendored.get(client["clientId"], {})
        changes = client_changes(client, wanted, zac_pkce=zac_pkce)
        if changes:
            process.output([*_kcadm(pod), "update", f"clients/{client['id']}", "-r", REALM, *changes])
            print(f"  updated client {client['clientId']}")
        _sync_client_parts(pod, client, wanted, client_roles.get(client["clientId"], []))
    profile = profile_with_samaccountname(
        json.loads(process.output([*_kcadm(pod), "get", "users/profile", "-r", REALM]))
    )
    if profile:
        process.output([*_kcadm(pod), "update", "users/profile", "-r", REALM, "-f", "-"], stdin=json.dumps(profile))
        print("  declared user attribute samaccountname")
    _sync_groups(pod, realm.get("groups", []))
    print(f"Keycloak's live '{REALM}' realm clients are in sync.")
