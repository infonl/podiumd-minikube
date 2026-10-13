"""The edge's request-body limit, as ExternalsPodiumD's NGINX Gateway Fabric has it."""

import requests

from conftest import host_url

# Above nginx's default 1 MB, which ExternalsPodiumD's live default-client-settings (maxSize 0) lifts.
BODY = b"x" * (2 * 1024 * 1024)


def test_two_megabyte_request_passes_the_gateway(edge_ip):
    """nginx passes the request to Open Zaak, which answers it, instead of rejecting it with 413."""
    response = requests.post(
        host_url("openzaak.local", "/documenten/api/v1/enkelvoudiginformatieobjecten"),
        data=BODY,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    assert response.status_code != 413
