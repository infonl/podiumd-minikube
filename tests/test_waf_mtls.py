"""The WAF's client-certificate passthrough on the inway's host: the edge cases of podiumd-infra's waf-mtls-check.

Its consumer cases (an onboarded certificate, a route's allow list) are podiumd-tests'.
"""

from pathlib import Path

import pytest
import requests

from conftest import host_url

from podiumd_minikube import process

INWAY = host_url("inway.local", "/echo/")
# A client's own copy of the headers the WAF sets from the handshake.
FORGED = {"X-Client-Cert-Fingerprint": "0" * 40, "X-Client-Cert-Subject": "CN=partner-a"}


@pytest.fixture(autouse=True)
def inway(enabled_profiles):
    if not enabled_profiles.get("frankgateway"):
        pytest.skip("'frankgateway' profile is not deployed")


def _answer(response: requests.Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]


@pytest.mark.usefixtures("edge_ip")
def test_no_certificate_reaches_the_inway_without_one():
    assert _answer(requests.get(INWAY, timeout=30)) == (401, "client_certificate_required")


@pytest.mark.usefixtures("edge_ip")
def test_forged_headers_over_http_are_removed():
    response = requests.get(INWAY.replace("https://", "http://", 1), headers=FORGED, allow_redirects=False, timeout=30)
    if response.status_code != 301:
        assert _answer(response) == (401, "client_certificate_required")


@pytest.mark.usefixtures("edge_ip")
def test_a_self_signed_certificate_reaches_the_inway_with_its_own_fingerprint(tmp_path: Path):
    """The inway looked up the certificate's fingerprint, not the forged one.

    secret_unavailable: podiumd-tests has registered no consumers in OpenBao yet.
    """
    cert, key = tmp_path / "rogue.crt", tmp_path / "rogue.key"
    process.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-subj", "/CN=podiumd-minikube test rogue", "-keyout", str(key), "-out", str(cert)])  # fmt: skip
    response = requests.get(INWAY, cert=(str(cert), str(key)), headers=FORGED, timeout=30)
    assert _answer(response) in {(401, "unknown_client_certificate"), (503, "secret_unavailable")}
