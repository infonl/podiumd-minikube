"""
PKCE (RFC 9700) verification for the two Keycloak clients that have it
actually enabled - see values.yaml's own podiumd.pabc.settings.oidc.pkceEnabled
and podiumd.openzaak.configuration.data comments, and
vendor/dimpact-zaakafhandelcomponent/NOTES.md's entries on both, for the
full story of what's enabled, what isn't, and why.

pabc first, since it's the one that's actually safe end to end (its own
ASP.NET Core middleware already sends a code_challenge unconditionally -
enforcing it on the Keycloak side was confirmed live not to break
anything). zac is the deliberate negative case: its client's
pkce.code.challenge.method is still "" on purpose (ZAC itself doesn't
support PKCE with the currently-pinned image - see NOTES.md), so this
module also guards that nobody re-enables it by accident before ZAC
actually supports it.
"""

from urllib.parse import parse_qs, urlparse

import requests

PABC_HOST = "pabc.local"
ZAC_HOST = "zac.local"
KEYCLOAK_HOST = "keycloak.local"
PABC_USERNAME = "pabcadmin"
PABC_PASSWORD = "pabcadmin"


def _via_traefik(traefik_ip, absolute_url):
    """
    Turn an absolute "http://<some>.local/path?query" redirect target into
    a (url, headers) pair that reaches it through Traefik by IP + Host
    header, instead of relying on DNS/`/etc/hosts` resolving *.local. Same
    helper as test_login_flow.py's own (kept local rather than shared -
    every test module here is self-contained by convention).
    """
    parsed = urlparse(absolute_url)
    url = f"http://{traefik_ip}{parsed.path}"
    if parsed.query:
        url += f"?{parsed.query}"
    return url, {"Host": parsed.netloc}


def test_pabc_challenge_always_sends_a_pkce_code_challenge(traefik_ip):
    """
    PABC's own ASP.NET Core OpenIdConnect middleware sends a real
    code_challenge/code_challenge_method=S256 unconditionally, regardless
    of whether the Keycloak client actually requires it - confirmed live by
    reading its source directly (PABC.Server/Auth/AuthenticationExtensions.cs
    sets options.UsePkce = true with no configuration surface at all).
    This is what makes enabling enforcement on the Keycloak side safe: the
    app was already doing this before that flag existed.
    """
    challenge = requests.get(
        f"http://{traefik_ip}/api/challenge",
        headers={"Host": PABC_HOST},
        params={"returnUrl": "/"},
        timeout=10,
        allow_redirects=False,
    )
    # Not a browser navigation (see AuthenticationExtensions.cs's own
    # IsBrowserNavigation() check on the Sec-Fetch-Dest header, which a
    # plain `requests` call never sets) - PABC answers 401 with the real
    # authorization URL in Location instead of a 302, by design, for its
    # SPA frontend to read and navigate to itself.
    assert challenge.status_code == 401
    auth_location = challenge.headers["Location"]
    assert KEYCLOAK_HOST in auth_location
    assert "client_id=pabc" in auth_location

    params = parse_qs(urlparse(auth_location).query)
    assert params.get("code_challenge_method") == ["S256"]
    assert len(params.get("code_challenge", [""])[0]) > 0


def test_pabc_pkce_login_accepted_by_keycloak(traefik_ip):
    """
    Full round trip: PABC's own code_challenge, Keycloak's real login form,
    real credentials, and the resulting authorization code all the way
    back to pabc.local/signin-oidc - proves Keycloak's pkce.code.challenge.method:
    S256 enforcement (vendor/dimpact-zaakafhandelcomponent/keycloak/
    zaakafhandelcomponent-realm.json's own "pabc" client) actually validates
    the challenge/verifier pair correctly, not just that the parameter is
    present.

    Deliberately does NOT assert a fully authenticated session afterward
    (no check against /api/me) - confirmed live, a separate and unrelated
    bug (PABC's own CookieSecurePolicy.Always, hardcoded with no config
    override - see values.yaml's own pkceEnabled comment) drops the OIDC
    nonce/correlation cookies over this project's plain-HTTP ingress, so
    the session never actually gets established regardless of PKCE. That's
    a known, documented limitation, not something this test should treat
    as a PKCE regression if it starts "passing" a stricter check later
    without TLS being added.
    """
    session = requests.Session()

    challenge = session.get(
        f"http://{traefik_ip}/api/challenge",
        headers={"Host": PABC_HOST},
        params={"returnUrl": "/"},
        timeout=10,
        allow_redirects=False,
    )
    assert challenge.status_code == 401
    auth_location = challenge.headers["Location"]

    # Keycloak's auth endpoint renders the real login form - not an error
    # page like "Missing parameter: code_challenge_method", which is what
    # a broken PKCE setup (challenge sent, but not accepted) looks like.
    auth_url, auth_headers = _via_traefik(traefik_ip, auth_location)
    login_page = session.get(auth_url, headers=auth_headers, timeout=10)
    assert login_page.status_code == 200
    assert 'id="kc-form-login"' in login_page.text

    form_action = _extract_form_action(login_page.text)
    assert form_action, "could not find the login form's action URL"

    submit_url, submit_headers = _via_traefik(traefik_ip, form_action)
    submitted = session.post(
        submit_url,
        headers=submit_headers,
        data={
            "username": PABC_USERNAME,
            "password": PABC_PASSWORD,
            "credentialId": "",
        },
        timeout=10,
        allow_redirects=False,
    )
    # PABC requests response_mode=form_post (unlike ZAC's default query
    # mode in test_login_flow.py) - a successful login is a 200 HTML page
    # with a JS-auto-submitting <form> POSTing the code to pabc.local, not
    # a 302 with the code in a Location header. A non-form_post 200 (the
    # login page re-rendered with an error) or any other status usually
    # means either the credentials are wrong, or Keycloak rejected the
    # PKCE challenge/verifier pair.
    assert submitted.status_code == 200
    assert "OIDC Form_Post Response" in submitted.text, (
        f"expected Keycloak's form_post auto-submit page, got: {submitted.text[:500]}"
    )
    callback_action = _extract_form_action(submitted.text)
    assert callback_action and PABC_HOST in callback_action
    code = _extract_hidden_input(submitted.text, "code")
    assert code, "no authorization code in the form_post response"

    # Actually POST it through, like the browser's own onload handler
    # would - confirms pabc.local's /signin-oidc callback accepts the code
    # (i.e. the full round trip works, not just that Keycloak issued one).
    callback_url, callback_headers = _via_traefik(traefik_ip, callback_action)
    callback = session.post(
        callback_url,
        headers=callback_headers,
        data={
            "code": code,
            "iss": _extract_hidden_input(submitted.text, "iss"),
            "session_state": _extract_hidden_input(submitted.text, "session_state"),
        },
        timeout=10,
        allow_redirects=False,
    )
    assert callback.status_code in (302, 200), (
        f"pabc.local/signin-oidc rejected the callback: {callback.status_code}"
    )


def test_zac_client_does_not_send_pkce_yet(traefik_ip):
    """
    Guards the *other* side of the same story: ZAC's own Keycloak client
    still has pkce.code.challenge.method: "" on purpose (ZAC itself doesn't
    support PKCE with the currently-pinned image tag - see
    vendor/dimpact-zaakafhandelcomponent/NOTES.md's own entry on this and
    values.yaml's zac.enablePkce comment). If this starts failing, it means
    someone re-enabled it on the Keycloak client without also confirming
    ZAC's image was bumped past PR #6490 and AUTH_ENABLE_PKCE was set -
    doing just the Keycloak side alone breaks every ZAC login outright
    ("invalid_request: Missing parameter: code_challenge_method").
    """
    initial = requests.get(
        f"http://{traefik_ip}/",
        headers={"Host": ZAC_HOST},
        timeout=10,
        allow_redirects=False,
    )
    assert initial.status_code == 302
    auth_location = initial.headers["Location"]
    assert "code_challenge" not in auth_location


def _extract_form_action(html):
    import html as html_module
    import re

    # Case-insensitive: Keycloak's own login form uses lowercase
    # action="...", but its form_post auto-submit page (see
    # test_pabc_pkce_login_accepted_by_keycloak) uses uppercase ACTION="...".
    match = re.search(r'action="([^"]*)"', html, re.IGNORECASE)
    return html_module.unescape(match.group(1)) if match else None


def _extract_hidden_input(html, name):
    import html as html_module
    import re

    match = re.search(
        rf'name="{re.escape(name)}"\s+value="([^"]*)"', html, re.IGNORECASE
    )
    return html_module.unescape(match.group(1)) if match else None
