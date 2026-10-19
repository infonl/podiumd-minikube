"""podiumd_minikube.keycloak: the realm repair after the chart's import."""

import base64
import json

import pytest

from conftest import FakeRun

from podiumd_minikube import keycloak
from podiumd_minikube import manifests
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.process import UserError

OIDC = {"id": "s-oidc", "protocol": "openid-connect"}
SAML = {"id": "s-saml", "protocol": "saml"}


def test_missing_default_scopes_only_for_chart_clients_without_any():
    clients = [
        {"id": "1", "clientId": "zac", "protocol": "openid-connect", "defaultClientScopes": []},
        {"id": "2", "clientId": "kiss", "protocol": "openid-connect", "defaultClientScopes": ["profile"]},
        {"id": "3", "clientId": "admin-cli", "protocol": "openid-connect"},
        {"id": "4", "clientId": "saml-app", "protocol": "saml"},
        {"id": "5", "clientId": "no-protocol"},
    ]
    assert keycloak.missing_default_scopes(clients, [OIDC, SAML]) == {"1": ["s-oidc"], "4": ["s-saml"], "5": ["s-oidc"]}


def test_kcadm_logs_in_with_the_admin_secret_from_stdin(fake_run: FakeRun):
    secret = {key: base64.b64encode(value.encode()).decode() for key, value in (("username", "a"), ("password", "p"))}
    fake_run.on("kubectl", "get", "secret", keycloak.ADMIN_SECRET, stdout=json.dumps({"data": secret}))
    fake_run.on("kubectl", "exec", stdout="[]")
    assert keycloak.kcadm(["get", "clients", "-r", "podiumd"]) == "[]"
    exec_call = fake_run.ran("kubectl", "exec")[0]
    assert exec_call[:-1] == [
        "kubectl",
        "exec",
        "-i",
        "-n",
        NAMESPACE,
        keycloak.POD,
        "-c",
        "keycloak",
        "--",
        "bash",
        "-c",
    ]
    assert "kcadm.sh get clients -r podiumd --config " in exec_call[-1]
    assert "a" not in exec_call[-1].split()
    assert fake_run.stdins[-1] == "a\np\n"


def _render(*jobs: str) -> manifests.Render:
    return manifests.Render(docs=[{"kind": "Job", "metadata": {"name": name}} for name in jobs], large_configmaps=[])


def test_repair_skips_a_cluster_without_keycloak(fake_run: FakeRun):
    fake_run.on("kubectl", "get", f"pod/{keycloak.POD}", returncode=1)
    keycloak.repair_default_client_scopes(_render())
    assert len(fake_run.calls) == 1


def test_repair_waits_for_the_realm_import(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(keycloak, "IMPORT_TIMEOUT", 0)
    fake_run.on("kubectl", "get", "job", stdout=json.dumps({"status": {}}))
    with pytest.raises(UserError, match=f"^the {keycloak.IMPORT_JOB_PREFIX} Job did not complete"):
        keycloak.repair_default_client_scopes(_render(f"{keycloak.IMPORT_JOB_PREFIX}-1"))


def test_repair_adds_the_realm_defaults_to_clients_without_any(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    clients = [
        {"id": "1", "clientId": "zac", "protocol": "openid-connect"},
        {"id": "2", "clientId": "kiss", "protocol": "openid-connect", "defaultClientScopes": ["profile"]},
    ]
    answers = {
        "default-default-client-scopes": [OIDC, {"id": "s-roles", "protocol": "openid-connect"}],
        "clients": clients,
    }
    commands: list[list[str]] = []

    def kcadm(*given: list[str]) -> str:
        commands.extend(given)
        return json.dumps(answers.get(given[0][1].rsplit("/", 1)[-1], ""))

    monkeypatch.setattr(keycloak, "kcadm", kcadm)
    fake_run.on("kubectl", "get", "job", stdout=json.dumps({"status": {"succeeded": 1}}))
    keycloak.repair_default_client_scopes(_render(f"{keycloak.IMPORT_JOB_PREFIX}-1", "openzaak-config"))
    assert [call[3] for call in fake_run.ran("kubectl", "get", "job")] == [f"{keycloak.IMPORT_JOB_PREFIX}-1"]
    assert commands[2:] == [
        ["update", "clients/1/default-client-scopes/s-oidc", "-r", keycloak.REALM],
        ["update", "clients/1/default-client-scopes/s-roles", "-r", keycloak.REALM],
    ]
