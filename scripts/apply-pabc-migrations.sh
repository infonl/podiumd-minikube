#!/usr/bin/env bash
# Safely (re)applies the pabc-migrations Job - the ONE place this project
# ever creates/recreates it, instead of ad-hoc `kubectl delete job` +
# `kubectl apply` (which is how this Job actually got recreated a few times
# during live development, e.g. after an image tag fix and after the
# mapping-data fix).
#
# This Job is genuinely dangerous to just recreate blindly: it is NOT
# idempotent - confirmed live, its own container clears PABC's database
# before reloading the vendored seed dataset. Re-running it against a
# database that already has real data (whether from a prior successful
# migration run, or roles/mappings added later through PABC's own API/UI)
# would silently destroy that data and replace it with just the seed set.
#
# Since the podiumd/pabc chart's own Job template has no idempotency or
# "skip if already seeded" mechanism of its own (no command override point
# exists to add one), and `kubectl apply` on an unchanged EXISTING Job is
# already a safe no-op (Jobs are immutable - this only matters once the
# Job has been deleted, for any reason, and needs recreating), this script
# is the guard for that one dangerous case.
#
# Usage:
#   ./scripts/apply-pabc-migrations.sh          # refuses if Pabc already has data
#   ./scripts/apply-pabc-migrations.sh --force   # wipes and reseeds anyway
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="podiumd-minikube"
JOB_NAME="pabc-migrations-1"
FORCE=false
[ "${1:-}" = "--force" ] && FORCE=true

existing_status="$(kubectl get job "${JOB_NAME}" -n "${NAMESPACE}" \
  -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"

if [ "${existing_status}" = "1" ] && [ "${FORCE}" = false ]; then
  echo "pabc-migrations-1 already exists and succeeded - leaving it alone."
  echo "(pass --force to delete and rerun it anyway, wiping and reseeding PABC's database)"
  exit 0
fi

row_count="$(kubectl exec -n "${NAMESPACE}" deploy/postgres -- \
  psql -U postgres -d Pabc -t -A -c "SELECT count(*) FROM mapping;" 2>/dev/null || true)"

if [ -z "${row_count}" ]; then
  echo "Pabc database/schema doesn't exist yet (or isn't reachable) - safe to proceed, nothing to lose."
elif [ "${row_count}" != "0" ] && [ "${FORCE}" = false ]; then
  echo "Refusing to (re)apply pabc-migrations-1: the Pabc database already"
  echo "has ${row_count} row(s) in 'mapping'. This Job clears the database"
  echo "before reloading its seed dataset - reapplying would destroy"
  echo "whatever is there now (the original seed data, or anything added"
  echo "since) and replace it with just the vendored dataset."
  echo
  echo "Re-run with --force if you're sure you want to wipe and reseed it."
  exit 1
else
  echo "Pabc database has ${row_count} row(s) in 'mapping'; proceeding because --force was passed."
fi

echo "Deleting any existing ${JOB_NAME} and recreating it..."
kubectl delete job "${JOB_NAME}" -n "${NAMESPACE}" --ignore-not-found

helm template podiumd-minikube "${CHART_DIR}" -n "${NAMESPACE}" \
  --show-only charts/podiumd/charts/pabc/templates/migration-job.yaml \
  | kubectl apply -n "${NAMESPACE}" -f -

echo "Waiting for the Job to complete..."
kubectl wait --for=condition=complete "job/${JOB_NAME}" -n "${NAMESPACE}" --timeout=120s
