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
# role mapping, not just a valid token, through a browser login (the zac
# client has no direct access grants). As podiumd-infra's
# seed-zac-zaakafhandelparameters.sh: its own Keycloak user SEED_USERNAME in
# group beheerders (podiumd's pabc-dataset maps it to every ZAC role), with a
# new random password on every run that is never stored.
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
import html
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

from http.cookiejar import CookieJar

KEYCLOAK_URL = os.environ["KEYCLOAK_URL"].rstrip("/")
KEYCLOAK_REALM = os.environ["KEYCLOAK_REALM"]
KEYCLOAK_ADMIN_USERNAME = os.environ["KEYCLOAK_ADMIN_USERNAME"]
KEYCLOAK_ADMIN_PASSWORD = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
SEED_USERNAME = "podiumd-minikube-zac-seed"
SEED_GROUP = "beheerders"

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


def http(method, url, *, headers=None, data=None, opener=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with (opener.open if opener else urllib.request.urlopen)(req, timeout=30) as resp:
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


def admin_token():
    form = {"grant_type": "password", "client_id": "admin-cli"}
    form |= {"username": KEYCLOAK_ADMIN_USERNAME, "password": KEYCLOAK_ADMIN_PASSWORD}
    req = urllib.request.Request(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data=urllib.parse.urlencode(form).encode(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def zac_session(password):
    """An opener with ZAC's session cookie, from the browser login: the zac client has no direct access grants."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()), NoRedirect())

    def location(method, url, data=None):
        try:
            resp = opener.open(urllib.request.Request(url, data=data, method=method), timeout=30)
            return resp.getcode(), resp.headers.get("Location"), resp.read().decode(errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Location"), exc.read().decode(errors="replace")

    zac = ZAC_BASE_URL.removesuffix("/rest")
    _, auth_url, _ = location("GET", f"{zac}/")
    _, _, page = location("GET", auth_url)
    action = re.search(r'action="([^"]*)"', page)
    if not action:
        sys.exit(f"no login form at {auth_url}")
    form = urllib.parse.urlencode({"username": SEED_USERNAME, "password": password, "credentialId": ""})
    status, callback, _ = location("POST", html.unescape(action.group(1)), form.encode())
    if status != 302 or not callback.startswith(zac):
        sys.exit(f"Keycloak login of {SEED_USERNAME} did not return to ZAC: {status} {callback}")
    location("GET", callback)
    return opener


def seed_user_password():
    """Ensures SEED_USERNAME in SEED_GROUP without required actions; returns its new password."""
    admin = {"Authorization": f"Bearer {admin_token()}"}
    api = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}"
    query = urllib.parse.urlencode({"username": SEED_USERNAME, "exact": "true"})
    users = http("GET", f"{api}/users?{query}", headers=admin)
    if not users:
        # Email and names filled in, so the user profile asks for no update at login.
        user = {
            "username": SEED_USERNAME,
            "enabled": True,
            "email": f"{SEED_USERNAME}@example.com",
            "emailVerified": True,
            "firstName": "podiumd-minikube",
            "lastName": "zac-seed",
        }
        http("POST", f"{api}/users", headers=admin, data=user)
        users = http("GET", f"{api}/users?{query}", headers=admin)
    user = users[0]
    user_id = user["id"]
    # The realm's default required action CONFIGURE_TOTP would stop the password grant.
    http("PUT", f"{api}/users/{user_id}", headers=admin, data={**user, "requiredActions": []})
    groups = http("GET", f"{api}/groups?{urllib.parse.urlencode({'search': SEED_GROUP, 'exact': 'true'})}", headers=admin)
    group = next((g for g in groups if g["name"] == SEED_GROUP), None)
    if group is None:
        sys.exit(f"realm {KEYCLOAK_REALM} has no group {SEED_GROUP}")
    http("PUT", f"{api}/users/{user_id}/groups/{group['id']}", headers=admin, data={})
    # As podiumd-infra: the realm role of the same name too, where the realm has it.
    roles = http("GET", f"{api}/roles?{urllib.parse.urlencode({'search': SEED_GROUP})}", headers=admin)
    role = [r for r in roles if r["name"] == SEED_GROUP]
    if role:
        http("POST", f"{api}/users/{user_id}/role-mappings/realm", headers=admin, data=role)
    password = secrets.token_urlsafe(33)
    credential = {"type": "password", "value": password, "temporary": False}
    http("PUT", f"{api}/users/{user_id}/reset-password", headers=admin, data=credential)
    return password


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
    # A chart upgrade adds a version and ends the old one (helm-charts #577).
    current = [z for z in zaaktypen["results"] if not z.get("eindeGeldigheid")]
    if not current:
        sys.exit(f"No current zaaktype found with identificatie={ZAAKTYPE_IDENTIFICATIE!r}")
    zaaktype = current[0]
    zaaktype_uuid = zaaktype["url"].rstrip("/").rsplit("/", 1)[-1]

    session = zac_session(seed_user_password())

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
        opener=session,
    )
    resultaattype = next(
        (r for r in resultaattypen if r["naam"] == NIET_ONTVANKELIJK_RESULTAAT_NAAM),
        None,
    )
    if resultaattype is None:
        sys.exit(
            f"No resultaattype named {NIET_ONTVANKELIJK_RESULTAAT_NAAM!r} found for zaaktype {ZAAKTYPE_IDENTIFICATIE!r}"
        )

    config = http("GET", f"{ZAC_BASE_URL}/zaakafhandelparameters/{zaaktype_uuid}", opener=session)
    config["caseDefinition"] = {"key": CASE_DEFINITION_KEY}
    config["defaultGroepId"] = DEFAULT_GROEP_ID
    config["zaakNietOntvankelijkResultaattype"] = resultaattype
    config["productaanvraagtype"] = PRODUCTAANVRAAGTYPE

    updated = http("PUT", f"{ZAC_BASE_URL}/zaakafhandelparameters", opener=session, data=config)
    if not updated.get("valide"):
        sys.exit(f"zaakafhandelparameters for {ZAAKTYPE_IDENTIFICATIE!r} still not valide after PUT: {updated}")

    print(f"zaakafhandelparameters for {ZAAKTYPE_IDENTIFICATIE!r} (uuid {zaaktype_uuid}) is valide.")


if __name__ == "__main__":
    main()
