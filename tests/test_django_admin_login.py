"""
Django-admin credential logins across this stack's Maykin-family apps.

Common root cause behind every fix here: these charts' production/docker
settings chain defaults SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE to True
(driven by an IS_HTTPS-style env var, either a dedicated
settings.isHttps values.yaml field or, where the chart doesn't expose one,
set directly via extraEnvVars) - the browser silently drops both cookies
over our plain http://*.local ingress, and login fails as a CSRF error
before a single credential is even checked. See each app's own
values.yaml comment (isHttps / extraEnvVars) for the exact mechanism used.

The login form itself is always shaped like a django-two-factor-auth
wizard (extra hidden "admin_login_view-current_step" field - a plain POST
without it 400s with "ManagementForm data is missing or has been
tampered with"), handled uniformly by _login() below. Every app here also
genuinely *enforces* a second factor for a fresh superuser with no
registered device - confirmed live per app - needing either the vendored
docker_no2fa.py shim (MAYKIN_2FA_ALLOW_MFA_BYPASS_BACKENDS) or, for
openklant specifically, a plain settings.disable2fa=true (that app's
shared open_api_framework base library reads DISABLE_2FA directly in its
real production settings chain, unlike the others); see each app's own
values.yaml comment for which.

Several apps here also have no working superuser-creation mechanism of
their own at all (openformulieren/openklant/openarchiefbeheer - confirmed
by grepping every template in each vendored chart for "superuser": zero
hits beyond a commented-out example) - each gets a custom idempotent Job
(templates/<app>/create-superuser-job.yaml) instead.
"""

import re

import pytest
import requests

from conftest import host_url, host_headers


def _login(traefik_ip, hostname, username, password):
    session = requests.Session()
    headers = {**host_headers(hostname), "Referer": f"http://{hostname}/admin/login/"}

    # GET /admin/login/ and follow wherever it redirects (openformulieren
    # splits into a separate /admin/classic-login/?next=/admin/ view) - the
    # POST below must target that final URL, not the original one, or it
    # 404s/re-redirects instead of submitting the form.
    login_page = session.get(
        host_url(traefik_ip, "/admin/login/"), headers=headers, timeout=10
    )
    assert login_page.status_code == 200
    login_url = login_page.url

    csrf_match = re.search(
        r'name="csrfmiddlewaretoken" value="([^"]+)"', login_page.text
    )
    assert csrf_match, f"{hostname}: login page did not render a CSRF token"
    step_match = re.search(
        r'name="admin_login_view-current_step" value="([^"]+)"', login_page.text
    )
    assert step_match, (
        f"{hostname}: login page did not render the two-factor wizard's "
        "step field - did the form shape change?"
    )
    # Some apps (openformulieren) pre-fill this from a ?next= query param
    # on the redirected URL - extract it rather than hardcoding "", or
    # submitting it empty can land on a different page post-login.
    next_match = re.search(r'name="next" value="([^"]*)"', login_page.text)
    next_value = next_match.group(1) if next_match else ""

    return session.post(
        login_url,
        headers={
            **headers,
            "Origin": f"http://{hostname}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "csrfmiddlewaretoken": csrf_match.group(1),
            "admin_login_view-current_step": step_match.group(1),
            "auth-username": username,
            "auth-password": password,
            "next": next_value,
        },
        timeout=10,
    )


def _assert_logged_in(response, username, hostname):
    assert response.status_code == 200, (
        f"{hostname}: login submission failed - a 403 usually means the "
        "IS_HTTPS/cookie fix regressed, a 400 usually means the "
        "two-factor wizard step field is missing/wrong"
    )
    body = re.sub(r"\s+", " ", response.text)
    # Not '/admin/logout/' or 'id="logout-form"': openarchiefbeheer's admin
    # renders a plain Dutch-locale GET link ("Afmelden") instead of the
    # POST form the other three use - so absence of the login form itself
    # is the one signal that's actually universal across locale/template.
    assert f"<strong>{username}</strong>" in body and 'name="auth-username"' not in body, (
        f"{hostname}: landed on a page that doesn't look like the "
        "logged-in admin dashboard - check for a wrong-credentials error, "
        "or a regressed docker_no2fa.py shim forcing a "
        "'Tweestapsauthenticatie instellen' (set up two-factor) wall "
        "instead"
    )


def test_objecttypen_admin_login(traefik_ip, enabled_profiles):
    """
    Only meaningful for the "classic" podiumd shape, where objecttypen is
    its own subchart (see scripts/lib/detect-objecten-shape.sh). Once
    podiumd merges objecten+objecttypen into a single "openobject" chart,
    this subchart - and its ingress/Deployment - stop existing entirely,
    so this test skips whenever `enabled_profiles` doesn't report a live
    `objecttypen` pod - covers the profile being off *and* the
    openobject/merged shape being active, both cases with no objecttypen
    Deployment to test against.

    Needs the same docker_no2fa.py shim as openzaak/opennotificaties below
    (see values.yaml's objecttypen.settings.djangoSettingsModule comment
    for why - and for the invalid manual test that briefly suggested
    otherwise).
    """
    if not enabled_profiles.get("objecttypen"):
        pytest.skip(
            "'objecttypen' has no live pod - either the objecten profile "
            "is off, or podiumd is on the merged 'openobject' shape "
            "(no separate objecttypen subchart to test against either way)"
        )
    response = _login(traefik_ip, "objecttypen.local", "admin", "admin")
    _assert_logged_in(response, "admin", "objecttypen.local")


def test_openzaak_admin_login(traefik_ip):
    """
    Always-on core app - credentials match compose's
    OPENZAAK_SUPERUSER_USERNAME/DJANGO_SUPERUSER_PASSWORD exactly (see
    values.yaml's openzaak.configuration.superuser comment). Needs both
    settings.isHttps=false and the vendored docker_no2fa.py shim
    (settings.djangoSettingsModule) to reach the dashboard at all -
    confirmed live that without the shim, DISABLE_2FA has no effect (only
    read by openzaak's own conf.dev/conf.ci, not the production chain)
    and a fresh superuser with no registered device is forced into 2FA
    device setup instead of being granted admin access.
    """
    response = _login(traefik_ip, "openzaak.local", "admin", "admin")
    _assert_logged_in(response, "admin", "openzaak.local")


def test_opennotificaties_admin_login(traefik_ip, enabled_profiles):
    """
    Optional profile - credentials match compose's
    OPENNOTIFICATIES_SUPERUSER_USERNAME/DJANGO_SUPERUSER_PASSWORD exactly.
    Same isHttps + docker_no2fa.py shim fix as openzaak, and the same
    confirmed-live 2FA enforcement for a fresh superuser.
    """
    if not enabled_profiles.get("opennotificaties"):
        pytest.skip("'opennotificaties' profile is not deployed")
    response = _login(traefik_ip, "opennotificaties.local", "admin", "admin")
    _assert_logged_in(response, "admin", "opennotificaties.local")


def test_openformulieren_admin_login(traefik_ip, enabled_profiles):
    """
    Optional profile - unlike the other three, this chart's own
    configuration.superuser field is wired to nothing at all (confirmed
    live: no template in the vendored chart references "superuser" outside
    a commented-out example), so the admin account is instead created by
    a custom Job (templates/openformulieren/create-superuser-job.yaml,
    idempotent get_or_create + set_password). Also needs the same
    isHttps + docker_no2fa.py shim fix as the others - same confirmed-live
    2FA enforcement for a fresh superuser.

    /admin/login/ redirects here to /admin/classic-login/?next=/admin/
    (this app splits OIDC and classic login into separate views) -
    _login()'s redirect-following handles that.
    """
    if not enabled_profiles.get("openformulieren"):
        pytest.skip("'openformulieren' profile is not deployed")
    response = _login(traefik_ip, "openformulieren-nginx.local", "admin", "admin")
    _assert_logged_in(response, "admin", "openformulieren-nginx.local")


def test_openklant_admin_login(traefik_ip):
    """
    Always-on core app - like openformulieren, this chart's own
    configuration.superuser field doesn't exist at all (confirmed live: no
    template in the vendored chart references "superuser" outside a
    commented-out example), so the admin account is instead created by a
    custom Job (templates/openklant/create-superuser-job.yaml). Unlike
    every other app here, no docker_no2fa.py shim is needed: openklant's
    conf/base.py wildcard-imports from maykinmedia's shared
    open_api_framework library, which reads DISABLE_2FA directly in the
    real production settings chain (confirmed live by reading that
    installed package's own source inside the running pod) - a plain
    settings.disable2fa=true is enough.
    """
    response = _login(traefik_ip, "openklant.local", "admin", "admin")
    _assert_logged_in(response, "admin", "openklant.local")


def test_openarchiefbeheer_admin_login(traefik_ip, enabled_profiles):
    """
    Optional profile - the cookie/2FA fixes (settings.cookie.*,
    djangoSettingsModule) were already done in the original build; only
    the superuser account was ever missing (same "configuration.superuser
    wired to nothing" gap as openformulieren/openklant, confirmed live the
    same way). Real docker-compose never solves this either - its own
    environment block has a commented-out attempt
    (DJANGO_SETTINGS_MODULE=openarchiefbeheer.conf.dev + DISABLE_2FA=true)
    with a comment saying it "errors with `ModuleNotFoundError: No module
    named 'debug_toolbar'`" and gives up, leaving 2FA enforced in real
    compose too - the vendored docker_no2fa.py here is this project's own
    fix, not something replicated from upstream.

    This app's admin renders a Dutch-locale GET logout link ("Afmelden"),
    not the POST `id="logout-form"` the other three use - _assert_logged_in
    checks for the login form's absence instead, which is universal.
    """
    if not enabled_profiles.get("openarchiefbeheer"):
        pytest.skip("'openarchiefbeheer' profile is not deployed")
    response = _login(traefik_ip, "openarchiefbeheer-web.local", "admin", "admin")
    _assert_logged_in(response, "admin", "openarchiefbeheer-web.local")
