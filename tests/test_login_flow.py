"""
End-to-end OIDC login flow through http://zac.local, replaying the same
redirect chain a browser would make (cookies included via requests.Session)
- this is the strongest verification this project has that ZAC, Keycloak,
and PABC are wired together correctly: it exercises the PKCE realm-client
fix, the PABC role/domain mapping data, and the Django ALLOWED_HOSTS fixes
all at once, not just that each service independently boots.

Test credentials (beheerder1newiam / minikube-test-1234) were created
directly via the Keycloak Admin API against the "beheerders-elk-domein"
group during live verification - dev-cluster-only, not a compose default.
If this test fails with a login/credential error rather than an
infrastructure error, the password may need resetting via that same API
(see plan.md's step 4 notes for the exact admin API calls used).
"""

from urllib.parse import urlparse

import requests

ZAC_HOST = "zac.local"
KEYCLOAK_HOST = "keycloak.local"
TEST_USERNAME = "beheerder1newiam"
TEST_PASSWORD = "minikube-test-1234"


def _via_traefik(traefik_ip, absolute_url):
    """
    Turn an absolute "http://<some>.local/path?query" redirect target into
    a (url, headers) pair that reaches it through Traefik by IP + Host
    header, instead of relying on DNS/`/etc/hosts` resolving *.local.
    """
    parsed = urlparse(absolute_url)
    url = f"http://{traefik_ip}{parsed.path}"
    if parsed.query:
        url += f"?{parsed.query}"
    return url, {"Host": parsed.netloc}


def test_full_login_flow_reaches_authenticated_app(traefik_ip):
    session = requests.Session()

    # 1. Unauthenticated request to ZAC redirects to Keycloak's real OIDC
    #    authorization endpoint.
    initial = session.get(
        f"http://{traefik_ip}/",
        headers={"Host": ZAC_HOST},
        timeout=10,
        allow_redirects=False,
    )
    assert initial.status_code == 302, "zac.local should redirect to Keycloak"
    auth_location = initial.headers["Location"]
    assert KEYCLOAK_HOST in auth_location
    assert "response_type=code" in auth_location
    assert "client_id=zaakafhandelcomponent" in auth_location

    # 2. Keycloak's auth endpoint renders the real login form (not an error
    #    page - this is exactly what the PKCE realm-client fix made work).
    auth_url, auth_headers = _via_traefik(traefik_ip, auth_location)
    login_page = session.get(auth_url, headers=auth_headers, timeout=10)
    assert login_page.status_code == 200
    assert 'id="kc-form-login"' in login_page.text

    form_action = _extract_form_action(login_page.text)
    assert form_action, "could not find the login form's action URL"

    # 3. Submit credentials - Keycloak should issue an authorization code
    #    and redirect back to zac.local.
    submit_url, submit_headers = _via_traefik(traefik_ip, form_action)
    submitted = session.post(
        submit_url,
        headers=submit_headers,
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
    assert ZAC_HOST in callback_location
    assert "code=" in callback_location

    # 4. Follow the callback - ZAC exchanges the code and redirects to /.
    callback_url, callback_headers = _via_traefik(traefik_ip, callback_location)
    callback = session.get(
        callback_url, headers=callback_headers, timeout=15, allow_redirects=False
    )
    assert callback.status_code == 302

    # 5. Final request should land on the real, authenticated app shell -
    #    not bounced back to login, and not ZAC's own "Geen toestemming"
    #    (403) authorization-denied page.
    final = session.get(
        f"http://{traefik_ip}/", headers={"Host": ZAC_HOST}, timeout=15
    )
    assert final.status_code == 200
    assert "<zac-root>" in final.text
    assert "Geen toestemming" not in final.text


def _extract_form_action(html):
    import html as html_module
    import re

    match = re.search(r'action="([^"]*)"', html)
    return html_module.unescape(match.group(1)) if match else None
