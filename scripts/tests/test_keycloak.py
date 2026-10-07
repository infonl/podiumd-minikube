"""lib.keycloak: syncing the live realm clients."""

from typing import Any

from lib import keycloak


def test_client_changes_add_https_and_set_pkce_once():
    zac: dict[str, Any] = {
        "clientId": "zaakafhandelcomponent",
        "redirectUris": ["http://zac.local/*"],
        "webOrigins": [],
        "attributes": {},
    }
    assert keycloak.client_changes(zac, zac_pkce=False) == [
        "-s",
        'redirectUris=["http://zac.local/*", "https://zac.local/*"]',
    ]
    done = {
        **zac,
        "redirectUris": ["http://zac.local/*", "https://zac.local/*"],
        "attributes": {"pkce.code.challenge.method": ""},
    }
    assert keycloak.client_changes(done, zac_pkce=False) == []
    assert keycloak.client_changes(done, zac_pkce=True) == ["-s", 'attributes."pkce.code.challenge.method"=S256']


def test_missing_clients_are_the_vendored_ones_not_live_with_https_twins():
    live = [{"clientId": "zac"}]
    vendored = [{"clientId": "zac"}, {"clientId": "openinwoner", "redirectUris": ["http://openinwoner.local/*"]}]
    assert keycloak.missing_clients(live, vendored) == [
        {
            "clientId": "openinwoner",
            "redirectUris": ["http://openinwoner.local/*", "https://openinwoner.local/*"],
            "webOrigins": [],
        }
    ]
