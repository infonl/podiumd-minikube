"""
Verifies ZAC's own zaakafhandelparameters are "valide" for the zaaktypen
that exist on johnb00 - i.e. that ZAC can actually be used to start a zaak
of that type, not just that the zaaktype is published in Open Zaak's own
Catalogi API.

Background (found live this session): a published, resultaattype-having
zaaktype is still unusable from ZAC until this ZAC-side config exists too
(ZaaktypeCmmnConfiguration.isValide() in the ZAC source requires a group,
a case definition, and a "niet ontvankelijk" resultaattype). Fixed by
seeding it via scripts/seed-test-data/lib/seed_zac_zaakafhandelparameters.py
(wired into scripts/seed-test-data/modules/catalogus.sh) - this test
guards against that regressing silently on a future reseed/redeploy.

No equivalent in ../tests/ - podiumd-minikube's own zaaktype
("zaaktype-test-1") is seeded through a dedicated Job
(templates/zac/productaanvraag-zaakafhandelparameters-job.yaml), checked
there via test_productaanvraag_flow.py::test_zac_zaaktype_test_1_zaakafhandelparameters_is_valide.
johnb00 has no such Job (that whole productaanvraag flow - a dedicated
Objecttype + Open Formulieren form + notification wiring - doesn't exist
here), so this file checks the same underlying "is it valide" property
directly against the two zaaktypen johnb00 actually has, rather than
porting that Job/flow.
"""

import requests

from conftest import keycloak_test_user

ZAC_HOST = "zac.johnb00.pd.test-rig.nl"

# (zaaktype uuid, human label) - both zaaktypen currently on johnb00:
#   zaaktype-voor-e2e-testen: the chart's own standard e2e-test zaaktype
#     (also what post-deployment-pabc-init-job.yml's PABC mappings target).
#   smoke-zaaktype-1: created by scripts/seed-test-data's own catalogus
#     module (--scale smoke).
ZAAKTYPEN = [
    ("0bc8bc97-92d9-42fc-9a33-e9ffbb082168", "zaaktype-voor-e2e-testen"),
    ("367612a7-5d44-4267-a574-ef4c1d024e80", "smoke-zaaktype-1"),
]


def _authenticated_session(username, password):
    """Same OIDC authorization-code flow as test_login_flow.py - ZAC's own
    /rest endpoints accept the resulting session cookie, and its 'zac'
    Keycloak client has no direct-grant support to shortcut this with."""
    import html
    import re

    session = requests.Session()
    initial = session.get(f"https://{ZAC_HOST}/", timeout=10, allow_redirects=False)
    assert initial.status_code == 302
    login_page = session.get(initial.headers["Location"], timeout=10)
    match = re.search(r'action="([^"]*)"', login_page.text)
    form_action = html.unescape(match.group(1))
    submitted = session.post(
        form_action,
        data={"username": username, "password": password, "credentialId": ""},
        timeout=10,
        allow_redirects=False,
    )
    assert submitted.status_code == 302, "login failed - check keycloak_test_user credentials"
    callback = session.get(submitted.headers["Location"], timeout=15, allow_redirects=False)
    assert callback.status_code == 302
    final = session.get(f"https://{ZAC_HOST}/", timeout=15)
    if final.status_code == 403:
        import pytest

        pytest.skip(
            f"{username!r} isn't authorized in PABC's own role mapping - "
            "see test_login_flow.py's docstring for the same known gap"
        )
    assert final.status_code == 200
    return session


def test_zaakafhandelparameters_valide(keycloak_test_user):
    username, password = keycloak_test_user
    session = _authenticated_session(username, password)

    not_valide = []
    for zaaktype_uuid, label in ZAAKTYPEN:
        response = session.get(
            f"https://{ZAC_HOST}/rest/zaakafhandelparameters/{zaaktype_uuid}", timeout=15
        )
        assert response.status_code == 200, f"{label}: {response.status_code} {response.text[:200]}"
        if not response.json().get("valide"):
            not_valide.append(label)

    assert not not_valide, (
        f"zaakafhandelparameters not valide for: {not_valide} - a zaaktype can be "
        "published in Open Zaak and still be unusable from ZAC itself; re-run "
        "scripts/seed-test-data/seed.sh's catalogus module with "
        "SEED_ZAC_BEHEERDER_USERNAME/PASSWORD set to fix"
    )
