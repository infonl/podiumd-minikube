#!/usr/bin/env bash
# Renders and applies this chart to whatever cluster `kubectl` is currently
# configured against - the "render + apply the chart" step
# scripts/provision-cluster.sh points at as its own next step.
#
# Uses `helm template | strip-image-digests.py | disable-service-links.py |
# exclude-pabc-migration-job.py | kubectl apply`, not `helm install`/`helm
# upgrade`: Helm's own release record embeds the entire
# resolved chart (including the ~3.87MB podiumd dependency), which exceeds
# Kubernetes' hardcoded 3MB API request-size limit (see plan.md's step 4
# notes - there's no flag to raise this limit in current Kubernetes
# versions, and attempting to add one crash-looped the whole control plane
# live). One consequence: Helm's own install/upgrade hooks never fire (they
# require a live Helm release, which this workflow never creates) -
# templates/storage-hooks.yaml's PV/PVC pre-provisioning depends on being
# applied *before* the rest of the manifest instead (see that file's own
# comments for the immutable-spec-protection mechanism this relies on),
# which is why this script applies it as a separate, earlier step rather
# than one `kubectl apply -f` over everything at once.
#
# The pabc-migrations Job is excluded from this general apply for a
# different reason - not immutability, but because it's genuinely
# destructive to create unguarded (see scripts/apply-pabc-migrations.sh's
# own header) - and applied via that guarded script instead, as its own
# explicit step below.
#
# Usage:
#   ./scripts/deploy.sh            # core profile only (matches values.yaml's own default)
#   ./scripts/deploy.sh --full     # every optional profile enabled too (objecten, objecttypen,
#                                  # opennotificaties, openarchiefbeheer, openformulieren, metrics, itest)
#   ./scripts/deploy.sh --set some.other=value   # any extra --set flags are passed through
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_NAME="podiumd-minikube"
NAMESPACE="podiumd-minikube"

EXTRA_SETS=()
if [ "${1:-}" = "--full" ]; then
  shift
  EXTRA_SETS=(
    --set itest.enabled=true
    --set objecten.enabled=true --set podiumd.objecten.enabled=true
    --set podiumd.objecttypen.enabled=true
    --set opennotificaties.enabled=true --set podiumd.opennotificaties.enabled=true
    --set openarchiefbeheer.enabled=true --set podiumd.openarchiefbeheer.enabled=true
    --set openformulieren.enabled=true --set podiumd.openformulieren.enabled=true
    --set metrics.enabled=true
  )
fi

render() {
  helm template "${RELEASE_NAME}" "${CHART_DIR}" -n "${NAMESPACE}" "${EXTRA_SETS[@]}" "$@" \
    | python3 "${CHART_DIR}/scripts/lib/strip-image-digests.py" \
    | python3 "${CHART_DIR}/scripts/lib/disable-service-links.py" \
    | python3 "${CHART_DIR}/scripts/lib/exclude-pabc-migration-job.py"
}

echo "Ensuring namespace '${NAMESPACE}' exists..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - > /dev/null

# storage-permissions-fix's own volume mount list depends on which profiles
# are enabled (see storage-hooks.yaml) - Jobs are immutable, so re-running
# this script with a *different* set of profiles than whatever's currently
# deployed would otherwise fail outright on this one resource. Unlike
# pabc-migrations (see scripts/apply-pabc-migrations.sh's own guard, and
# why it exists), this Job is safe to unconditionally delete and recreate
# any time - it only ever does an idempotent chmod, nothing it could lose.
kubectl delete job storage-permissions-fix -n "${NAMESPACE}" --ignore-not-found

echo "Applying storage-hook PV/PVC pairs first (see this script's own comment for why)..."
render -s templates/storage-hooks.yaml | kubectl apply -n "${NAMESPACE}" -f -

if kubectl get job storage-permissions-fix -n "${NAMESPACE}" > /dev/null 2>&1; then
  echo "Waiting for the storage-permissions-fix Job to complete..."
  kubectl wait --for=condition=complete job/storage-permissions-fix -n "${NAMESPACE}" --timeout=60s
fi

echo
echo "Applying the full manifest..."
set +e
apply_output="$(render | kubectl apply -n "${NAMESPACE}" -f - 2>&1)"
apply_exit=$?
set -e
echo "${apply_output}"

# Expected failures, not real ones: podiumd's own competing Azure-CSI
# PV/PVC objects (one pair per enabled app covered by storage-hooks.yaml)
# get rejected by Kubernetes' immutable-spec check every time, on purpose -
# that's the whole mechanism protecting our own pre-provisioned pair from
# being overwritten (see storage-hooks.yaml). Compute exactly how many of
# those to expect from the same render used above, rather than a hardcoded
# number, so this stays correct regardless of which profiles are enabled.
expected_errors=$(( $(render -s templates/storage-hooks.yaml | grep -c "^kind: PersistentVolume$") * 2 ))
actual_errors="$(grep -c "error when applying patch" <<< "${apply_output}" || true)"

echo
if [ "${apply_exit}" -eq 0 ]; then
  echo "Applied cleanly."
elif [ "${actual_errors}" -eq "${expected_errors}" ]; then
  echo "${actual_errors} \"spec is immutable\" error(s) above - expected (podiumd's own"
  echo "competing storage objects being correctly rejected), not a real failure."
else
  echo "WARNING: ${actual_errors} apply error(s), expected exactly ${expected_errors} from" >&2
  echo "the known immutable-spec case - re-check the output above for something new." >&2
  exit 1
fi

echo
echo "Applying pabc-migrations (guarded - see scripts/apply-pabc-migrations.sh)..."
"${CHART_DIR}/scripts/apply-pabc-migrations.sh"

echo
echo "Done. Next: ./scripts/setup-tunnel.sh for external reachability, or run"
echo "the suite in tests/ to verify."
