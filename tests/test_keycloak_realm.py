"""The wiring values.yaml and manifests.tune_keycloak add to podiumd's Keycloak.

PKCE: no client requires it; zac has it off (as podiumd-infra) and the
Django apps' mozilla-django-oidc-db cannot send it (plan.md).
"""

import base64
import json

import pytest
import requests

from conftest import host_url
from conftest import kubectl

from podiumd_minikube import manifests
from podiumd_minikube.paths import NAMESPACE

REALM = "podiumd"
PKCE_ATTRIBUTE = "pkce.code.challenge.method"
DJANGO_APP_CLIENT_IDS = ("openzaak", "openklant", "objecten", "opennotificaties", "openformulieren", "abc")


def _env(kind, name):
    """The env of the first container of a workload's pod template."""
    spec = json.loads(kubectl("get", kind, name, "-n", NAMESPACE, "-o", "json"))["spec"]["template"]["spec"]
    return {item["name"]: item.get("value") for item in spec["containers"][0].get("env", [])}


@pytest.fixture(scope="module")
def live_clients(edge_ip):
    """The realm's clients by clientId, through the Admin API with the chart's bootstrap admin."""
    secret = json.loads(kubectl("get", "secret", "keycloak-podiumd-admin", "-n", NAMESPACE, "-o", "json"))["data"]
    token = requests.post(
        host_url("keycloak.local", "/realms/master/protocol/openid-connect/token"),
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": base64.b64decode(secret["username"]).decode(),
            "password": base64.b64decode(secret["password"]).decode(),
        },
        timeout=10,
    )
    token.raise_for_status()
    response = requests.get(
        host_url("keycloak.local", f"/admin/realms/{REALM}/clients"),
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        timeout=10,
    )
    response.raise_for_status()
    return {client["clientId"]: client for client in response.json()}


def _pkce(client):
    return (client.get("attributes") or {}).get(PKCE_ATTRIBUTE, "")


@pytest.mark.parametrize(
    ("realm", "issuer"),
    [(REALM, "https://keycloak.local/realms/podiumd"), ("master", "https://keycloak-admin.local/realms/master")],
)
def test_issuer_follows_the_frontend_urls(edge_ip, realm, issuer):
    response = requests.get(host_url("keycloak.local", f"/realms/{realm}/.well-known/openid-configuration"), timeout=10)
    response.raise_for_status()
    assert response.json()["issuer"] == issuer


def test_pkce_requirements(live_clients):
    required = [client_id for client_id in DJANGO_APP_CLIENT_IDS if _pkce(live_clients.get(client_id, {}))]
    assert not required, f"PKCE required for {required}, whose apps do not send it: their logins break"
    # podiumd's zac.auth.enablePkce sets both sides; Keycloak must not require what ZAC does not send.
    sends = json.loads(kubectl("get", "configmap", "zac", "-n", NAMESPACE, "-o", "json"))["data"]["AUTH_ENABLE_PKCE"]
    assert bool(_pkce(live_clients["zac"])) == (sends == "true")


def test_jvm_flags_reach_the_operator_built_pod(edge_ip):
    assert _env("statefulset", "keycloak").get("JAVA_OPTS_APPEND") == manifests.KEYCLOAK_JAVA_OPTS
    operator = _env("deployment", manifests.KEYCLOAK_OPERATOR)
    assert {key: operator.get(key) for key in manifests.KEYCLOAK_OPERATOR_ENV} == manifests.KEYCLOAK_OPERATOR_ENV
