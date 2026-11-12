"""The BAG mock's self links name the proxy the app called, by a host Open Zaak's URLValidator accepts."""

import json

import pytest

from conftest import kubectl

from podiumd_minikube.paths import NAMESPACE

ADDRESS = "lvbag/individuelebevragingen/v2/adressen/0363200003761447"
API_PROXY = f"http://api-proxy.{NAMESPACE}.svc.cluster.local/"
OUTWAY = f"http://frankgateway-outway.{NAMESPACE}.svc.cluster.local:9080/"


def _self_link(base):
    body = kubectl("exec", "-n", NAMESPACE, "deploy/api-proxy", "--", "curl", "-sf", f"{base}{ADDRESS}")
    return json.loads(body)["_links"]["self"]["href"]


def test_bag_self_links_through_the_api_proxy_name_the_api_proxy():
    assert _self_link("http://localhost:8080/") == f"{API_PROXY}{ADDRESS}"


def test_bag_self_links_through_the_outway_name_the_outway(enabled_profiles):
    if not enabled_profiles.get("frankgateway"):
        pytest.skip("'frankgateway' profile is not deployed")
    assert _self_link(OUTWAY) == f"{OUTWAY}{ADDRESS}"
