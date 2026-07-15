"""
External reachability via Traefik, replaying the same Host-header-based
curl checks used throughout manual verification of this chart.

Expected status codes:
  - 302 for zac.local/keycloak.local (both redirect - ZAC to Keycloak's
    OIDC auth endpoint, Keycloak's own root to its admin console)
  - 200 for everything else (each app's own home/API root page)
"""

import requests
import pytest

from conftest import host_url, host_headers

# (hostname, expected_status, profile_key or None if always-on)
HOSTS = [
    ("zac.local", 302, None),
    ("keycloak.local", 302, None),
    ("openzaak.local", 200, None),
    ("openklant.local", 200, None),
    ("pabc.local", 200, None),
    ("solr.local", 302, None),  # Solr's admin UI redirects / -> /solr/
    ("objecten.local", 200, "objecten"),
    ("objecttypen.local", 200, "objecttypen"),
    ("opennotificaties.local", 200, "opennotificaties"),
    ("openarchiefbeheer-web.local", 200, "openarchiefbeheer"),
    ("openarchiefbeheer-ui.local", 200, "openarchiefbeheer"),
    ("openformulieren-nginx.local", 403, "openformulieren"),
    ("openformulieren-web.local", 403, "openformulieren"),
    ("grafana.local", 200, "metrics"),
    ("greenmail.local", 200, "itest"),
]


@pytest.mark.parametrize("hostname,expected_status,profile", HOSTS)
def test_ingress_host_reachable(traefik_ip, enabled_profiles, hostname, expected_status, profile):
    if profile is not None and not enabled_profiles.get(profile):
        pytest.skip(f"'{profile}' profile is not deployed")
    response = requests.get(
        host_url(traefik_ip),
        headers=host_headers(hostname),
        timeout=10,
        allow_redirects=False,
    )
    assert response.status_code == expected_status


def test_openformulieren_admin_login_reachable(traefik_ip, enabled_profiles):
    """
    openformulieren's own root path (/) returns a real, app-rendered 403 -
    expected since no demo form was ever imported (see plan.md's
    explicitly-out-of-scope note), not an infra problem. The actual login
    surface is /admin/, which should redirect through to a real 200 login
    page instead.
    """
    if not enabled_profiles.get("openformulieren"):
        pytest.skip("'openformulieren' profile is not deployed")
    session = requests.Session()
    response = session.get(
        host_url(traefik_ip, "/admin/login/"),
        headers=host_headers("openformulieren-nginx.local"),
        timeout=10,
    )
    assert response.status_code == 200
