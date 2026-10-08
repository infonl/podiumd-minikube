"""The frankgateway profile: OpenBao unsealed, the outway serving the api-proxy's paths, apps pointed at it."""

import json

import pytest

from conftest import kubectl

NAMESPACE = "podiumd-minikube"
OUTWAY = "http://frankgateway-outway:9080"


@pytest.fixture(autouse=True)
def frankgateway(enabled_profiles):
    if not enabled_profiles.get("frankgateway"):
        pytest.skip("'frankgateway' profile is not deployed")


def test_openbao_initialised_and_unsealed():
    status = json.loads(
        kubectl(
            "exec", "-n", NAMESPACE, "podiumd-minikube-openbao-0", "--",
            "env", "BAO_ADDR=http://127.0.0.1:8200", "bao", "status", "-format=json",
        )
    )  # fmt: skip
    assert status["initialized"]
    assert not status["sealed"]


def _status_from_api_proxy(*curl_args):
    """HTTP status of a curl from the api-proxy pod, which has curl (the gateway image does not)."""
    return kubectl(
        "exec", "-n", NAMESPACE, "deploy/api-proxy", "--", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
        *curl_args,
    )  # fmt: skip


@pytest.mark.parametrize(
    "curl_args",
    [
        ["-H", "Accept: application/hal+json", f"{OUTWAY}/api/v2/zoeken?kvkNummer=68750110&type=rechtspersoon"],
        [f"{OUTWAY}/lvbag/individuelebevragingen/v2/adressen/0363200003761447"],
        [
            "-H", "Content-Type: application/json",
            "-d", '{"type":"RaadpleegMetBurgerservicenummer","burgerservicenummer":["999993653"],"fields":["naam"]}',
            f"{OUTWAY}/haalcentraal/api/brp/personen",
        ],
    ],
)  # fmt: skip
def test_outway_routes(curl_args):
    assert _status_from_api_proxy(*curl_args) == "200"


def test_zac_calls_brp_through_the_outway():
    url = kubectl("get", "configmap", "zac", "-n", NAMESPACE, "-o", "jsonpath={.data.BRP_API_CLIENT_MP_REST_URL}")
    assert url.startswith(f"{OUTWAY}/")
