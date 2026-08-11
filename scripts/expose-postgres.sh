#!/usr/bin/env bash
# Port-forwards the shared Postgres instance to localhost:5432, so a GUI
# tool (pgAdmin, DBeaver, TablePlus, ...) can connect directly.
#
# There is only ONE Postgres server in this stack (templates/postgres/
# deployment.yaml) - every app's own database (openzaak, openklant,
# objects, objecttypes, opennotificaties, openarchiefbeheer,
# openformulieren, "Pabc", zac, keycloak) lives on it as a separate
# database owned by that app's own role, not a separate server per app.
# The POSTGRES_USER/POSTGRES_PASSWORD superuser (postgres/postgres,
# hardcoded in that same deployment.yaml) can see and query all of them
# from one connection - point your GUI tool at that, then switch
# databases inside it, rather than needing one connection per app.
#
# Usage:
#   ./scripts/expose-postgres.sh
#   ./scripts/expose-postgres.sh 5433   # forward to a different local port,
#                                        # e.g. if you already run Postgres
#                                        # locally on 5432
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="podiumd-minikube"
SERVICE="postgres"
REMOTE_PORT=5432
LOCAL_PORT="${1:-5432}"
FORWARD_LOG="/tmp/podiumd-minikube-postgres-port-forward.log"

source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"

existing_pid="$(pgrep -f "kubectl .*port-forward.*svc/${SERVICE} ${LOCAL_PORT}:${REMOTE_PORT}" | head -1 || true)"
if [ -n "${existing_pid}" ]; then
  echo "Already forwarding localhost:${LOCAL_PORT} -> ${SERVICE}:${REMOTE_PORT} (PID ${existing_pid})."
else
  echo "Starting 'kubectl port-forward' in the background (log: ${FORWARD_LOG})..."
  nohup kubectl port-forward "svc/${SERVICE}" "${LOCAL_PORT}:${REMOTE_PORT}" -n "${NAMESPACE}" \
    > "${FORWARD_LOG}" 2>&1 &
  disown
  # port-forward's own "Forwarding from ..." line only appears once the
  # tunnel is actually accepting connections - polling for it instead of a
  # fixed sleep, same reasoning as setup-tunnel.sh's own external-IP wait.
  echo -n "Waiting for it to come up"
  elapsed=0
  while [ "${elapsed}" -lt 10 ]; do
    grep -q "Forwarding from" "${FORWARD_LOG}" 2>/dev/null && break
    echo -n "."
    sleep 1
    elapsed=$((elapsed + 1))
  done
  echo
  if ! grep -q "Forwarding from" "${FORWARD_LOG}" 2>/dev/null; then
    echo "Timed out waiting for the port-forward to come up. Log (${FORWARD_LOG}):" >&2
    cat "${FORWARD_LOG}" >&2
    exit 1
  fi
fi

echo
echo "Connect with:"
echo "  Host:     localhost"
echo "  Port:     ${LOCAL_PORT}"
echo "  Username: postgres"
echo "  Password: postgres"
echo "  Database: postgres   (connect here first, then switch - see below)"
echo
echo "Databases on this instance (each app's own credentials also work,"
echo "matching values.yaml's settings.database block for that app, but the"
echo "postgres/postgres superuser above can reach all of them from one"
echo "connection):"
kubectl exec -n "${NAMESPACE}" "deploy/${SERVICE}" -- psql -U postgres -tA -c \
  "select datname from pg_database where not datistemplate order by datname;" 2>/dev/null \
  | sed 's/^/  - /'
echo
echo "Stop forwarding with: pkill -f 'kubectl.*port-forward.*svc/${SERVICE} ${LOCAL_PORT}:${REMOTE_PORT}'"
