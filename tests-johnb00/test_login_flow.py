"""
End-to-end OIDC login flow through https://zac.johnb00.pd.test-rig.nl -
adapted from ../tests/test_login_flow.py. Real DNS + a real trusted TLS
cert means no Traefik-IP/Host-header replaying is needed - `requests`
follows real redirects to real hostnames directly.

Verified live before writing this: johnb00's realm is "podiumd" (not
minikube's "zaakafhandelcomponent") and ZAC's Keycloak client_id is "zac"
(not "zaakafhandelcomponent") - see the initial redirect's own
`client_id=zac` query param.

Uses the real "johnb00" realm user (see conftest.py's keycloak_test_user
fixture) rather than a baked-in realm-import credential - johnb00's own
realm import doesn't ship a fixed test-user password the way minikube's
vendored realm.json does.

Known limitation, found live: the "johnb00" user has no group/role
memberships (confirmed via the Admin API), so it likely isn't authorized in
PABC's own role/domain mapping - the login flow itself (OIDC redirect ->
form -> code -> callback) is still meaningful to verify end to end, but the
final landing page may be ZAC's "Geen toestemming" (403-equivalent
authorization-denied) page rather than the real dashboard. Both outcomes
are treated as distinct, clearly-labeled results below - only an actual
infra/redirect-chain failure is a hard test failure.
"""

import pytest
import requests

ZAC_HOST = "zac.johnb00.pd.test-rig.nl"
KEYCLOAK_HOST = "keycloak.johnb00.pd.test-rig.nl"


def test_full_login_flow_reaches_authenticated_app(keycloak_test_user):
    username, password = keycloak_test_user
    session = requests.Session()

    # 1. Unauthenticated request to ZAC redirects to Keycloak's real OIDC
    #    authorization endpoint.
    initial = session.get(
        f"https://{ZAC_HOST}/", timeout=10, allow_redirects=False
    )
    assert initial.status_code == 302, "zac should redirect to Keycloak"
    auth_location = initial.headers["Location"]
    assert KEYCLOAK_HOST in auth_location
    assert "response_type=code" in auth_location
    assert "client_id=zac" in auth_location

    # 2. Keycloak's auth endpoint renders the real login form.
    login_page = session.get(auth_location, timeout=10)
    assert login_page.status_code == 200
    assert 'id="kc-form-login"' in login_page.text

    form_action = _extract_form_action(login_page.text)
    assert form_action, "could not find the login form's action URL"

    # 3. Submit credentials - Keycloak should issue an authorization code
    #    and redirect back to zac.
    submitted = session.post(
        form_action,
        data={
            "username": username,
            "password": password,
            "credentialId": "",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert submitted.status_code == 302, (
        "login form submission should redirect with an authorization code "
        "- a non-redirect response here usually means the credentials are "
        "wrong (see conftest.py's keycloak_test_user fixture for how the "
        "password was set, and tests-johnb00/README.md to reset it)"
    )
    callback_location = submitted.headers["Location"]
    assert ZAC_HOST in callback_location
    assert "code=" in callback_location

    # 4. Follow the callback - ZAC exchanges the code and redirects to /.
    callback = session.get(
        callback_location, timeout=15, allow_redirects=False
    )
    assert callback.status_code == 302

    # 5. Final request should land on the real app shell - not bounced
    #    back to login. It may instead be a 403 (confirmed live: ZAC's own
    #    PABC-authorization-denied response) if this user has no PABC
    #    role mapping (see this module's docstring) - that's a distinct,
    #    separately-flagged outcome, not an infra failure, since the OIDC
    #    round trip itself already succeeded by this point.
    final = session.get(f"https://{ZAC_HOST}/", timeout=15)
    if final.status_code == 403:
        pytest.skip(
            "OIDC login round trip succeeded end to end (redirect, form, "
            "code, callback all worked), but ZAC returned 403 - the "
            "'johnb00' user isn't authorized in PABC's own role/domain "
            "mapping (Keycloak shows no group/role assignment for this "
            "user) - not an infra/redirect-chain problem"
        )
    assert final.status_code == 200
    assert "<zac-root>" in final.text


def _extract_form_action(html):
    import html as html_module
    import re

    match = re.search(r'action="([^"]*)"', html)
    return html_module.unescape(match.group(1)) if match else None
