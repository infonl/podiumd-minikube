"""The BAG mock behind the api-proxy: its self links are outbound URLs (values.yaml bag.urlRewrite)."""

import json

from conftest import kubectl

from podiumd_minikube.manifests import API_PROXY_URL
from podiumd_minikube.manifests import OUTWAY_URL
from podiumd_minikube.paths import NAMESPACE

ADDRESS = "lvbag/individuelebevragingen/v2/adressen/0363200003761447"


def test_bag_self_links_point_at_the_outbound_url(enabled_profiles):
    body = kubectl(
        "exec", "-n", NAMESPACE, "deploy/api-proxy", "--", "curl", "-sf", f"http://localhost:8080/{ADDRESS}",
    )  # fmt: skip
    outbound = OUTWAY_URL if enabled_profiles.get("frankgateway") else API_PROXY_URL
    href = json.loads(body)["_links"]["self"]["href"]
    assert href == f"{outbound}{ADDRESS}", href
