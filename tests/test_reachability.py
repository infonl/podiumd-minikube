"""
External reachability via the edge, replaying the same Host-header-based
curl checks used throughout manual verification of this chart.

Expected status codes:
  - 302 for zac.local/keycloak.local (both redirect - ZAC to Keycloak's
    OIDC auth endpoint, Keycloak's own root to its admin console)
  - 401 for ita.local (see its entry)
  - 200 for everything else (each app's own home/API root page)
"""

import pytest
import requests

from conftest import host_url

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
    ("openinwoner.local", 200, "openinwoner"),
    ("ita.local", 401, "ita"),  # like PABC: 401 to a request that is not a page navigation
    ("contact.local", 200, "kiss"),
    ("referentielijsten.local", 200, "referentielijsten"),
    ("openbeheer.local", 200, "openbeheer"),
    ("mailpit.local", 200, None),
]


@pytest.mark.parametrize(("hostname", "expected_status", "profile"), HOSTS)
def test_ingress_host_reachable(edge_ip, enabled_profiles, hostname, expected_status, profile):
    if profile is not None and not enabled_profiles.get(profile):
        pytest.skip(f"'{profile}' profile is not deployed")
    response = requests.get(
        host_url(hostname),
        timeout=10,
        allow_redirects=False,
    )
    assert response.status_code == expected_status


def test_openformulieren_admin_login_reachable(edge_ip, enabled_profiles):
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
        host_url("openformulieren-nginx.local", "/admin/login/"),
        timeout=10,
    )
    assert response.status_code == 200
