"""OMC, as ExternalsPodiumD's smoke test 79-omc-health checks it."""

import base64
import hashlib
import hmac
import json
import time

import pytest
import requests

from conftest import host_url

# values.yaml's podiumd.omc.settings.omc.auth.jwt.secret; issuer and audience
# are the chart's defaults.
JWT_SECRET = "omcAuthJwtSecretForPodiumdMinikubeAtLeastSixtyFourCharactersLong0123"  # nosec B105


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def omc_token() -> str:
    """An HS256 JWT OMC accepts, with the claims of ExternalsPodiumD's omc-client.ts."""
    now = int(time.time())
    claims = {"iss": "omc", "aud": "omc", "sub": "podiumd-minikube", "iat": now, "exp": now + 600}
    signing_input = f"{_b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())}.{_b64(json.dumps(claims).encode())}"
    signature = hmac.new(JWT_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


@pytest.fixture
def omc(traefik_ip, enabled_profiles):
    if not enabled_profiles.get("omc"):
        pytest.skip("'omc' profile is not deployed")


@pytest.mark.usefixtures("omc")
def test_omc_version():
    headers = {"Authorization": f"Bearer {omc_token()}"}
    assert requests.get(host_url("omc.local", "/Events/Version"), headers=headers, timeout=10).status_code == 200


@pytest.mark.usefixtures("omc")
def test_omc_listen_requires_a_token():
    response = requests.post(host_url("omc.local", "/Events/Listen"), json={}, timeout=10)
    assert response.status_code in (401, 403)
