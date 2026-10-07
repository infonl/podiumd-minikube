"""The edge's request-body limit, as ExternalsPodiumD's NGINX Gateway Fabric has it."""

import requests

from conftest import host_url

# ExternalsPodiumD sets no ClientSettingsPolicy, so nginx's default 1 MB applies.
BODY = b"x" * (2 * 1024 * 1024)


def test_two_megabyte_request_is_rejected_at_the_gateway(edge_ip):
    """nginx answers 413 before the request reaches Open Zaak or its authentication."""
    response = requests.post(
        host_url("openzaak.local", "/documenten/api/v1/enkelvoudiginformatieobjecten"),
        data=BODY,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    assert response.status_code == 413
