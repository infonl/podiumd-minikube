"""The WAF's client-certificate check on the inway's host: the edge cases of podiumd-infra's waf-mtls-check.

Its consumer cases (an onboarded certificate, a route's allow list) are podiumd-tests'.
"""

from pathlib import Path

import pytest
import requests

from conftest import host_url

from podiumd_minikube import pki
from podiumd_minikube import process

INWAY = host_url("inway.local", "/echo/")


@pytest.fixture(autouse=True)
def inway(enabled_profiles):
    if not enabled_profiles.get("frankgateway"):
        pytest.skip("'frankgateway' profile is not deployed")


def _openssl(*args: str) -> None:
    process.run(["openssl", *args])


def _certificate(directory: Path, name: str, *, signed: bool) -> tuple[str, str]:
    """Return (certificate, key) files of a client certificate, from the client CA or self-signed."""
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    subject = f"/CN=podiumd-minikube test {name}"
    if signed:
        csr = directory / f"{name}.csr"
        _openssl(
            "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", subject, "-keyout", str(key), "-out", str(csr)
        )
        _openssl(
            "x509", "-req", "-in", str(csr), "-CA", str(pki.CLIENT_CA_CERT), "-CAkey", str(pki.CLIENT_CA_KEY),
            "-days", "1", "-out", str(cert),
        )  # fmt: skip
    else:
        _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", subject,
                 "-keyout", str(key), "-out", str(cert))  # fmt: skip
    return str(cert), str(key)


@pytest.mark.usefixtures("edge_ip")
def test_no_certificate_is_refused():
    response = requests.get(INWAY, timeout=30)
    assert response.status_code == 400
    assert "No required SSL certificate was sent" in response.text


@pytest.mark.usefixtures("edge_ip")
def test_a_certificate_of_another_ca_is_refused(tmp_path: Path):
    response = requests.get(INWAY, cert=_certificate(tmp_path, "rogue", signed=False), timeout=30)
    assert response.status_code == 400
    assert "The SSL certificate error" in response.text


@pytest.mark.usefixtures("edge_ip")
def test_a_certificate_of_the_client_ca_reaches_the_inway_verified(tmp_path: Path):
    """The inway passed its verification check (the WAF replaced the forged FAILED) and looked up the fingerprint.

    secret_unavailable: podiumd-tests has registered no consumers in OpenBao yet.
    """
    forged = {"X-Client-Cert-Verification": "FAILED", "X-Client-Cert-Fingerprint": "0" * 40}
    response = requests.get(INWAY, cert=_certificate(tmp_path, "unknown", signed=True), headers=forged, timeout=30)
    answer = (response.status_code, response.json()["error"])
    assert answer in {(401, "unknown_client_certificate"), (503, "secret_unavailable")}
