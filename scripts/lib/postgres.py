"""The vendored init SQL's databases, created on a running Postgres.

Postgres runs the init SQL only on an empty data directory, so a database
added there later never reaches a provisioned cluster.
"""

import re

from lib import kube
from lib.paths import NAMESPACE
from lib.paths import VENDOR_DIR

INIT_SQL = VENDOR_DIR / "postgres" / "00-create-databases.sql"
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
    return set(
        kube.kubectl(
            "exec", "-n", NAMESPACE, kube.first_pod("app=postgres"), "--", "psql", "-U", "postgres", "-tA", "-c", query
        ).split()
    )


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
