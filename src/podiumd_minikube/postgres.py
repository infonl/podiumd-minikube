"""The shared Postgres on a running cluster: databases added to the init SQL, and a new major.

Postgres runs the init SQL only on an empty data directory, so a database
added there later never reaches a provisioned cluster; and it cannot open a
data directory of another major.
"""

import re

from dataclasses import dataclass
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
ALLOW_LIST_SQL = CHART_DIR / "files" / "postgres" / "00-azure-extension-allow-list.sql"
# Values setup_configuration and podiumd's Jobs look rows up by, renamed to
# ExternalsPodiumD's: database -> (table, column) -> {old: new}. Without the
# rename they add a new row beside the old one (zgw_consumers_service.api_root
# is unique; catalogus ALG is found by its RSIN).
# (table, column) -> old value -> new value
Renames = dict[tuple[str, str], dict[str, str]]
RENAMES: dict[str, Renames] = {
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
_DATABASE = re.compile(r"CREATE DATABASE (\w+);")


def missing_sql(init_sql: str, databases: set[str], roles: set[str]) -> tuple[list[str], str]:
    r"""(names, script): the init SQL's statements for the databases not in databases.

    A database's statements are its role's CREATE ROLE (same name; unless that
    role exists), its CREATE DATABASE and the statements after its `\c` lines.
    """
    statements = [line for line in init_sql.splitlines() if line.strip() and not line.startswith("--")]
    role_lines = {match.group(1): line for line in statements if (match := _ROLE.match(line))}
    created = {match.group(1): line for line in statements if (match := _DATABASE.match(line))}
    missing = [name for name in created if name not in databases]
    script: list[str] = []
    for name in missing:
        if name not in roles:
            script.append(role_lines[name])
        script.append(created[name])
    script.extend(line for line, database in _connected(statements) if database in missing)
    return missing, "".join(f"{line}\n" for line in script)


def _connected(statements: list[str]) -> list[tuple[str, str]]:
    r"""Each statement from the first `\c` line on, with the database the last `\c` line before it names."""
    database = ""
    found: list[tuple[str, str]] = []
    for line in statements:
        if line.startswith(r"\c "):
            database = line.split()[1]
        if database:
            found.append((line, database))
    return found


# One query for what every step reads: the major, each database's owner, the roles.
FACTS_SQL = (
    "select 'major|' || current_setting('server_version_num')::int / 10000"
    " union all select 'database|' || datname || '|' || pg_get_userbyid(datdba)"
    " from pg_database where datname <> 'template0'"
    " union all select 'role|' || rolname from pg_roles;"
)


@dataclass(frozen=True)
class Server:
    """The running Postgres: its pod, major, databases with their owners, and roles."""

    pod: str
    major: int
    owners: dict[str, str]
    roles: set[str]


def server() -> Server:
    """Return the running Postgres, read with one psql call."""
    pod = kube.first_pod("app=postgres")
    rows = [line.split("|") for line in _psql(pod, "-tA", "-c", FACTS_SQL).splitlines() if line]
    major = next(int(row[1]) for row in rows if row[0] == "major")
    owners = {row[1]: row[2] for row in rows if row[0] == "database"}
    return Server(pod, major, owners, {row[1] for row in rows if row[0] == "role"})


def _psql(pod: str, *args: str, stdin: str | None = None) -> str:
    """Return psql's stdout, run as postgres in pod with args."""
    return kube.kubectl("exec", "-i", "-n", NAMESPACE, pod, "--", "psql", "-U", "postgres", *args, stdin=stdin)


def major_of(image: str) -> int:
    """Return the Postgres major of a postgis/postgis:<major>-<postgis> image."""
    return int(image.rsplit(":", 1)[1].split("-", maxsplit=1)[0])


def restore_errors(stderr: str) -> list[str]:
    """Distinct psql ERROR lines, without those for objects the init SQL already created."""
    error_lines = [line for line in stderr.splitlines() if "ERROR:" in line]
    errors = {line.split("ERROR:", 1)[1].strip() for line in error_lines}
    return sorted(
        error for error in errors if not error.endswith("already exists") and "transaction_timeout" not in error
    )


def prepare(render: manifests.Render) -> None:
    """Run the database steps before an upgrade: major version, renames, missing databases, Azure's settings."""
    if not kube.is_present("deployment/postgres"):
        return
    current = server()
    # A step that changed the server is followed by a fresh read for the next one.
    if migrate_major(render, current) is not None:
        current = server()
    if rename_databases(current):
        current = server()
    if create_missing_databases(current):
        current = server()
    align_with_azure(current)
    rename_rows(current)


def migrate_major(render: manifests.Render, current: Server) -> Path | None:
    """Dump every database and restore it into a fresh data directory when the rendered major differs; return the dump.

    Clients are restarted afterwards; writes between the dump and the restore are lost.
    """
    rendered = render.named("Deployment", "postgres")
    if rendered is None:
        return None
    template = rendered["spec"]["template"]
    image = template["spec"]["containers"][0]["image"]
    wanted, running = major_of(image), current.major
    if wanted == running:
        return None
    print(f"\nPostgres {running} -> {wanted}: dump, fresh data directory, restore...")
    DUMP_DIR.mkdir(exist_ok=True)
    dump = DUMP_DIR / f"pg{running}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.sql"
    dump.write_text(
        kube.kubectl("exec", "-n", NAMESPACE, current.pod, "--", "pg_dumpall", "-U", "postgres"), encoding="utf-8"
    )
    print(f"Dumped to {dump}.")
    provision.load_images([image])
    kube.kubectl_shown("apply", "-n", NAMESPACE, "-f", "-", stdin=manifests.dump([rendered]))
    kube.kubectl_shown("rollout", "status", "deployment/postgres", "-n", NAMESPACE, "--timeout=600s")
    restore(dump, wanted, render)
    return dump


def restore(dump: Path, major: int, render: manifests.Render) -> None:
    """Restores dump into the running Postgres of major, removes other data directories, restarts the clients."""
    pod = kube.first_pod("app=postgres")
    result = process.run(
        ["kubectl", "exec", "-i", "-n", NAMESPACE, pod, "--", "psql", "-U", "postgres", "-q", "-f", "-"],
        stdin=dump.read_text(encoding="utf-8"),
    )
    errors = restore_errors(result.stderr)
    if errors:
        shown = "; ".join(errors[:10])
        msg = f"restore into Postgres {major} had errors (dump kept in {dump}): {shown}"
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
    kube.kubectl_shown(
        "rollout", "restart", "-n", NAMESPACE, *[client for client in clients if kube.is_present(client)]
    )
    print(f"Postgres {major} restored; old data directories removed, dump kept in {dump}.")


def create_missing_databases(current: Server) -> list[str]:
    """Create the init SQL's databases that the running Postgres lacks; return their names."""
    missing, script = missing_sql(INIT_SQL.read_text(encoding="utf-8"), set(current.owners), current.roles)
    if not missing:
        return []
    kube.kubectl_shown(
        "exec", "-i", "-n", NAMESPACE, current.pod, "--",
        "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-f", "-",
        stdin=script,
    )  # fmt: skip
    print(f"Created database(s) {', '.join(missing)} from {INIT_SQL.name}.")
    return missing


def align_with_azure(current: Server) -> None:
    """Give each app database to postgres with ALL granted to its owner, and install the extension allow-list.

    As the init SQL, for clusters provisioned before it; idempotent. Also drops
    openzaak's unused "uuid-ossp", which Azure does not allow.
    """
    owners = current.owners
    script = [r"\set ON_ERROR_STOP on"]
    for database, owner in owners.items():
        script += [f"\\c {database}", ALLOW_LIST_SQL.read_text(encoding="utf-8")]
        if owner != "postgres":
            script += [
                f"ALTER DATABASE {database} OWNER TO postgres;",
                f"GRANT ALL ON DATABASE {database} TO {owner};",
                f"GRANT ALL ON SCHEMA public TO {owner};",
            ]
    if "openzaak" in owners:
        script += [r"\c openzaak", 'DROP EXTENSION IF EXISTS "uuid-ossp";']
    _psql(current.pod, "-q", "-f", "-", stdin="\n".join(script))
    moved = sorted(database for database, owner in owners.items() if owner != "postgres")
    if moved:
        print(f"Databases given to postgres, with grants to their app role: {', '.join(moved)}")


def rename_databases(current: Server) -> list[str]:
    """Rename RENAMED_DATABASES and their roles in place; return the old names renamed."""
    renamed: list[str] = []
    for old, new in RENAMED_DATABASES.items():
        if old not in current.owners or new in current.owners:
            continue
        # Postgres renames no database with sessions; its clients reconnect after the deploy rolls them.
        _psql(
            current.pod, "-q", "-v", "ON_ERROR_STOP=1", "-v", f"old={old}", "-v", f"new={new}", "-f", "-",
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
        renamed.append(old)
    return renamed


def rename_rows(current: Server) -> None:
    """Apply RENAMES in the databases and tables that exist; an old value whose new one exists stays."""
    found = _column_values(current)
    for (database, table, column), values in found.items():
        for old, new in RENAMES[database][table, column].items():
            if old in values and new not in values:
                _psql(
                    current.pod, "-d", database, "-v", "ON_ERROR_STOP=1",
                    "-v", f"table={table}", "-v", f"column={column}", "-v", f"old={old}", "-v", f"new={new}", "-f", "-",
                    stdin='UPDATE :"table" SET :"column" = :\'new\' WHERE :"column" = :\'old\';\n',
                )  # fmt: skip
                print(f"Renamed {table}.{column} {old} to {new} in {database}.")


# A row of rename_rows_script's output: database|table|column|values.
_FIELDS = 4


def rename_rows_script(databases: set[str]) -> str:
    r"""Return the psql script that prints "database|table|column|values" for each RENAMES column that exists.

    A database created this deploy has no tables until its app migrates: `\if` skips them.
    """
    lines: list[str] = []
    for database, tables in RENAMES.items():
        if database not in databases:
            continue
        lines.append(f"\\c {database}")
        for table, column in tables:
            # Names from RENAMES, never from input.
            values = f"coalesce(string_agg({column}::text, ' '), '')"
            select = f"select '{database}|{table}|{column}|' || {values} from {table};"  # noqa: S608  # nosec B608
            lines.extend(
                [f"select to_regclass({table!r}) is not null as found \\gset", r"\if :found", select, r"\endif"]
            )
    return "".join(f"{line}\n" for line in lines)


def _column_values(current: Server) -> dict[tuple[str, str, str], set[str]]:
    """Return the values of each RENAMES column that exists, read with one psql call."""
    script = rename_rows_script(set(current.owners))
    if not script:
        return {}
    output = _psql(current.pod, "-q", "-tA", "-v", "ON_ERROR_STOP=1", "-f", "-", stdin=script)
    rows = [line.split("|", _FIELDS - 1) for line in output.splitlines()]
    complete = [row for row in rows if len(row) == _FIELDS]
    found: dict[tuple[str, str, str], set[str]] = {}
    for database, table, column, values in complete:
        found[database, table, column] = set(values.split())
    return found
