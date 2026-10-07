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
    assert keycloak.client_changes(zac, {}, zac_pkce=False) == [
        "-s",
        'redirectUris=["http://zac.local/*", "https://zac.local/*"]',
    ]
    done = {
        **zac,
        "redirectUris": ["http://zac.local/*", "https://zac.local/*"],
        "attributes": {"pkce.code.challenge.method": ""},
    }
    assert keycloak.client_changes(done, {}, zac_pkce=False) == []
    assert keycloak.client_changes(done, {}, zac_pkce=True) == ["-s", 'attributes."pkce.code.challenge.method"=S256']


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


def test_client_changes_add_vendored_uris_and_keep_live_ones():
    live = {"clientId": "kiss", "redirectUris": ["http://kiss.local/*"], "webOrigins": []}
    vendored = {"redirectUris": ["https://contact.local/*"]}
    assert keycloak.client_changes(live, vendored, zac_pkce=False) == [
        "-s",
        'redirectUris=["http://kiss.local/*", "https://contact.local/*", "https://kiss.local/*"]',
    ]


def test_missing_by_name_drops_ids():
    live = [{"name": "samaccountname"}]
    vendored = [{"id": "1", "name": "samaccountname"}, {"id": "2", "containerId": "c", "name": "kiss-roles"}]
    assert keycloak.missing_by_name(live, vendored) == [{"name": "kiss-roles"}]


def test_profile_with_samaccountname_once():
    profile = {"unmanagedAttributePolicy": "ENABLED", "attributes": [{"name": "username"}]}
    added = keycloak.profile_with_samaccountname(profile)
    assert added is not None
    assert [a["name"] for a in added["attributes"]] == ["username", "samaccountname"]
    assert keycloak.profile_with_samaccountname(added) is None
