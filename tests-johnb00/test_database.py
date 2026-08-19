"""
Postgres schema/fixture checks - adapted from ../tests/test_database.py.

johnb00 has no in-cluster Postgres pod to `kubectl exec` into (it's an
external Azure Postgres Flexible Server) and no Key Vault access to any
admin/superuser credential from outside CI. Instead, every check here goes
through `manage.py shell` inside each app's own pod, using that app's
already-configured Django DB connection - no separate credential needed at
all. Confirmed live: `pg_database` is world-readable even from a
non-superuser app role, so "all expected databases exist" still works via
any one app's connection (openzaak's, here) despite not being the
Postgres admin.

EXPECTED_DATABASES and POSTGIS_DATABASES were both derived by querying the
live server directly, not carried over from the minikube reference (whose
names differ: "objects"/"objecttypes"/"Pabc" there vs. "objecten"/
"objecttypen"/"pabc" here, and opennotificaties has PostGIS on minikube but
NOT on johnb00 - confirmed live, empty pg_extension result).
"""

import pytest

from conftest import NAMESPACE, kubectl

EXPECTED_DATABASES = {
    "zaakafhandelcomponent",
    "keycloak",
    "openzaak",
    "openklant",
    "objecten",
    "objecttypen",
    "opennotificaties",
    "openarchiefbeheer",
    "openformulieren",
    "openforms",
    "openbeheer",
    "referentielijsten",
    "pabc",
    "datamigratie",
}

# Confirmed live via `manage.py shell` in each app's own pod - only these
# two actually have the postgis extension installed on johnb00.
# opennotificaties does NOT (confirmed live, unlike the minikube reference).
# openarchiefbeheer is `enabled: false` (no pod) so can't be checked at all.
POSTGIS_DATABASES = {"openzaak", "objecten"}

# app -> pod-selector prefix, since opennotificaties' Helm release uses
# fullnameOverride: notificaties (see values/johnb00/podiumd.yaml) - its
# deployment is literally named "notificaties", not "opennotificaties".
APP_DEPLOYMENTS = {
    "openzaak": "openzaak",
    "objecten": "objecten",
    "opennotificaties": "notificaties",
}


def _manage_shell(deployment, code):
    return kubectl(
        "exec",
        "-n",
        NAMESPACE,
        f"deploy/{deployment}",
        "--",
        "python",
        "/app/src/manage.py",
        "shell",
        "-c",
        code,
    ).strip().splitlines()[-1]


@pytest.fixture(scope="module")
def existing_databases():
    raw = _manage_shell(
        "openzaak",
        "from django.db import connection\n"
        "with connection.cursor() as c:\n"
        "    c.execute('SELECT datname FROM pg_database WHERE datistemplate = false;')\n"
        "    print(','.join(r[0] for r in c.fetchall()))\n",
    )
    return set(raw.split(","))


def test_all_expected_databases_exist(existing_databases):
    missing = EXPECTED_DATABASES - existing_databases
    assert not missing, f"missing databases: {missing}"


@pytest.mark.parametrize("database", sorted(POSTGIS_DATABASES))
def test_postgis_extension_installed(database, existing_databases, enabled_profiles):
    if database not in existing_databases:
        pytest.skip(f"database '{database}' does not exist")
    deployment = APP_DEPLOYMENTS[database]
    result = _manage_shell(
        deployment,
        "from django.db import connection\n"
        "with connection.cursor() as c:\n"
        "    c.execute(\"SELECT extname FROM pg_extension WHERE extname = 'postgis';\")\n"
        "    print(c.fetchone()[0] if c.rowcount else '')\n",
    )
    assert result == "postgis", f"postgis extension missing from '{database}'"


def test_openzaak_zac_client_credentials_seeded(existing_databases):
    """
    ZAC's own ZGW client credentials (identifier 'zac') must exist in Open
    Zaak's authorizations_applicatie/vng_api_common_jwtsecret tables, or
    ZAC can never authenticate against Open Zaak's APIs at all. Checks
    existence only, not the secret's actual value (avoid ever printing a
    live credential from a test run) - the expected value is the plaintext
    `podiumd.openzaak.configuration.data`'s vng_api_common_credentials
    entry for identifier "zac" in values/johnb00/podiumd.yaml, not
    re-verified byte-for-byte here.
    """
    if "openzaak" not in existing_databases:
        pytest.skip("openzaak database does not exist")
    applicatie_client_ids = _manage_shell(
        "openzaak",
        "from django.db import connection\n"
        "with connection.cursor() as c:\n"
        "    c.execute('SELECT client_ids FROM authorizations_applicatie;')\n"
        "    print(c.fetchall())\n",
    )
    assert "'zac'" in applicatie_client_ids or '"zac"' in applicatie_client_ids

    jwtsecret_identifiers = _manage_shell(
        "openzaak",
        "from django.db import connection\n"
        "with connection.cursor() as c:\n"
        "    c.execute('SELECT identifier FROM vng_api_common_jwtsecret;')\n"
        "    print([r[0] for r in c.fetchall()])\n",
    )
    assert "zac" in jwtsecret_identifiers
