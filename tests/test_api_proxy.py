"""The api-proxy: BRP, KvK and BAG on the paths ExternalsPodiumD's apps call."""

import pytest
import requests

from conftest import host_url

PROXY = "api-proxy.local"


# KvK: its test API's dataset (68750110 "Test BV Donald"), with the Accept header ZAC sends; BAG: WireMock.
@pytest.mark.parametrize(
    ("path", "accept"),
    [
        ("/api/v2/zoeken?kvkNummer=68750110&type=rechtspersoon", "application/hal+json"),
        ("/api/v1/basisprofielen/68750110?geoData=false", "application/hal+json"),
        ("/lvbag/individuelebevragingen/v2/adressen/0363200003761447", "*/*"),
    ],
)
def test_kvk_and_bag(edge_ip, path, accept):
    response = requests.get(host_url(PROXY, path), headers={"Accept": accept}, timeout=10)
    assert response.status_code == 200, response.text[:300]


def test_brp_mock(edge_ip):
    response = requests.post(
        host_url(PROXY, "/haalcentraal/api/brp/personen"),
        json={"type": "RaadpleegMetBurgerservicenummer", "burgerservicenummer": ["999993653"], "fields": ["naam"]},
        timeout=10,
    )
    assert response.status_code == 200, response.text
    assert response.json()["personen"], "brp-personen-mock has no person 999993653"
