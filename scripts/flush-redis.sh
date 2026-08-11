#!/usr/bin/env bash
# Flushes the shared Redis instance (templates/redis/deployment.yaml) - one
# unauthenticated instance for the whole stack, the same "one shared
# service instead of one per app" pattern as Postgres
# (scripts/expose-postgres.sh), just addressed by DB *number* instead of
# database *name*.
#
# There's no clean way to flush "just the caches": DB numbers are shared
# across multiple apps' cache/axes keys AND, for openarchiefbeheer and
# openformulieren specifically, their Celery broker/result-backend too
# (confirmed against values.yaml's own settings.cache/settings.celery
# blocks per app):
#   DB 0: openzaak/openklant/objecten/objecttypen/opennotificaties cache+axes,
#         PLUS openarchiefbeheer's cache+axes+choices AND its Celery
#         broker/result-backend (all on db0 for that app - no separation)
#   DB 1: openzaak/objecten Celery broker/result-backend, opennotificaties'
#         configured-but-unused Celery db (its real broker is RabbitMQ)
#   DB 2: openklant's configured-but-inactive Celery db (its worker stays
#         at replicaCount 0), PLUS openformulieren's cache+axes+
#         portalLocker AND its Celery broker/result-backend (all on db2 -
#         same no-separation situation as openarchiefbeheer)
#
# Default behaviour is a full FLUSHALL - clears every DB, cache and Celery
# state alike. Pass --db N to flush a single DB instead if you specifically
# know that's what you want (see the breakdown above for what shares it).
#
# Usage:
#   ./scripts/flush-redis.sh            # FLUSHALL - everything, all DBs
#   ./scripts/flush-redis.sh --db 0     # FLUSHDB on just DB 0
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="podiumd-minikube"
DEPLOYMENT="redis"

source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"

DB=""
if [ "${1:-}" = "--db" ]; then
  DB="${2:?--db requires a database number}"
fi

redis_cli() {
  kubectl exec -n "${NAMESPACE}" "deploy/${DEPLOYMENT}" -- redis-cli "$@"
}

if [ -n "${DB}" ]; then
  before="$(redis_cli -n "${DB}" DBSIZE)"
  echo "DB ${DB}: ${before} key(s) before flush."
  redis_cli -n "${DB}" FLUSHDB > /dev/null
  after="$(redis_cli -n "${DB}" DBSIZE)"
  echo "DB ${DB}: ${after} key(s) after flush."
else
  echo "Key counts before flush (DBs 0-2, the only ones any app here uses):"
  for i in 0 1 2; do
    echo "  DB ${i}: $(redis_cli -n "${i}" DBSIZE) key(s)"
  done
  redis_cli FLUSHALL > /dev/null
  echo "Flushed all DBs."
  echo "Key counts after flush:"
  for i in 0 1 2; do
    echo "  DB ${i}: $(redis_cli -n "${i}" DBSIZE) key(s)"
  done
fi
