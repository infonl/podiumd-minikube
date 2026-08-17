"""
End-to-end verification of the productaanvraag flow: a form submission ->
Objects API -> Open Notificaties -> ZAC creating a zaak of zaaktype-test-1
automatically, with no manual post-deploy step. This is the same flow
verified live (by hand) while building it - see values.yaml's own
podiumd.zac.productaanvraag comment,
templates/{zac,openformulieren}/productaanvraag-*.yaml, and
scripts/lib/seed-fixtures.sh's own objecttypen loaddata step for the pieces
this module exercises, and .claude/plans/plan.md's own "productaanvraag
flow" entry for the story of how each piece was found and wired.

Every piece here is provisioned purely from values.yaml + this chart's own
scripts/Jobs - no test in this module depends on any state left over from
manual `kubectl exec` verification.
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlparse

import pytest
import requests

from conftest import NAMESPACE, host_url, host_headers, kubectl

OPENZAAK_HOST = "openzaak.local"
OBJECTEN_HOST = "objecten.local"
OBJECTTYPEN_HOST = "objecttypen.local"
ZAC_HOST = "zac.local"
KEYCLOAK_HOST = "keycloak.local"

# Not this suite's usual Traefik ingress host (OBJECTTYPEN_HOST above) -
# confirmed live, Objects API validates a submitted object's "type" URL
# against its own internally-registered zgw_consumers Service's api_root
# (podiumd.objecten.configuration.data's own "objecttypen-api" service in
# values.yaml, the dotted internal Service DNS form - same URLValidator
# story as ZAC's own zgwApis.url), not whatever host the caller used to
# reach Objects API itself. Real form submissions hit this the same way -
# Open Formulieren's own "objecttypes-api" service in values.yaml uses the
# identical dotted host for exactly this reason.
PRODUCTAANVRAAG_OBJECTTYPE_INTERNAL_HOST = "objecttypen.podiumd-minikube"

CATALOGUS_DOMEIN = "ALG"
CATALOGUS_RSIN = "002564440"
ZAAKTYPE_IDENTIFICATIE = "zaaktype-test-1"
PRODUCTAANVRAAGTYPE = "productaanvraag-test-zaaktype-1"
PRODUCTAANVRAAG_OBJECTTYPE_UUID = "021f685e-9482-4620-b157-34cd4003da6b"

# Matches values.yaml's own podiumd.objecten.configuration.data tokenauth
# entry for "open-formulieren" - a fake, fully-public dev/test credential
# like every other one used throughout this project, safe to hardcode here.
OBJECTEN_TOKEN = "fakeOpenFormulierenObjectsToken"

ZGW_JWT_CLIENT_ID = "zac_client"
ZGW_JWT_SECRET = "openzaakZaakafhandelcomponentClientSecret"

BEHEERDER_USERNAME = "beheerder1newiam"
BEHEERDER_PASSWORD = "beheerder1newiam"

# The two custom Jobs this project adds for the pieces no bundled
# setup_configuration mechanism covers (see each one's own template header) -
# checked by name below. The third gap (the productaanvraag objecttype's
# own schema) isn't a Job at all - it's seeded by
# scripts/lib/seed-fixtures.sh, checked directly further down instead
# (test_productaanvraag_objecttype_is_registered_and_published). The four
# subchart-bundled config Jobs (objecten-config/objecttypen-config/
# opennotificaties-config/openformulieren-config) are NOT included here
# either: their own subchart default is ttlSecondsAfterFinished: 0, so
# Kubernetes deletes them within seconds of succeeding (confirmed live) -
# by the time this suite runs, they're already gone regardless of whether
# they worked. Their effects are verified directly instead (the
# opennotificaties kanaal/abonnement check below, the objecttype/token
# checks, and the full-flow test, which couldn't pass at all if any of the
# four hadn't run correctly).
EXPECTED_SUCCEEDED_JOBS = (
    "zac-productaanvraag-zaakafhandelparameters",
    "openformulieren-productaanvraag-form",
)

REQUIRED_PROFILES = ("objecten", "objecttypen", "opennotificaties", "openformulieren")


@pytest.fixture(autouse=True)
def _skip_if_flow_not_deployed(enabled_profiles):
    missing = [p for p in REQUIRED_PROFILES if not enabled_profiles.get(p)]
    if missing:
        pytest.skip(
            f"productaanvraag flow needs {missing} enabled too (this is only "
            "wired up under --full)"
        )


def _zgw_jwt(client_id, secret):
    def b64url(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = {"typ": "JWT", "alg": "HS256"}
    payload = {
        "iss": client_id,
        "iat": int(time.time()),
        "client_id": client_id,
        "user_id": client_id,
        "user_representation": client_id,
    }
    signing_input = b64url(json.dumps(header).encode()) + b"." + b64url(
        json.dumps(payload).encode()
    )
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + b64url(signature)).decode()


def _openzaak_get(traefik_ip, path, **params):
    response = requests.get(
        host_url(traefik_ip, path),
        headers={
            **host_headers(OPENZAAK_HOST),
            "Authorization": f"Bearer {_zgw_jwt(ZGW_JWT_CLIENT_ID, ZGW_JWT_SECRET)}",
            "Accept-Crs": "EPSG:4326",
        },
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _zaaktype_url(traefik_ip):
    catalogi = _openzaak_get(
        traefik_ip,
        "/catalogi/api/v1/catalogussen",
        domein=CATALOGUS_DOMEIN,
        rsin=CATALOGUS_RSIN,
    )
    assert catalogi["count"] > 0, "expected catalogus ALG/002564440 to already exist"
    catalogus_url = catalogi["results"][0]["url"]

    zaaktypen = _openzaak_get(
        traefik_ip,
        "/catalogi/api/v1/zaaktypen",
        catalogus=catalogus_url,
        identificatie=ZAAKTYPE_IDENTIFICATIE,
    )
    assert zaaktypen["count"] > 0, f"expected zaaktype {ZAAKTYPE_IDENTIFICATIE!r} to already exist"
    return zaaktypen["results"][0]["url"]


def _beheerder_token(traefik_ip):
    response = requests.post(
        host_url(traefik_ip, "/realms/zaakafhandelcomponent/protocol/openid-connect/token"),
        headers=host_headers(KEYCLOAK_HOST),
        data={
            "grant_type": "password",
            "client_id": "zaakafhandelcomponent",
            "client_secret": "keycloakZaakafhandelcomponentClientSecret",
            "username": BEHEERDER_USERNAME,
            "password": BEHEERDER_PASSWORD,
            "scope": "openid",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


@pytest.mark.parametrize("job_name", EXPECTED_SUCCEEDED_JOBS)
def test_seed_job_succeeded(job_name):
    """
    The three custom Jobs this flow's own templates additions add (see
    each one's own header comment) - must have actually run to completion,
    not just exist. Unlike the four bundled setup_configuration Jobs (see
    EXPECTED_SUCCEEDED_JOBS's own comment for why those aren't checked this
    way), none of these three set ttlSecondsAfterFinished, so they're
    expected to still be around to check.
    """
    status = kubectl(
        "get", "job", job_name, "-n", NAMESPACE, "-o", "jsonpath={.status.succeeded}"
    ).strip()
    assert status == "1", f"{job_name} has not succeeded (status.succeeded={status!r})"


def _opennotificaties_psql(query):
    postgres_pod = kubectl(
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        "app=postgres",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ).strip()
    return kubectl(
        "exec",
        "-n",
        NAMESPACE,
        postgres_pod,
        "--",
        "psql",
        "-U",
        "opennotificaties",
        "-d",
        "opennotificaties",
        "-t",
        "-A",
        "-c",
        query,
    ).strip()


def test_opennotificaties_has_objecten_kanaal_and_zac_abonnement():
    """
    podiumd.opennotificaties.configuration.data's own setup_configuration -
    the bundled Job that creates this self-deletes within seconds of
    succeeding (ttlSecondsAfterFinished: 0), so its effect is checked
    directly in the database instead (same `kubectl exec` pattern
    test_database.py already uses), rather than the Job itself.
    """
    kanalen = _opennotificaties_psql("SELECT naam FROM datamodel_kanaal;")
    assert "objecten" in kanalen.splitlines()

    callback_urls = _opennotificaties_psql("SELECT callback_url FROM datamodel_abonnement;")
    assert "http://zac.podiumd-minikube/rest/notificaties" in callback_urls.splitlines()


def test_productaanvraag_objecttype_is_registered_and_published(traefik_ip):
    """
    scripts/lib/seed-fixtures.sh's own objecttypen loaddata (+ its
    draft-to-published fixup), verified against the real Objecttypen API
    rather than just trusting the script's exit code.
    """
    response = requests.get(
        host_url(traefik_ip, f"/api/v2/objecttypes/{PRODUCTAANVRAAG_OBJECTTYPE_UUID}"),
        headers={
            **host_headers(OBJECTTYPEN_HOST),
            "Authorization": "Token openFormulierenToObjecttypenToken",
        },
        timeout=15,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Productaanvraag-Dimpact"

    versions = requests.get(
        host_url(traefik_ip, f"/api/v2/objecttypes/{PRODUCTAANVRAAG_OBJECTTYPE_UUID}/versions"),
        headers={
            **host_headers(OBJECTTYPEN_HOST),
            "Authorization": "Token openFormulierenToObjecttypenToken",
        },
        timeout=15,
    )
    assert versions.status_code == 200
    assert any(v["status"] == "published" for v in versions.json()["results"]), (
        "expected at least one published version - "
        "seed-fixtures.sh's own fixup should have flipped every draft "
        "version this fixture defines"
    )


def test_zac_zaaktype_test_1_zaakafhandelparameters_is_valide(traefik_ip):
    """
    templates/zac/productaanvraag-zaakafhandelparameters-job.yaml's own
    seeding, verified against ZAC's real REST API rather than just trusting
    the Job's exit code - both that it's "valide" at all (a group, a case
    definition, and a niet-ontvankelijk resultaattype are all required, see
    ZaaktypeCmmnConfiguration.isValide() in the ZAC source) and that its
    productaanvraagtype matches what the productaanvraag object's own "type"
    field carries in test_full_productaanvraag_flow_creates_a_zaak below.
    """
    zaaktype_uuid = _zaaktype_url(traefik_ip).rstrip("/").rsplit("/", 1)[-1]
    response = requests.get(
        host_url(traefik_ip, f"/rest/zaakafhandelparameters/{zaaktype_uuid}"),
        headers={
            **host_headers(ZAC_HOST),
            "Authorization": f"Bearer {_beheerder_token(traefik_ip)}",
        },
        timeout=15,
    )
    assert response.status_code == 200
    config = response.json()
    assert config["valide"] is True
    assert config["productaanvraagtype"] == PRODUCTAANVRAAGTYPE
    assert config["defaultGroepId"] == "behandelaars-test-2"
    assert config["caseDefinition"]["key"] == "generiek-zaakafhandelmodel"
    assert config["zaakNietOntvankelijkResultaattype"]["naam"] == "Geweigerd"


def test_openformulieren_form_has_valid_objects_api_backend():
    """
    templates/openformulieren/productaanvraag-form-job.yaml's own form,
    verified the same way it was confirmed live while building this flow:
    running Open Formulieren's own ObjectsAPIOptionsSerializer against the
    real, deployed registration backend options - this actually calls out
    to the live Catalogi/Objecttypen APIs (case type, document type,
    catalogue all resolved for real), not just a shape/schema check.

    Uses `kubectl exec`, like test_database.py's own postgres checks - this
    validation only exists inside Open Formulieren's own Django app, there's
    no equivalent public REST endpoint for it.
    """
    pod = kubectl(
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        "app.kubernetes.io/name=openformulieren",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ).strip()
    script = (
        "from openforms.forms.models import Form\n"
        "from openforms.registrations.contrib.objects_api.config import ObjectsAPIOptionsSerializer\n"
        "form = Form.objects.get(slug='test-zaaktype-1')\n"
        "assert form.active, 'form is not active'\n"
        "backend = form.registration_backends.get(key='objects-api-productaanvraag')\n"
        "serializer = ObjectsAPIOptionsSerializer(data=backend.options, context={'validate_business_logic': True})\n"
        "valid = serializer.is_valid()\n"
        "print('VALID:' + str(valid))\n"
        "print('ERRORS:' + str(serializer.errors))\n"
    )
    output = kubectl(
        "exec", "-n", NAMESPACE, pod, "--", "python", "src/manage.py", "shell", "-c", script
    )
    assert "VALID:True" in output, output
    assert "ERRORS:{}" in output, output


def test_full_productaanvraag_flow_creates_a_zaak(traefik_ip):
    """
    The real thing, not a proxy for it: post a productaanvraag object to
    Objects API (exactly what Open Formulieren's objects_api registration
    backend does on a real form submission) and poll ZAC's own zaken until
    one shows up - proving Objects API -> Open Notificaties -> ZAC's
    ProductaanvraagService are wired together correctly end to end, not
    just that each piece is individually configured right.

    A unique kenmerk per test run (the current time) keeps repeated runs of
    this test distinguishable from each other and from the zaken left over
    from this flow's own manual verification while it was being built.
    """
    kenmerk = f"pytest-{int(time.time())}"
    zaaktype_url = _zaaktype_url(traefik_ip)

    create_response = requests.post(
        host_url(traefik_ip, "/api/v2/objects"),
        headers={
            **host_headers(OBJECTEN_HOST),
            "Authorization": f"Token {OBJECTEN_TOKEN}",
            "Content-Crs": "EPSG:4326",
        },
        json={
            "type": (
                f"http://{PRODUCTAANVRAAG_OBJECTTYPE_INTERNAL_HOST}"
                f"/api/v2/objecttypes/{PRODUCTAANVRAAG_OBJECTTYPE_UUID}"
            ),
            "record": {
                "typeVersion": 1,
                "data": {
                    "bron": {"naam": "Open Formulieren", "kenmerk": kenmerk},
                    "type": PRODUCTAANVRAAGTYPE,
                    "aanvraaggegevens": {
                        "melding": {
                            "naamAanvrager": "pytest",
                            "omschrijving": "productaanvraag flow test",
                        }
                    },
                    "taal": "nld",
                },
                "startAt": time.strftime("%Y-%m-%d"),
            },
        },
        timeout=15,
    )
    assert create_response.status_code == 201, create_response.text

    deadline = time.time() + 60
    matching_zaak = None
    while time.time() < deadline and matching_zaak is None:
        zaken = _openzaak_get(traefik_ip, "/zaken/api/v1/zaken", zaaktype=zaaktype_url)
        matching_zaak = next(
            (z for z in zaken["results"] if kenmerk in z["toelichting"]), None
        )
        if matching_zaak is None:
            time.sleep(3)

    assert matching_zaak is not None, (
        f"no zaak with kenmerk {kenmerk!r} appeared within 60s - the "
        "notification either wasn't sent, wasn't delivered to ZAC, or "
        "ZAC couldn't match it to a zaaktype (see "
        "test_zac_zaaktype_test_1_zaakafhandelparameters_is_valide)"
    )
