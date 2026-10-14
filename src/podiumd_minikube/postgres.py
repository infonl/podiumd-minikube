"""The shared Postgres on a running cluster: databases added to the init SQL, and a new major.

Postgres runs the init SQL only on an empty data directory, so a database
added there later never reaches a provisioned cluster; and it cannot open a
data directory of another major.
"""

import re

from datetime import UTC
from datetime import datetime

from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube import provision
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import VENDOR_DIR

INIT_SQL = VENDOR_DIR / "postgres" / "00-create-databases.sql"
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
    pod = kube.first_pod("app=postgres")
    result = process.run(
        ["kubectl", "exec", "-i", "-n", NAMESPACE, pod, "--", "psql", "-U", "postgres", "-q", "-f", "-"],
        stdin=dump.read_text(encoding="utf-8"),
    )
    if errors := restore_errors(result.stderr):
        msg = f"restore into Postgres {wanted} had errors (dump kept in {dump}): " + "; ".join(errors[:10])
        raise process.UserError(msg)
    kube.kubectl(
        "exec", "-n", NAMESPACE, pod, "--", "find", DATA_ROOT, "-mindepth", "1", "-maxdepth", "1",
        "!", "-name", f"pg{wanted}", "-exec", "rm", "-rf", "{}", "+",
    )  # fmt: skip
    clients = [
        f"{doc['kind'].lower()}/{manifests.name_of(doc)}"
        for doc in render.docs
        if doc.get("kind") in ("Deployment", "StatefulSet") and manifests.name_of(doc) != "postgres"
    ]
    kube.kubectl_shown("rollout", "restart", "-n", NAMESPACE, *[c for c in clients if kube.exists(c)])
    print(f"Postgres {wanted} restored; old data directories removed, dump kept in {dump}.")


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
