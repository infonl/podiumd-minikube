"""
End-to-end OIDC login flow through https://zac.local, replaying the same
redirect chain a browser would make (cookies included via requests.Session)
- this is the strongest verification this project has that ZAC, Keycloak,
and PABC are wired together correctly: it exercises the PKCE realm-client
fix, the PABC role/domain mapping data, and the Django ALLOWED_HOSTS fixes
all at once, not just that each service independently boots.

Test credentials (beheerder1newiam / beheerder1newiam) are baked into the
vendored realm import itself
(vendor/dimpact-zaakafhandelcomponent/keycloak/zaakafhandelcomponent-realm.json)
- both the user (in the "beheerders-elk-domein" group) and its password
credential hash are part of that file, re-imported identically by Keycloak
on every startup (confirmed live: the user's own createdTimestamp is
identical across fresh deploys - it's baked into the JSON, not persisted
state that happens to survive). If this test ever fails with a
login/credential error rather than an infrastructure error, check that
file for what the real password is - resetting it against a *live*
Keycloak via the Admin API is only a temporary fix, silently undone by the
next fresh import.
"""

import requests

from conftest import CA_FILE

ZAC_URL = "https://zac.local/"
ZAC_HOST = "zac.local"
KEYCLOAK_HOST = "keycloak.local"
TEST_USERNAME = "beheerder1newiam"
TEST_PASSWORD = "beheerder1newiam"


def test_full_login_flow_reaches_authenticated_app(traefik_ip):
    session = requests.Session()
    session.verify = CA_FILE

    # 1. Unauthenticated request to ZAC redirects to Keycloak's real OIDC
    #    authorization endpoint, on https.
    initial = session.get(ZAC_URL, timeout=10, allow_redirects=False)
    assert initial.status_code == 302, "zac.local should redirect to Keycloak"
    auth_location = initial.headers["Location"]
    assert auth_location.startswith(f"https://{KEYCLOAK_HOST}/")
    assert "response_type=code" in auth_location
    assert "client_id=zaakafhandelcomponent" in auth_location

    # 2. Keycloak's auth endpoint renders the real login form (not an error
    #    page - this is exactly what the PKCE realm-client fix made work).
    login_page = session.get(auth_location, timeout=10)
    assert login_page.status_code == 200
    assert 'id="kc-form-login"' in login_page.text

    form_action = _extract_form_action(login_page.text)
    assert form_action, "could not find the login form's action URL"

    # 3. Submit credentials - Keycloak should issue an authorization code
    #    and redirect back to zac.local.
    submitted = session.post(
        form_action,
        data={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "credentialId": "",
        },
        timeout=10,
        allow_redirects=False,
    )
    assert submitted.status_code == 302, (
        "login form submission should redirect with an authorization code "
        "- a non-redirect response here usually means the credentials are "
        "wrong (see this module's docstring for how to reset them)"
    )
    callback_location = submitted.headers["Location"]
    assert callback_location.startswith(f"https://{ZAC_HOST}/")
    assert "code=" in callback_location

    # 4. Follow the callback - ZAC exchanges the code and redirects to /.
    callback = session.get(callback_location, timeout=15, allow_redirects=False)
    assert callback.status_code == 302

    # 5. Final request should land on the real, authenticated app shell -
    #    not bounced back to login, and not ZAC's own "Geen toestemming"
    #    (403) authorization-denied page.
    final = session.get(ZAC_URL, timeout=15)
    assert final.status_code == 200
    assert "<zac-root>" in final.text
    assert "Geen toestemming" not in final.text


def _extract_form_action(html):
    import html as html_module
    import re

    match = re.search(r'action="([^"]*)"', html)
    return html_module.unescape(match.group(1)) if match else None
