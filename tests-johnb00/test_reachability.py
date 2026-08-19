"""
External reachability over real HTTPS (Let's Encrypt production certs,
real DNS) - adapted from ../tests/test_reachability.py.

Every (host, expected_status) pair below was verified live with a real
`curl` against https://<host>.johnb00.pd.test-rig.nl/ before being written
here - none of minikube's own expected-status assumptions were carried over
unchanged, since johnb00's actual ingress host -> Service mapping (see
values/johnb00/infra.yaml) differs from minikube's chart-embedded ingress
names in several places (documented per-host below).

Two real, pre-existing infra bugs were found while deriving this table
(not introduced by this test suite, and not asserted as "expected" here -
see this session's report instead of enshrining them as regression
baselines):
  - `abc.johnb00.pd.test-rig.nl` (openarchiefbeheer) -> 404: the app is
    `enabled: false` in values/johnb00/podiumd.yaml, but its Ingress
    (routing to the nonexistent `openarchiefbeheer-nginx` Service) still
    exists. Naturally excluded from HOSTS below since the openarchiefbeheer
    profile is off - no assertion is made about this dangling ingress
    either way.
  - `opennotificaties.johnb00.pd.test-rig.nl` -> 404: values/johnb00/
    infra.yaml's ingress entry for this host points at Service
    `opennotificaties-nginx`, which doesn't exist - opennotificaties'
    Helm release uses `fullnameOverride: notificaties` (see podiumd.yaml),
    so its real Service is just `notificaties`, reachable at
    `notificaties.johnb00.pd.test-rig.nl` instead (asserted below, 200).
    This looks like a stale/copy-pasted infra.yaml entry, not something
    this test suite should paper over by testing the broken host anyway.
"""

import requests
import pytest

from conftest import app_url

# (hostname, expected_status, profile_key or None if always-on)
# Verified live 2026-08-19 against the real johnb00 cluster.
HOSTS = [
    ("zac", 302, None),  # -> Keycloak OIDC authorize endpoint
    ("keycloak", 302, None),  # -> Keycloak's own admin console
    ("openzaak", 200, None),
    ("openklant", 200, None),
    ("pabc", 200, None),
    ("objecten", 200, "objecten"),
    ("objecttypen", 200, "objecttypen"),
    # Real Service is "notificaties" (fullnameOverride) - see this file's
    # own docstring for why the "opennotificaties" hostname itself is
    # excluded (dangling ingress, points at a nonexistent Service).
    ("notificaties", 200, "opennotificaties"),
    # Verified live: root path 403s by design (app-rendered, no demo form
    # imported) - same shape as minikube's own openformulieren entries,
    # confirmed independently rather than assumed to carry over.
    ("formulier", 403, "openformulieren"),
    ("openformulieren", 403, "openformulieren"),
    ("mailpit", 200, None),  # deployed this session - see values/johnb00/mailpit.yaml
]


@pytest.mark.parametrize("hostname,expected_status,profile", HOSTS)
def test_ingress_host_reachable(enabled_profiles, hostname, expected_status, profile):
    if profile is not None and not enabled_profiles.get(profile):
        pytest.skip(f"'{profile}' profile is not deployed")
    response = requests.get(
        app_url(hostname), timeout=10, allow_redirects=False
    )
    assert response.status_code == expected_status


def test_openformulieren_admin_login_reachable(enabled_profiles):
    """openformulieren's own root path (/) 403s by design (no demo form
    imported) - /admin/login/ is the real login surface and should 200."""
    if not enabled_profiles.get("openformulieren"):
        pytest.skip("'openformulieren' profile is not deployed")
    response = requests.get(app_url("formulier", "/admin/login/"), timeout=10)
    assert response.status_code == 200
