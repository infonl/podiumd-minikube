"""KISS and ITA wiring of this project: Elasticsearch sizing and the edge forwarding chunked bodies."""

import json

import pytest
import requests

from conftest import host_url
from conftest import kubectl

# podiumd's create-required-objecttypen Job creates it with this fixed UUID.
ACTIVITEITENLOG = "2eb81bd1-0d2b-4123-84ab-d55b99b9e75a"
# values.yaml's podiumd.ita.apiConnections.object.apiKey.
ITA_OBJECTEN_TOKEN = "objectenItaToken"  # nosec B105


def test_kiss_elasticsearch_green(enabled_profiles):
    if not enabled_profiles.get("kiss"):
        pytest.skip("'kiss' profile is not deployed")
    health = json.loads(kubectl("get", "elasticsearch", "kiss", "-n", "podiumd-minikube", "-o", "json"))["status"]
    assert health.get("health") == "green"


def test_chunked_post_reaches_objecten(edge_ip, enabled_profiles):
    """ITA posts its logboek chunked; the edge must forward the body (403 means Objecten got none)."""
    if not enabled_profiles.get("ita"):
        pytest.skip("'ita' profile is not deployed")
    body = json.dumps(
        {
            "type": f"https://objecttypen.local/api/v2/objecttypes/{ACTIVITEITENLOG}",
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
