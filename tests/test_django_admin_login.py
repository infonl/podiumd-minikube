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
registered device - confirmed live per app - needing the same vendored
docker_no2fa.py shim (MAYKIN_2FA_ALLOW_MFA_BYPASS_BACKENDS); see each
app's own values.yaml settings.djangoSettingsModule comment.
"""

import re

import pytest
import requests

from conftest import host_url, host_headers


def _login(traefik_ip, hostname, username, password):
    session = requests.Session()
    login_url = host_url(traefik_ip, "/admin/login/")
    headers = {**host_headers(hostname), "Referer": f"http://{hostname}/admin/login/"}

    login_page = session.get(login_url, headers=headers, timeout=10)
    assert login_page.status_code == 200

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
            "next": "",
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
    assert f"<strong>{username}</strong>" in body and 'id="logout-form"' in body, (
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
