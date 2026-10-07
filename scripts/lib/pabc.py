"""The guarded (re)creation of the pabc-migrations Job.

The Job empties PABC's database before loading the vendored seed data, so
recreating it over existing data destroys that data. The render excludes it
(lib.manifests); this is the only place that creates it.
"""

from lib import kube
from lib import manifests
from lib import process
from lib.manifests import PABC_MIGRATION_JOB
from lib.paths import NAMESPACE
from lib.process import UserError

MIGRATION_JOB_TEMPLATE = "charts/podiumd/charts/pabc/templates/migration-job.yaml"


def mapping_rows() -> str:
    """Row count of Pabc's mapping table as psql prints it; "" when the database or table is missing."""
    result = process.run(
        ["kubectl", "exec", "-n", NAMESPACE, "deploy/postgres", "--",
         "psql", "-U", "postgres", "-d", "Pabc", "-t", "-A", "-c", "SELECT count(*) FROM mapping;"],
        check=False,
    )  # fmt: skip
    return result.stdout.strip() if result.returncode == 0 else ""


def apply_migrations(*, force: bool) -> None:
    """Creates the Job unless it succeeded before or PABC has data; force wipes and reseeds anyway."""
    succeeded = process.run(
        ["kubectl", "get", "job", PABC_MIGRATION_JOB, "-n", NAMESPACE, "-o", "jsonpath={.status.succeeded}"],
        check=False,
    ).stdout.strip()
    if succeeded == "1" and not force:
        print(f"{PABC_MIGRATION_JOB} already exists and succeeded - leaving it alone.")
        print("(pass --force to delete and rerun it anyway, wiping and reseeding PABC's database)")
        return

    rows = mapping_rows()
    if not rows:
        print("Pabc database/schema doesn't exist yet (or isn't reachable) - safe to proceed, nothing to lose.")
    elif rows != "0" and not force:
        msg = (
            f"refusing to (re)apply {PABC_MIGRATION_JOB}: Pabc's 'mapping' table has {rows} row(s), "
            "and the Job empties the database before reseeding it. Re-run with --force to wipe and reseed."
        )
        raise UserError(msg)
    else:
        print(f"Pabc database has {rows} row(s) in 'mapping'; proceeding because --force was passed.")

    print(f"Deleting any existing {PABC_MIGRATION_JOB} and recreating it...")
    kube.kubectl_shown("delete", "job", PABC_MIGRATION_JOB, "-n", NAMESPACE, "--ignore-not-found")
    kube.kubectl_shown(
        "apply", "-n", NAMESPACE, "-f", "-", stdin=manifests.helm_template("--show-only", MIGRATION_JOB_TEMPLATE)
    )
    print("Waiting for the Job to complete...")
    kube.kubectl_shown(
        "wait", "--for=condition=complete", f"job/{PABC_MIGRATION_JOB}", "-n", NAMESPACE, "--timeout=120s"
    )
