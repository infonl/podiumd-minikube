# Idempotent seeding of the productaanvraag flow's ZAC-side configuration:
# gives ZAAKTYPE_IDENTIFICATIE valid zaakafhandelparameters (so ZAC can
# actually start a zaak of that type at all - see
# ZaaktypeCmmnConfiguration.isValide() in the ZAC source: it requires a
# group, a case definition and a "niet-ontvankelijk" resultaattype before
# any zaak can be started) and points its own productaanvraagtype at the
# value the productaanvraag object's own "type" field carries, so
# ProductaanvraagService can match an incoming productaanvraag to this
# zaaktype (see nl.info.zac.productaanvraag.ProductaanvraagService).
#
# Uses ZAC's own /rest/zaakafhandelparameters REST API directly, since ZAC
# (a WildFly/Kotlin app, unlike every Django app elsewhere in this flow) has
# no setup_configuration-style declarative mechanism of its own - confirmed
# live this is otherwise only reachable by hand through the beheerder UI.
# Authenticates as a real user (not a service-account client_credentials
# grant) because every zaakafhandelparameters endpoint asserts
# policyService.readOverigeRechten().beheren, which needs an actual PABC
# role mapping, not just a valid token - BEHEERDER_USERNAME is one of this
# project's own vendored test users for exactly this role (see
# tests/test_login_flow.py's identical credentials).
#
# GET-then-PUT, not a hand-built payload: ZAC's own
# RestZaakafhandelParameters has several fields the frontend requires
# non-null but this script doesn't otherwise care about (mailtemplateKoppelingen,
# betrokkeneKoppelingen, automaticEmailConfirmation, ...) - fetching ZAC's own
# generated default and only overwriting the handful of fields this flow
# actually needs is far less fragile than reconstructing that whole shape
# by hand (confirmed live: an earlier hand-built payload attempt is exactly
# how the fields this comment lists were discovered to matter at all).
#
# Idempotent: PUT-ing the same zaakafhandelparameters twice is a plain
# update, not a create - safe to re-run this Job's already-succeeded,
# unchanged spec like any other Job in this project.
import json
import os
import sys
import urllib.error
import urllib.request

KEYCLOAK_TOKEN_URL = os.environ["KEYCLOAK_TOKEN_URL"]
KEYCLOAK_CLIENT_ID = os.environ["KEYCLOAK_CLIENT_ID"]
KEYCLOAK_CLIENT_SECRET = os.environ["KEYCLOAK_CLIENT_SECRET"]
BEHEERDER_USERNAME = os.environ["BEHEERDER_USERNAME"]
BEHEERDER_PASSWORD = os.environ["BEHEERDER_PASSWORD"]

ZAC_BASE_URL = os.environ["ZAC_BASE_URL"].rstrip("/")
OPENZAAK_BASE_URL = os.environ["OPENZAAK_BASE_URL"].rstrip("/")
ZGW_JWT_CLIENT_ID = os.environ["ZGW_JWT_CLIENT_ID"]
ZGW_JWT_SECRET = os.environ["ZGW_JWT_SECRET"]

CATALOGUS_DOMEIN = os.environ["CATALOGUS_DOMEIN"]
CATALOGUS_RSIN = os.environ["CATALOGUS_RSIN"]
ZAAKTYPE_IDENTIFICATIE = os.environ["ZAAKTYPE_IDENTIFICATIE"]
CASE_DEFINITION_KEY = os.environ["CASE_DEFINITION_KEY"]
DEFAULT_GROEP_ID = os.environ["DEFAULT_GROEP_ID"]
NIET_ONTVANKELIJK_RESULTAAT_NAAM = os.environ["NIET_ONTVANKELIJK_RESULTAAT_NAAM"]
PRODUCTAANVRAAGTYPE = os.environ["PRODUCTAANVRAAGTYPE"]


def http(method, url, *, headers=None, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc


def zgw_jwt(client_id, secret):
    import base64
    import hashlib
    import hmac
    import time

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
    signing_input = b64url(json.dumps(header).encode()) + b"." + b64url(json.dumps(payload).encode())
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + b64url(signature)).decode()


def get_beheerder_token():
    form = "&".join(
        f"{k}={v}" for k, v in {
            "grant_type": "password",
            "client_id": KEYCLOAK_CLIENT_ID,
            "client_secret": KEYCLOAK_CLIENT_SECRET,
            "username": BEHEERDER_USERNAME,
            "password": BEHEERDER_PASSWORD,
            "scope": "openid",
        }.items()
    )
    req = urllib.request.Request(KEYCLOAK_TOKEN_URL, data=form.encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def main():
    zgw_headers = {"Authorization": f"Bearer {zgw_jwt(ZGW_JWT_CLIENT_ID, ZGW_JWT_SECRET)}"}

    catalogi = http(
        "GET",
        f"{OPENZAAK_BASE_URL}/catalogi/api/v1/catalogussen?domein={CATALOGUS_DOMEIN}&rsin={CATALOGUS_RSIN}",
        headers=zgw_headers,
    )
    if catalogi["count"] == 0:
        sys.exit(f"No catalogus found for domein={CATALOGUS_DOMEIN} rsin={CATALOGUS_RSIN}")
    catalogus_url = catalogi["results"][0]["url"]

    zaaktypen = http(
        "GET",
        f"{OPENZAAK_BASE_URL}/catalogi/api/v1/zaaktypen?catalogus={catalogus_url}&identificatie={ZAAKTYPE_IDENTIFICATIE}",
        headers=zgw_headers,
    )
    if zaaktypen["count"] == 0:
        sys.exit(f"No zaaktype found with identificatie={ZAAKTYPE_IDENTIFICATIE!r}")
    zaaktype = zaaktypen["results"][0]
    zaaktype_uuid = zaaktype["url"].rstrip("/").rsplit("/", 1)[-1]

    bearer = {"Authorization": f"Bearer {get_beheerder_token()}"}

    # Via ZAC's own endpoint, not openzaak's raw Catalogi API directly: it
    # already returns RestResultaattype-shaped objects (id/naam/...) that
    # can be dropped straight into zaakNietOntvankelijkResultaattype below -
    # openzaak's own raw resultaattypen resource uses a different shape
    # (omschrijving/url, no plain "naam"/"id") that ZAC's converter doesn't
    # accept on write (confirmed live: only `.id` is read from this field,
    # but it has to be *this* endpoint's "id", ZAC's own ZTC-client-mapped
    # UUID, not openzaak's raw resource representation).
    resultaattypen = http(
        "GET",
        f"{ZAC_BASE_URL}/zaakafhandelparameters/resultaattypes/{zaaktype_uuid}",
        headers=bearer,
    )
    resultaattype = next(
        (r for r in resultaattypen if r["naam"] == NIET_ONTVANKELIJK_RESULTAAT_NAAM),
        None,
    )
    if resultaattype is None:
        sys.exit(
            f"No resultaattype named {NIET_ONTVANKELIJK_RESULTAAT_NAAM!r} found for zaaktype {ZAAKTYPE_IDENTIFICATIE!r}"
        )

    config = http("GET", f"{ZAC_BASE_URL}/zaakafhandelparameters/{zaaktype_uuid}", headers=bearer)
    config["caseDefinition"] = {"key": CASE_DEFINITION_KEY}
    config["defaultGroepId"] = DEFAULT_GROEP_ID
    config["zaakNietOntvankelijkResultaattype"] = resultaattype
    config["productaanvraagtype"] = PRODUCTAANVRAAGTYPE

    updated = http("PUT", f"{ZAC_BASE_URL}/zaakafhandelparameters", headers=bearer, data=config)
    if not updated.get("valide"):
        sys.exit(f"zaakafhandelparameters for {ZAAKTYPE_IDENTIFICATIE!r} still not valide after PUT: {updated}")

    print(f"zaakafhandelparameters for {ZAAKTYPE_IDENTIFICATIE!r} (uuid {zaaktype_uuid}) is valide.")


if __name__ == "__main__":
    main()
