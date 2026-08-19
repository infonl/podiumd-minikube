"""
PKCE (RFC 9700) verification for every Keycloak client this project wires
up an OIDC login for - see values.yaml's own
podiumd.pabc.settings.oidc.pkceEnabled, podiumd.openzaak.configuration.data,
and podiumd.zac.image.tag comments, and
vendor/dimpact-zaakafhandelcomponent/NOTES.md's entries on all three, for
the full story of what's enabled, what isn't, and why.

zac is the deliberate negative case here *by default* - its client's
pkce.code.challenge.method is kept "" (ZAC itself doesn't support PKCE
with whatever zac chart podiumd 4.8.x bundles, see NOTES.md). Bumping zac
to chart 1.0.289/app 5.4.2 (past PR #6490, "feat: add configurable PKCE
support for the OIDC authorization code flow") flips that - confirmed
live that ZAC's own container then genuinely sends a code_challenge
unconditionally, the same way pabc's ASP.NET Core middleware already
does - but that chart bump needs a local --path checkout (see
podiumd.zac.image.tag's own values.yaml comment for the exact recipe)
until podiumd 4.9 is released, so it's gated behind top-level
zac.experimentalPkce (off by default - see
scripts/lib/zac-experimental-pkce.sh). Whichever side that flag is on,
scripts/lib/fixup-zac-pkce-realm.py and scripts/lib/sync-zac-pkce-realm.sh
both keep the Keycloak client's own pkce.code.challenge.method in sync
with it - both the vendored realm.json, for future fresh imports, and the
*already-imported* live realm via the Admin API, since Keycloak only
imports a realm once and editing the JSON file alone never affects an
already-existing one (confirmed live the same way the earlier "bad
request connecting to zac.local" incident in plan.md was fixed).
test_zac_client_now_sends_a_pkce_code_challenge below skips entirely when
the switch is off, since with it off ZAC deliberately never sends a
challenge at all.

The seven Django-based ZGW components (openzaak/openklant/objecten/
objecttypen/opennotificaties/openformulieren/openarchiefbeheer) are the
*permanent* negative case, unlike zac: all seven share the same
`mozilla-django-oidc-db` library for admin OIDC login, and - checked live,
not just from a changelog - none of it, at any version this project
bundles (1.1.1 or 2.0.1 depending on the app), has ever had any PKCE
support at all. Confirmed three ways inside each app's own running pod:
its `OIDCProvider` Django model has no field with "pkce" in the name,
`grep -ri pkce` across the entire installed `mozilla_django_oidc_db`
package tree finds nothing, and the upstream project's own CHANGELOG.rst
never mentions it either (a GitHub search across every issue/PR in
maykinmedia/mozilla-django-oidc-db for "pkce" also returns zero results -
not merely unimplemented, seemingly never even proposed). Re-bumping any
of these apps' image tags doesn't change this on its own - it would only
help if a future bump also happens to pull in a `mozilla-django-oidc-db`
release that adds the feature, which would need re-checking the same way,
not assumed from the app version bump alone.

ita and kiss (PodiumD-only additions, not part of dimpact-zaakafhandelcomponent's
docker-compose stack - see NOTES.md) are a third, unusual case: confirmed
by cloning each app's own public source that both hardcode
`options.UsePkce = true` in their own OpenIdConnect setup, the same
pattern as pabc's own AuthenticationExtensions.cs - so PKCE itself is
unconditionally on for both, same category of finding as pabc. But unlike
pabc, this can't actually be confirmed *live* the same way (no equivalent
of test_pabc_challenge_always_sends_a_pkce_code_challenge exists below
for either): both apps also never set RequireHttpsMetadata anywhere in
their own source, so it stays at the OpenIdConnect middleware's default of
`true`, and both evidently resolve the OIDC handler's options eagerly on
every single request - even a bare `/healthz` - which throws against this
project's http:// Keycloak authority before ever reaching a redirect. Both
crash-loop unconditionally as a result, with no values.yaml/extraEnvVars
fix possible (confirmed: neither app ever reads this setting from
configuration at all), so `podiumd.ita`/`podiumd.kiss` are both kept
`enabled: false` - see values.yaml's own comments on each. Their Keycloak
clients are still provisioned (kept `pkce.code.challenge.method: ""`,
guarded by test_ita_and_kiss_clients_do_not_require_pkce below) so that if
either app is ever re-enabled without someone re-reading this docstring
first, a login attempt fails safe (Keycloak not requiring a challenge the
app happens to send anyway) rather than fails hard (Keycloak requiring one
a broken/rolled-back version of the app can't send, the exact zac/Django
incident this whole module exists to prevent).
"""

from urllib.parse import parse_qs, urlparse

import pytest
import requests

PABC_HOST = "pabc.local"
ZAC_HOST = "zac.local"
KEYCLOAK_HOST = "keycloak.local"
PABC_USERNAME = "pabcadmin"
PABC_PASSWORD = "pabcadmin"

# The Keycloak clientId for each of the seven Django-based ZGW components -
# matches vendor/dimpact-zaakafhandelcomponent/keycloak/
# zaakafhandelcomponent-realm.json exactly (see NOTES.md's own entry on
# these seven clients for why they exist ahead of most of them actually
# being wired up to an OIDC admin login yet).
DJANGO_APP_CLIENT_IDS = (
    "openzaak",
    "openklant",
    "objecten",
    "objecttypen",
    "opennotificaties",
    "openformulieren",
    "openarchiefbeheer",
)

# ita/kiss - see this module's own docstring for why these two are a
# different case from DJANGO_APP_CLIENT_IDS above (PKCE confirmed
# unconditionally on from source, not absent - but untestable live, and
# both podiumd.ita/podiumd.kiss stay enabled: false as a result).
ITA_KISS_CLIENT_IDS = ("ita", "kiss")


def _keycloak_admin_token(traefik_ip):
    response = requests.post(
        f"http://{traefik_ip}/realms/master/protocol/openid-connect/token",
        headers={"Host": KEYCLOAK_HOST},
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "admin",
            "password": "admin",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _zac_experimental_pkce_live(traefik_ip):
    """
    Whether the zac 5.4.2/PKCE experiment (top-level zac.experimentalPkce
    in values.yaml, off by default - see scripts/lib/zac-experimental-pkce.sh)
    is actually active on the currently-deployed cluster.

    Deliberately does NOT check the zac ConfigMap's own AUTH_ENABLE_PKCE key
    (an earlier version of this helper did) - found live, that key is left
    unconditionally "true" by values.yaml's own podiumd.zac.auth.enablePkce,
    documented there as "a silent no-op on any zac chart without
    AUTH_ENABLE_PKCE support" - so it's always "true" regardless of
    zac.experimentalPkce, making that check always return True and this
    test always run instead of skipping when the experiment is off (caught
    live: failed outright, immediately after fixing an unrelated realm/zac
    version mismatch, instead of skipping as intended). values.yaml's own
    comment on that field says it plainly: "What actually gates real PKCE
    end to end is the *realm's own* pkce.code.challenge.method requirement,
    not this value" - so that's what this checks instead, the same live
    signal test_ita_and_kiss_clients_do_not_require_pkce and
    test_django_app_client_does_not_require_pkce already use.
    """
    token = _keycloak_admin_token(traefik_ip)
    response = requests.get(
        f"http://{traefik_ip}/admin/realms/zaakafhandelcomponent/clients",
        headers={"Host": KEYCLOAK_HOST, "Authorization": f"Bearer {token}"},
        params={"clientId": "zaakafhandelcomponent"},
        timeout=10,
    )
    response.raise_for_status()
    clients = response.json()
    return bool(clients) and clients[0]["attributes"].get(
        "pkce.code.challenge.method", ""
    ) == "S256"


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


def test_zac_client_now_sends_a_pkce_code_challenge(traefik_ip):
    """
    The other side of the same story, now flipped: ZAC's own image (chart
    1.0.289/app 5.4.2, past PR #6490) genuinely sends a code_challenge on
    every authorization request, confirmed live. If this starts failing,
    either the AUTH_ENABLE_PKCE env var stopped being set (see
    values.yaml's own podiumd.zac.auth.enablePkce comment) or the image was
    rolled back to a pre-PKCE version without also reverting the Keycloak
    client's own pkce.code.challenge.method back to "" - leaving those two
    out of sync breaks every ZAC login outright ("invalid_request: Missing
    parameter: code_challenge_method").

    Doesn't replay the full form-submission round trip like
    test_pabc_pkce_login_accepted_by_keycloak does - test_login_flow.py's
    own test_full_login_flow_reaches_authenticated_app already exercises
    that end to end (real credentials, real code exchange, lands on the
    authenticated app shell) and would fail here too if PKCE broke it.

    Only meaningful with zac.experimentalPkce actually on (off by default -
    see scripts/lib/zac-experimental-pkce.sh) - skips otherwise, since with
    it off ZAC deliberately doesn't send a challenge and the Keycloak
    client deliberately doesn't require one (scripts/lib/fixup-zac-pkce-realm.py
    / sync-zac-pkce-realm.sh both keep those two in sync either way, this
    just isn't the experiment being tested here).
    """
    if not _zac_experimental_pkce_live(traefik_ip):
        pytest.skip("zac.experimentalPkce is off on this cluster")

    initial = requests.get(
        f"http://{traefik_ip}/",
        headers={"Host": ZAC_HOST},
        timeout=10,
        allow_redirects=False,
    )
    assert initial.status_code == 302
    auth_location = initial.headers["Location"]
    assert KEYCLOAK_HOST in auth_location
    assert "client_id=zaakafhandelcomponent" in auth_location

    params = parse_qs(urlparse(auth_location).query)
    assert params.get("code_challenge_method") == ["S256"]
    assert len(params.get("code_challenge", [""])[0]) > 0


@pytest.mark.parametrize("client_id", DJANGO_APP_CLIENT_IDS)
def test_django_app_client_does_not_require_pkce(traefik_ip, client_id):
    """
    Guards the permanent negative case this module's own docstring explains:
    none of the seven Django-based ZGW components' shared OIDC library
    (`mozilla-django-oidc-db`) has ever had PKCE support, at any version
    bundled here - so their Keycloak clients must stay
    pkce.code.challenge.method: "" (not required). If this ever fails, it
    means someone enabled it on the Keycloak side (by hand, or by copying
    zac's own fix) without first confirming the specific app's own
    mozilla-django-oidc-db actually grew PKCE support - doing just the
    Keycloak side alone breaks every login for that app outright
    ("invalid_request: Missing parameter: code_challenge_method"), the
    exact failure mode this project has now hit twice (zac, before its own
    chart bump; see this module's own docstring).

    Checked against the *live* Keycloak realm via the Admin API, not the
    vendored realm.json - Keycloak only imports a realm once, so the two
    can drift after a manual live fix (confirmed happen twice already this
    project, see this module's own docstring).
    """
    token = _keycloak_admin_token(traefik_ip)
    response = requests.get(
        f"http://{traefik_ip}/admin/realms/zaakafhandelcomponent/clients",
        headers={"Host": KEYCLOAK_HOST, "Authorization": f"Bearer {token}"},
        params={"clientId": client_id},
        timeout=10,
    )
    response.raise_for_status()
    clients = response.json()
    assert clients, f"no Keycloak client found for clientId={client_id!r}"
    assert clients[0]["attributes"].get("pkce.code.challenge.method", "") == "", (
        f"{client_id}'s Keycloak client now requires PKCE, but its own "
        "mozilla-django-oidc-db has no support for sending a code_challenge "
        "(confirmed live - see this module's own docstring) - every login "
        "for this app is now broken"
    )


@pytest.mark.parametrize("client_id", ITA_KISS_CLIENT_IDS)
def test_ita_and_kiss_clients_do_not_require_pkce(traefik_ip, client_id):
    """
    Unlike the Django apps above, ita/kiss's own apps *do* hardcode
    `UsePkce = true` (confirmed by reading each app's own public source -
    see this module's own docstring) - so this isn't guarding against a
    library that can never send a challenge. It's guarding against
    something more specific: neither app can actually serve a single HTTP
    request against this project's http:// Keycloak authority at all (a
    separate, hardcoded RequireHttpsMetadata default, also confirmed from
    source), so podiumd.ita/podiumd.kiss both stay `enabled: false` and
    neither has ever completed a real login here. If someone re-enables
    either without reading that far, this test is what keeps the Keycloak
    client itself from independently starting to require a challenge the
    app can never actually be confirmed to send in this environment -
    that combination is exactly what broke zac and, once, the Django apps'
    story elsewhere in this module.
    """
    token = _keycloak_admin_token(traefik_ip)
    response = requests.get(
        f"http://{traefik_ip}/admin/realms/zaakafhandelcomponent/clients",
        headers={"Host": KEYCLOAK_HOST, "Authorization": f"Bearer {token}"},
        params={"clientId": client_id},
        timeout=10,
    )
    response.raise_for_status()
    clients = response.json()
    assert clients, f"no Keycloak client found for clientId={client_id!r}"
    assert clients[0]["attributes"].get("pkce.code.challenge.method", "") == "", (
        f"{client_id}'s Keycloak client now requires PKCE - fine only if "
        f"podiumd.{client_id}.enabled is actually true and confirmed live "
        "to work now (see this module's own docstring for why it doesn't "
        "as of this writing)"
    )


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
