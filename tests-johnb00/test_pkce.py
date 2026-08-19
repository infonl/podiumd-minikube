"""
PKCE checks - drastically reduced from ../tests/test_pkce.py.

The reference file represents extensive source-level research (which
version of which OIDC library supports PKCE, for every one of ~10 Keycloak
clients) that this adaptation does not attempt to re-derive for johnb00's
own chart version/library versions within this session's time budget - that
would be its own significant investigation, not a mechanical port.

What IS checked here is purely empirical, verified live against johnb00's
actual Keycloak realm before writing this file: none of the checked
clients currently has `pkce.code.challenge.method` set to anything at all
(confirmed via the Admin API for "zac" and "pabc" - both attribute maps
came back without that key). So the only thing this module currently
guards is that state not silently changing - if a client starts requiring
PKCE without a corresponding, verified app-side capability check (the kind
of investigation the reference file's docstring describes), that's a
"someone changed a config live" signal worth catching, not something to
assume is fine.

pabc's own hardcoded `UsePkce = true` (ASP.NET Core middleware, confirmed
by reading its source - see ../tests/test_pkce.py's own docstring, same
app/version story applies to any environment including johnb00) is
independent of environment and IS still meaningfully checked below: pabc
sends a code_challenge whether or not Keycloak requires one.

Not ported: the full pabc login round trip (needs a known-valid PABC user
credential - none available for johnb00, see this session's report), the
zac.experimentalPkce check (that flag doesn't exist in podiumd-infra's
usage), and the full ita/kiss/Django-app-by-app PKCE-support matrix (would
need the same from-source library investigation as the reference file,
not attempted here).
"""

import requests

from conftest import kubectl, NAMESPACE

PABC_HOST = "pabc.johnb00.pd.test-rig.nl"
KEYCLOAK_HOST = "keycloak.johnb00.pd.test-rig.nl"
REALM = "podiumd"

# Every client checked live before writing this file - confirmed none
# currently has pkce.code.challenge.method set.
CHECKED_CLIENT_IDS = ("zac", "pabc", "openzaak", "openklant", "objecten", "objecttypen")


def _keycloak_admin_token():
    username = kubectl(
        "get", "secret", "keycloak-podiumd-admin", "-n", NAMESPACE,
        "-o", "jsonpath={.data.username}",
    )
    password = kubectl(
        "get", "secret", "keycloak-podiumd-admin", "-n", NAMESPACE,
        "-o", "jsonpath={.data.password}",
    )
    import base64

    response = requests.post(
        f"https://{KEYCLOAK_HOST}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": base64.b64decode(username).decode(),
            "password": base64.b64decode(password).decode(),
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def test_pabc_challenge_always_sends_a_pkce_code_challenge():
    """PABC's own ASP.NET Core middleware sends a real code_challenge
    unconditionally, regardless of whether Keycloak requires one - see
    this module's own docstring."""
    from urllib.parse import parse_qs, urlparse

    challenge = requests.get(
        f"https://{PABC_HOST}/api/challenge",
        params={"returnUrl": "/"},
        timeout=10,
        allow_redirects=False,
    )
    assert challenge.status_code == 401
    auth_location = challenge.headers["Location"]
    assert KEYCLOAK_HOST in auth_location
    assert "client_id=pabc" in auth_location

    params = parse_qs(urlparse(auth_location).query)
    assert params.get("code_challenge_method") == ["S256"]
    assert len(params.get("code_challenge", [""])[0]) > 0


def test_no_client_currently_requires_pkce():
    """
    Regression guard, not a from-source capability verification (see this
    module's own docstring for why): every checked client's Keycloak
    `pkce.code.challenge.method` attribute is empty/absent, matching what
    was confirmed live before this file was written. If this starts
    failing, someone (or some future re-deploy) changed that live - worth
    re-running the kind of per-app library investigation the ../tests/
    reference file documents before assuming the app side can actually
    handle it.
    """
    token = _keycloak_admin_token()
    offenders = []
    for client_id in CHECKED_CLIENT_IDS:
        response = requests.get(
            f"https://{KEYCLOAK_HOST}/admin/realms/{REALM}/clients",
            headers={"Authorization": f"Bearer {token}"},
            params={"clientId": client_id},
            timeout=10,
        )
        response.raise_for_status()
        clients = response.json()
        if not clients:
            continue
        method = clients[0].get("attributes", {}).get("pkce.code.challenge.method", "")
        if method:
            offenders.append(f"{client_id}: {method}")
    assert not offenders, (
        f"client(s) now require PKCE that didn't before: {offenders} - "
        "verify the corresponding app can actually send a code_challenge "
        "before treating this as expected"
    )
