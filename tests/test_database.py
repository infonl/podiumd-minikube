"""The shared Postgres: databases, extensions and what setup_configuration registers, via `kubectl exec`."""

import pytest
import yaml

from conftest import NAMESPACE
from conftest import kubectl

from podiumd_minikube.paths import VALUES_YAML

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
    "pabc",
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


def test_openzaak_knows_zacs_client(pods, existing_databases):
    """Open Zaak's setup_configuration registers ZAC's zgwApis client and secret."""
    if "openzaak" not in existing_databases:
        pytest.skip("openzaak database does not exist")
    zgw = yaml.safe_load(VALUES_YAML.read_text(encoding="utf-8"))["podiumd"]["zac"]["zgwApis"]
    client_ids = psql("openzaak", "SELECT client_ids FROM authorizations_applicatie;")
    assert zgw["clientId"] in client_ids
    secrets = dict(
        line.split("|", 1)
        for line in psql("openzaak", "SELECT identifier, secret FROM vng_api_common_jwtsecret;").splitlines()
    )
    assert secrets.get(zgw["clientId"]) == zgw["secret"]


def test_opennotificaties_has_objecten_kanaal_and_zac_abonnement(enabled_profiles):
    """podiumd.opennotificaties.configuration.data: the productaanvraag flow's kanaal and ZAC's abonnement."""
    if not enabled_profiles.get("opennotificaties"):
        pytest.skip("'opennotificaties' profile is not deployed")
    assert "objecten" in psql("opennotificaties", "SELECT naam FROM datamodel_kanaal;").split()
    assert (
        "https://zac.local/rest/notificaties"
        in psql("opennotificaties", "SELECT callback_url FROM datamodel_abonnement;").split()
    )
