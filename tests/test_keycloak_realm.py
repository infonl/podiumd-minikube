"""The live Keycloak realm matches lib.keycloak.sync_realm's target (Keycloak imports the realm file only once).

PKCE: zaakafhandelcomponent follows zac.experimentalPkce; pabc requires S256;
the Django apps' clients never require it (mozilla-django-oidc-db has no
PKCE support, plan.md).
"""

import json

import pytest
import requests

from conftest import host_url

from lib import keycloak
from lib import values

DJANGO_APP_CLIENT_IDS = (
    "openzaak", "openklant", "objecten", "objecttypen", "opennotificaties", "openformulieren", "openarchiefbeheer",
)  # fmt: skip


@pytest.fixture(scope="module")
def live_clients(edge_ip):
    """The realm's clients by clientId, through the Admin API."""
    token = requests.post(
        host_url("keycloak.local", "/realms/master/protocol/openid-connect/token"),
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": keycloak.ADMIN_USER,
            "password": keycloak.ADMIN_PASSWORD,
        },
        timeout=10,
    )
    token.raise_for_status()
    response = requests.get(
        host_url("keycloak.local", f"/admin/realms/{keycloak.REALM}/clients"),
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        timeout=10,
    )
    response.raise_for_status()
    return {client["clientId"]: client for client in response.json()}


def _pkce(client):
    return (client.get("attributes") or {}).get(keycloak.PKCE_ATTRIBUTE, "")


def test_every_vendored_client_is_live_and_synced(live_clients):
    vendored = json.loads(keycloak.REALM_FILE.read_text(encoding="utf-8"))["clients"]
    assert not keycloak.missing_clients(list(live_clients.values()), vendored), "clients missing: redeploy (sync_realm)"
    by_id = {client["clientId"]: client for client in vendored}
    pending = {
        client_id: changes
        for client_id, client in live_clients.items()
        if (
            changes := keycloak.client_changes(
                client, by_id.get(client_id, {}), zac_pkce=values.zac_experimental_pkce()
            )
        )
    }
    assert not pending, f"live clients differ from the realm sync's target: {pending}"


def test_pkce_requirements(live_clients):
    assert _pkce(live_clients[keycloak.ZAC_CLIENT_ID]) == ("S256" if values.zac_experimental_pkce() else "")
    assert _pkce(live_clients["pabc"]) == "S256"
    required = [client_id for client_id in DJANGO_APP_CLIENT_IDS if _pkce(live_clients[client_id])]
    assert not required, f"PKCE required for {required}, whose mozilla-django-oidc-db cannot send it: logins break"
