"""The shared Postgres on a running cluster: databases added to the init SQL, and a new major.

Postgres runs the init SQL only on an empty data directory, so a database
added there later never reaches a provisioned cluster; and it cannot open a
data directory of another major.
"""

import re

from datetime import UTC
from datetime import datetime
from pathlib import Path

from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube import provision
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import VENDOR_DIR

INIT_SQL = VENDOR_DIR / "postgres" / "00-create-databases.sql"
# Values setup_configuration and podiumd's Jobs look rows up by, renamed to
# ExternalsPodiumD's: database -> (table, column) -> {old: new}. Without the
# rename they add a new row beside the old one (zgw_consumers_service.api_root
# is unique; catalogus ALG is found by its RSIN).
RENAMES: dict[str, dict[tuple[str, str], dict[str, str]]] = {
    "openformulieren": {
        ("zgw_consumers_service", "slug"): {
            "objects-api": "objecten-api",
            "objecttypes-api": "objecttypen-api",
            "openzaak-zaken": "openzaak-zaken-api",
            "openzaak-documenten": "openzaak-documenten-api",
            "openzaak-catalogi": "openzaak-catalogi-api",
        },
    },
    "openarchiefbeheer": {
        ("zgw_consumers_service", "slug"): {
            "zaken-test": "openzaak-zaken-api",
            "documenten-test": "openzaak-documenten-api",
            "catalogi-test": "openzaak-catalogi-api",
            "besluiten-test": "openzaak-besluiten-api",
            "selectielijst": "vng-selectielijst",
        },
    },
    "objecten": {
        ("zgw_consumers_service", "slug"): {
            "objecttypes-api": "objecttypen-api",
            "open-notificaties": "notificaties-api",
        },
    },
    "openzaak": {("catalogi_catalogus", "rsin"): {"123443210": "856683164"}},
}
# Old database (and its role) -> ExternalsPodiumD's name; the password becomes the new name.
RENAMED_DATABASES = {"objects": "objecten", "objecttypes": "objecttypen"}
# pg_dumpall output of each migration, kept as a backup.
DUMP_DIR = CHART_DIR / ".postgres-dumps"
DATA_ROOT = "/var/lib/postgresql/data"
_ROLE = re.compile(r"CREATE ROLE (\w+) ")
_DATABASE = re.compile(r'CREATE DATABASE "?(\w+)"? OWNER (\w+);')


def missing_sql(init_sql: str, databases: set[str], roles: set[str]) -> tuple[list[str], str]:
    """(names, script): the init SQL's statements for the databases not in databases.

    A database's statements are its CREATE ROLE (unless that role exists), its
    CREATE DATABASE and the statements after its `\\c` line.
    """
    statements = [line for line in init_sql.splitlines() if line.strip() and not line.startswith("--")]
    role_lines = {m.group(1): line for line in statements if (m := _ROLE.match(line))}
    missing: list[str] = []
    script: list[str] = []
    for line in statements:
        if (m := _DATABASE.match(line)) and m.group(1) not in databases:
            missing.append(m.group(1))
            if m.group(2) not in roles:
                script.append(role_lines[m.group(2)])
            script.append(line)
    connected = ""
    for line in statements:
        if line.startswith("\\c "):
            connected = line.split()[1]
            if connected in missing:
                script.append(line)
        elif connected in missing:
            script.append(line)
    return missing, "\n".join(script) + "\n" if script else ""


def _names(query: str) -> set[str]:
    return set(_psql(query).split())


def _psql(query: str) -> str:
    return kube.kubectl(
        "exec", "-n", NAMESPACE, kube.first_pod("app=postgres"), "--", "psql", "-U", "postgres", "-tA", "-c", query
    )  # fmt: skip


def major_of(image: str) -> int:
    """The Postgres major of a postgis/postgis:<major>-<postgis> image."""
    return int(image.rsplit(":", 1)[1].split("-")[0])


def restore_errors(stderr: str) -> list[str]:
    """Distinct psql ERROR lines, without those for objects the init SQL already created."""
    errors = {line.split("ERROR:", 1)[1].strip() for line in stderr.splitlines() if "ERROR:" in line}
    return sorted(e for e in errors if not e.endswith("already exists") and "transaction_timeout" not in e)


def migrate_major(render: manifests.Render) -> None:
    """Dumps every database and restores it into a fresh data directory when the rendered major differs.

    Clients are restarted afterwards; writes between the dump and the restore are lost.
    """
    rendered = [doc for doc in render.docs if doc.get("kind") == "Deployment" and manifests.name_of(doc) == "postgres"]
    if not rendered or not kube.exists("deployment/postgres"):
        return
    image = rendered[0]["spec"]["template"]["spec"]["containers"][0]["image"]
    wanted, running = major_of(image), int(_psql("show server_version_num").strip()) // 10000
    if wanted == running:
        return
    print(f"\nPostgres {running} -> {wanted}: dump, fresh data directory, restore...")
    DUMP_DIR.mkdir(exist_ok=True)
    dump = DUMP_DIR / f"pg{running}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sql"
    pod = kube.first_pod("app=postgres")
    dump.write_text(kube.kubectl("exec", "-n", NAMESPACE, pod, "--", "pg_dumpall", "-U", "postgres"), encoding="utf-8")
    print(f"Dumped to {dump}.")
    provision.load_images([image])
    kube.kubectl_shown("apply", "-n", NAMESPACE, "-f", "-", stdin=manifests.dump(rendered))
    kube.kubectl_shown("rollout", "status", "deployment/postgres", "-n", NAMESPACE, "--timeout=600s")
    restore(dump, wanted, render)


def restore(dump: Path, major: int, render: manifests.Render) -> None:
    """Restores dump into the running Postgres of major, removes other data directories, restarts the clients."""
    pod = kube.first_pod("app=postgres")
    result = process.run(
        ["kubectl", "exec", "-i", "-n", NAMESPACE, pod, "--", "psql", "-U", "postgres", "-q", "-f", "-"],
        stdin=dump.read_text(encoding="utf-8"),
    )
    if errors := restore_errors(result.stderr):
        msg = f"restore into Postgres {major} had errors (dump kept in {dump}): " + "; ".join(errors[:10])
        raise process.UserError(msg)
    kube.kubectl(
        "exec", "-n", NAMESPACE, pod, "--", "find", DATA_ROOT, "-mindepth", "1", "-maxdepth", "1",
        "!", "-name", f"pg{major}", "-exec", "rm", "-rf", "{}", "+",
    )  # fmt: skip
    # The Keycloak operator runs each Keycloak as a StatefulSet of the same name.
    kinds = {"Deployment": "deployment", "StatefulSet": "statefulset", "Keycloak": "statefulset"}
    clients = [
        f"{kinds[doc['kind']]}/{manifests.name_of(doc)}"
        for doc in render.docs
        if doc.get("kind") in kinds and manifests.name_of(doc) != "postgres"
    ]
    kube.kubectl_shown("rollout", "restart", "-n", NAMESPACE, *[c for c in clients if kube.exists(c)])
    print(f"Postgres {major} restored; old data directories removed, dump kept in {dump}.")


def create_missing_databases() -> None:
    """Creates the init SQL's databases that the running Postgres lacks."""
    if not kube.exists("deployment/postgres"):
        return
    databases = _names("select datname from pg_database;")
    roles = _names("select rolname from pg_roles;")
    missing, script = missing_sql(INIT_SQL.read_text(encoding="utf-8"), databases, roles)
    if not missing:
        return
    kube.kubectl_shown(
        "exec", "-i", "-n", NAMESPACE, kube.first_pod("app=postgres"), "--",
        "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-f", "-",
        stdin=script,
    )  # fmt: skip
    print(f"Created database(s) {', '.join(missing)} from {INIT_SQL.name}.")


def rename_databases() -> None:
    """Renames RENAMED_DATABASES and their roles in place; runs before create_missing_databases."""
    if not kube.exists("deployment/postgres"):
        return
    databases = _names("select datname from pg_database;")
    pod = kube.first_pod("app=postgres")
    for old, new in RENAMED_DATABASES.items():
        if old not in databases or new in databases:
            continue
        # Postgres renames no database with sessions; its clients reconnect after the deploy rolls them.
        kube.kubectl(
            "exec", "-i", "-n", NAMESPACE, pod, "--", "psql", "-U", "postgres", "-q", "-v", "ON_ERROR_STOP=1",
            "-v", f"old={old}", "-v", f"new={new}", "-f", "-",
            stdin=(
                'ALTER DATABASE :"old" ALLOW_CONNECTIONS false;\n'
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'old';\n"
                'ALTER DATABASE :"old" RENAME TO :"new";\n'
                'ALTER DATABASE :"new" ALLOW_CONNECTIONS true;\n'
                'ALTER ROLE :"old" RENAME TO :"new";\n'
                "ALTER ROLE :\"new\" PASSWORD :'new';\n"
            ),
        )  # fmt: skip
        print(f"Renamed database and role {old} to {new}.")


def rename_rows() -> None:
    """Applies RENAMES in the databases and tables that exist; an old value whose new one exists stays."""
    if not kube.exists("deployment/postgres"):
        return
    databases = _names("select datname from pg_database;")
    pod = kube.first_pod("app=postgres")
    for database, tables in RENAMES.items():
        if database not in databases:
            continue
        psql = ["exec", "-i", "-n", NAMESPACE, pod, "--", "psql", "-U", "postgres", "-d", database, "-tA"]
        for (table, column), renames in tables.items():
            # A database created this deploy has no tables until its app migrates.
            if not kube.kubectl(*psql, "-c", f"select to_regclass('{table}');").strip():
                continue
            names = ["-v", f"table={table}", "-v", f"column={column}"]
            values = kube.kubectl(*psql, *names, "-f", "-", stdin='select :"column" from :"table";\n').split()
            for old, new in renames.items():
                if old in values and new not in values:
                    kube.kubectl(
                        *psql, *names, "-v", "ON_ERROR_STOP=1", "-v", f"old={old}", "-v", f"new={new}", "-f", "-",
                        stdin='UPDATE :"table" SET :"column" = :\'new\' WHERE :"column" = :\'old\';\n',
                    )  # fmt: skip
                    print(f"Renamed {table}.{column} {old} to {new} in {database}.")
