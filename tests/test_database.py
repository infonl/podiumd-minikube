"""
Postgres schema/fixture checks, run via `kubectl exec` into the postgres
pod (there's no port-forward assumed to be running, so this avoids needing
one just to run the test suite).
"""

import pytest

from conftest import NAMESPACE, kubectl

EXPECTED_DATABASES = {
    "zac",
    "keycloak",
    "openzaak",
    "openklant",
    "objects",
    "objecttypes",
    "opennotificaties",
    "openarchiefbeheer",
    "openformulieren",
    "Pabc",  # yes, capital P - matches podiumd.pabc.settings.database.name exactly
}

# Matches 00-create-databases.sql's own PostGIS CREATE EXTENSION statements.
POSTGIS_DATABASES = {"openzaak", "objects", "opennotificaties", "openarchiefbeheer"}


def psql(database, query):
    postgres_pod = _postgres_pod_name()
    return kubectl(
        "exec",
        "-n",
        NAMESPACE,
        postgres_pod,
        "--",
        "psql",
        "-U",
        "postgres",
        "-d",
        database,
        "-t",
        "-A",
        "-c",
        query,
    ).strip()


def _postgres_pod_name():
    return kubectl(
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        "app=postgres",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ).strip()


@pytest.fixture(scope="module")
def existing_databases(pods):
    raw = psql("postgres", "SELECT datname FROM pg_database WHERE datistemplate = false;")
    return set(raw.splitlines())


def test_all_expected_databases_exist(existing_databases):
    missing = EXPECTED_DATABASES - existing_databases
    assert not missing, f"missing databases: {missing}"


@pytest.mark.parametrize("database", sorted(POSTGIS_DATABASES))
def test_postgis_extension_installed(pods, database, existing_databases):
    if database not in existing_databases:
        pytest.skip(f"database '{database}' does not exist (profile not deployed)")
    result = psql(database, "SELECT extname FROM pg_extension WHERE extname = 'postgis';")
    assert result == "postgis", f"postgis extension missing from '{database}'"


def test_openzaak_zac_client_credentials_seeded(pods, existing_databases):
    """
    The specific fixture data ZAC itself depends on to authenticate against
    Open Zaak's ZGW APIs - found missing live in step 4 (the fixture-seeding
    script waited forever for an admin user that was never created), fixed,
    and this is the regression check for that fix staying in place.
    """
    if "openzaak" not in existing_databases:
        pytest.skip("openzaak database does not exist")
    client_ids = psql("openzaak", "SELECT client_ids FROM authorizations_applicatie;")
    assert "zac_client" in client_ids

    secret = psql(
        "openzaak",
        "SELECT secret FROM vng_api_common_jwtsecret WHERE identifier = 'zac_client';",
    )
    assert secret == "openzaakZaakafhandelcomponentClientSecret"
