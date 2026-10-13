"""podiumd_minikube.keycloak: the realm repair after the chart's import."""

from podiumd_minikube import keycloak

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
