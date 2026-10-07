"""KISS (chart name contact) and ITA: health, unauthenticated API, their objecttypes."""

import json

import pytest
import requests

from conftest import host_url
from conftest import kubectl

# podiumd's create-required-objecttypen Job creates these with fixed UUIDs.
OBJECTTYPES = {
    "Medewerker": "90984726-2692-49d8-8f9d-742075cb761d",
    "Afdeling": "c8d301f9-80b8-4575-8b29-3e9431f92994",
    "Groep": "cbec27ac-157b-490b-b586-9e8a8f5bbb85",
    "InterneTaak": "72240cd6-29bb-4a66-b1c3-ab6589a39a22",
    "Kennisartikel": "e4d2e353-80ea-4962-8276-920d59a3e8de",
    "VAC": "6a0d8f9d-f7e2-4e57-a91b-01656968a62d",
    "Activiteitenlog": "2eb81bd1-0d2b-4123-84ab-d55b99b9e75a",
}
# values.yaml's podiumd.objecttypen.configuration.token.
OBJECTTYPEN_TOKEN = "objecttypenIntegratieteamToken"  # nosec B105
# values.yaml's podiumd.ita.apiConnections.object.apiKey.
ITA_OBJECTEN_TOKEN = "objectenItaToken"  # nosec B105


@pytest.mark.parametrize("path", ["/healthz", "/api/healthcheck"])
def test_kiss_health(traefik_ip, enabled_profiles, path):
    if not enabled_profiles.get("kiss"):
        pytest.skip("'kiss' profile is not deployed")
    assert requests.get(host_url("contact.local", path), timeout=10).status_code == 200


def test_kiss_elasticsearch_green(enabled_profiles):
    if not enabled_profiles.get("kiss"):
        pytest.skip("'kiss' profile is not deployed")
    health = json.loads(kubectl("get", "elasticsearch", "kiss", "-n", "podiumd-minikube", "-o", "json"))["status"]
    assert health.get("health") == "green"


def test_ita_api_requires_login(traefik_ip, enabled_profiles):
    if not enabled_profiles.get("ita"):
        pytest.skip("'ita' profile is not deployed")
    response = requests.get(host_url("ita.local", "/api/kanalen"), timeout=10, allow_redirects=False)
    assert response.status_code in (401, 403)


@pytest.mark.parametrize(("name", "uuid"), OBJECTTYPES.items())
def test_kiss_objecttype_published(traefik_ip, enabled_profiles, name, uuid):
    if not (enabled_profiles.get("kiss") or enabled_profiles.get("ita")):
        pytest.skip("neither 'kiss' nor 'ita' profile is deployed")
    response = requests.get(
        host_url("objecttypen.local", f"/api/v2/objecttypes/{uuid}"),
        headers={"Authorization": f"Token {OBJECTTYPEN_TOKEN}"},
        timeout=10,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == name
    assert body["versions"], f"{name} has no published version"


def test_chunked_post_reaches_objecten(traefik_ip, enabled_profiles):
    """ITA posts its logboek chunked; the edge must forward the body (403 means Objecten got none)."""
    if not enabled_profiles.get("ita"):
        pytest.skip("'ita' profile is not deployed")
    body = json.dumps(
        {
            "type": f"https://objecttypen.local/api/v2/objecttypes/{OBJECTTYPES['Activiteitenlog']}",
            "record": {"typeVersion": 1, "data": {}, "startAt": "2026-01-01"},
        }
    ).encode()
    response = requests.post(
        host_url("objecten.local", "/api/v2/objects"),
        data=iter([body]),  # a generator body makes requests send it chunked
        headers={
            "Authorization": f"Token {ITA_OBJECTEN_TOKEN}",
            "Content-Type": "application/json",
            "Content-Crs": "EPSG:4326",
        },
        timeout=10,
    )
    # 400: past the permission check, rejected by the Activiteitenlog schema; nothing is created.
    assert response.status_code == 400, response.text
