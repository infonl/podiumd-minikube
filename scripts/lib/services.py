"""Access to the shared Postgres and Redis instances."""

from pathlib import Path

from lib import kube
from lib import polling
from lib import process
from lib.paths import NAMESPACE
from lib.process import UserError

POSTGRES_PORT = 5432
FORWARD_LOG = Path("/tmp/podiumd-minikube-postgres-port-forward.log")  # noqa: S108 - user-visible log
# Redis DBs the apps use (see values.yaml's settings.cache/settings.celery per app).
REDIS_DBS = range(15)


def expose_postgres(local_port: int) -> None:
    """Port-forwards svc/postgres to localhost:local_port and lists its databases."""
    kube.require_minikube_context()
    forward = f"svc/postgres {local_port}:{POSTGRES_PORT}"
    pid = process.running(f"kubectl .*port-forward.*{forward}")
    if pid:
        print(f"Already forwarding localhost:{local_port} -> postgres:{POSTGRES_PORT} (PID {pid}).")
    else:
        print(f"Starting 'kubectl port-forward' in the background (log: {FORWARD_LOG})...")
        process.spawn(
            ["kubectl", "port-forward", "svc/postgres", f"{local_port}:{POSTGRES_PORT}", "-n", NAMESPACE], FORWARD_LOG
        )

        def forwarding() -> bool:
            return FORWARD_LOG.is_file() and "Forwarding from" in FORWARD_LOG.read_text(encoding="utf-8")

        if not polling.wait_until(forwarding, timeout=10, interval=1):
            msg = f"port-forward did not come up within 10s; log: {FORWARD_LOG}"
            raise UserError(msg)
    databases = kube.kubectl(
        "exec", "-n", NAMESPACE, "deploy/postgres", "--", "psql", "-U", "postgres", "-tA", "-c",
        "select datname from pg_database where not datistemplate order by datname;",
    ).split()  # fmt: skip
    print(f"\nConnect with host localhost, port {local_port}, user postgres, password postgres, database postgres.")
    print("Databases (each app's own credentials from values.yaml also work):")
    print("\n".join(f"  - {name}" for name in databases))
    print(f"\nStop forwarding with: pkill -f 'kubectl.*port-forward.*{forward}'")


def _redis(*args: str) -> str:
    return kube.kubectl("exec", "-n", NAMESPACE, "deploy/redis", "--", "redis-cli", *args).strip()


def flush_redis(db: int | None) -> None:
    """FLUSHDB on db, or FLUSHALL when db is None (DBs mix caches and Celery queues per app)."""
    kube.require_minikube_context()
    if db is not None:
        print(f"DB {db}: {_redis('-n', str(db), 'DBSIZE')} key(s) before flush.")
        _redis("-n", str(db), "FLUSHDB")
        print(f"DB {db}: {_redis('-n', str(db), 'DBSIZE')} key(s) after flush.")
        return
    print("Key counts before flush:")
    for number in REDIS_DBS:
        print(f"  DB {number}: {_redis('-n', str(number), 'DBSIZE')} key(s)")
    _redis("FLUSHALL")
    print("Flushed all DBs. Key counts after flush:")
    for number in REDIS_DBS:
        print(f"  DB {number}: {_redis('-n', str(number), 'DBSIZE')} key(s)")
