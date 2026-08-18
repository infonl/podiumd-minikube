#!/usr/bin/env bash
# Renders and applies this chart to whatever cluster `kubectl` is currently
# configured against - the "render + apply the chart" step
# scripts/provision-cluster.sh points at as its own next step.
#
# First syncs charts/*.tgz against .podiumd-versions.yaml - the only place
# either dependency's version is recorded (Chart.yaml no longer holds a
# real one at all) - refusing with a clear message if you haven't run
# scripts/set-podiumd-version.sh yet. See
# scripts/lib/podiumd-dependency.sh's own header for the full mechanism.
# Chart.yaml/Chart.lock's own on-disk content is never left changed by
# this.
#
# Uses `helm template | strip-image-digests.py | disable-service-links.py |
# exclude-pabc-migration-job.py | exclude-helm-test-hooks.py |
# split-large-configmaps.py | kubectl apply`, not `helm install`/`helm
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
# destructive to create unguarded (see scripts/lib/apply-pabc-migrations.sh's
# own header) - and applied via that guarded script instead, as its own
# explicit step below.
#
# Helm test hooks (e.g. monitoring-logging's own bundled grafana-test, when
# monitoringLogging.enabled=true) are excluded for a third reason - see
# exclude-helm-test-hooks.py's own header - there's no real Helm release for
# `helm test` to ever run them properly, and applying one as a plain
# resource just leaves a permanently-failed Pod behind.
#
# Large ConfigMaps (currently only monitoring-logging's own bundled Grafana
# dashboards, when monitoringLogging.enabled=true) are excluded for a fourth,
# unrelated reason - see split-large-configmaps.py's own header - and applied
# via `kubectl apply --server-side` separately below instead, which doesn't
# hit the same limit.
#
# When monitoringLogging.enabled=true, this also applies that dependency's
# own CustomResourceDefinitions first (Prometheus/PrometheusRule/
# ServiceMonitor/PodMonitor/...) via apply-monitoring-logging-crds.sh - see
# its own header for why `helm template`, unlike `helm install`, never
# renders a chart's `crds/` directory at all.
#
# After applying, also prunes any Deployment/StatefulSet/DaemonSet running
# in the namespace that isn't part of this render at all (see
# prune-orphaned-workloads.py's own header) - `kubectl apply` alone never
# deletes resources that dropped out of the render (unlike `helm upgrade`),
# so re-running this script with different profile/--set flags than
# whatever's currently deployed would otherwise leave the old ones running
# forever (e.g. toggling monitoringLogging.enabled leaves the other
# implementation's Grafana/Tempo/otel-collector running right alongside the
# new ones).
#
# Then, if the objecten profile is actually part of the (post-prune) live
# state, automatically runs scripts/lib/seed-fixtures.sh - safe to do on every
# single deploy.sh run, not just the first, since that script's own seed()
# calls are each independently idempotent (skip straight past the
# wait/copy/loaddata work once their target model already has data - see
# its own header).
#
# Usage:
#   ./scripts/deploy.sh            # core profile only (matches values.yaml's own default)
#   ./scripts/deploy.sh --full     # every optional profile enabled too (objecten, objecttypen,
#                                  # opennotificaties, openarchiefbeheer, openformulieren, metrics, wiremock)
#   ./scripts/deploy.sh --set some.other=value   # any extra --set flags are passed through
#   ./scripts/deploy.sh --force-prune   # skip prune-orphaned-workloads.py's own refusal to
#                                        # delete a suspiciously large number of resources -
#                                        # see that script's own header for why it exists.
#                                        # Combine with --full: "--full --force-prune"
#
# Which implementation backs the metrics profile - templates/metrics/'s raw
# resources, or the heavier monitoring-logging dependency
# (loki/alloy/grafana/tempo/kube-prometheus-stack) - is NOT a deploy.sh flag.
# It's read straight from values.yaml's own monitoringLogging.enabled (set
# it persistently via scripts/set-podiumd-version.sh, or edit values.yaml by
# hand) - a separate flag here would just be a second way to say the same
# thing and could disagree with it. Whichever way it resolves, this script
# automatically does the two things that implementation needs at deploy
# time that Helm's own templates block can't express: its CRDs applied
# first (see apply-monitoring-logging-crds.sh's own header for why `helm
# template` never renders a chart's crds/ directory), and ZAC's OTLP
# endpoint repointed at its otel-collector Service instead of the raw
# templates' one. metrics.enabled itself is unaffected either way - still
# its own independent flag (on by default with --full) - monitoringLogging
# only decides *which* implementation runs once that profile is on, not
# whether it's on at all.
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_NAME="podiumd-minikube"
NAMESPACE="podiumd-minikube"

source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"
source "${CHART_DIR}/scripts/lib/monitoring-logging-enabled.sh"
source "${CHART_DIR}/scripts/lib/podiumd-dependency.sh"

# Ensures charts/*.tgz matches .podiumd-versions.yaml before anything
# below reads from it - including detect-objecten-shape.sh just below,
# which inspects the actual podiumd tarball's contents to tell the
# classic/merged shapes apart. Exits with a clear message if that file
# doesn't have an entry for both dependencies yet (see
# scripts/set-podiumd-version.sh). Fixed a real, previously-silent gap
# too: a podiumd version bump committed to Chart.yaml/Chart.lock (back
# when those files still held the real version) used to only take effect
# once someone happened to run `helm dependency update` by hand
# afterward - confirmed live, this deploy.sh used to render against a
# stale, already-fetched older version otherwise, with no warning at
# all. Cheap to call unconditionally now: skips its own network
# round-trip entirely once the target tarball is already present (see
# sync_podiumd_dependencies's own header).
sync_podiumd_dependencies

EXTRA_SETS=()
FORCE_PRUNE=false
# Default/exported so it's always defined for
# fixup-merged-objecten-shape.py's own os.environ lookup below,
# even on a plain (non---full) run where detect-objecten-shape.sh never
# runs at all.
export OBJECTEN_MERGED=false
while true; do
  case "${1:-}" in
    --full)
      shift
      source "${CHART_DIR}/scripts/lib/detect-objecten-shape.sh"
      export OBJECTEN_MERGED
      EXTRA_SETS+=(
        --set wiremock.enabled=true
        --set objecten.enabled=true --set podiumd.objecten.enabled=true
        "${OBJECTEN_SHAPE_SETS[@]}"
        --set opennotificaties.enabled=true --set podiumd.opennotificaties.enabled=true
        --set openarchiefbeheer.enabled=true --set podiumd.openarchiefbeheer.enabled=true
        --set openformulieren.enabled=true --set podiumd.openformulieren.enabled=true
        --set metrics.enabled=true
      )
      ;;
    --force-prune)
      # Forwarded to prune-orphaned-workloads.py's own --force - see its
      # header for why it refuses a suspiciously large prune by default.
      # Confirmed live to matter: running plain deploy.sh (core profile
      # only) against a cluster actually running --full once deleted the
      # entire optional-profile stack in one shot, the exact scenario that
      # guard now exists to catch.
      shift
      FORCE_PRUNE=true
      ;;
    *)
      break
      ;;
  esac
done

MONITORING_LOGGING_REQUESTED=false
if monitoring_logging_enabled "${CHART_DIR}/values.yaml"; then
  MONITORING_LOGGING_REQUESTED=true
  # ZAC's OTLP endpoint repointed at monitoring-logging's own otel-collector
  # Service (named "<release>-opentelemetry-collector" by that chart's own
  # fullname template, confirmed live via `helm template` against the real
  # upstream chart) - values.yaml's own monitoringLogging comment already
  # covers metrics.enabled/monitoringLogging.enabled moving together; this
  # is the one piece Helm's own templates can't derive on their own.
  EXTRA_SETS+=(
    --set "podiumd.zac.opentelemetry_zaakafhandelcomponent.endpoint=http://${RELEASE_NAME}-opentelemetry-collector:4317"
  )
fi
# Remaining args (e.g. `--set some.other=value`, per this script's own
# usage comment) are forwarded to every render() call below via "$@" -
# render()'s own "$@" is its *call-site* args (`-s templates/...` for the
# storage-hooks-only renders), so these have to be appended there, not
# read again inside render() itself.
EXTRA_ARGS=("$@")

LARGE_CONFIGMAPS_FILE="$(mktemp)"
trap 'rm -f "${LARGE_CONFIGMAPS_FILE}"' EXIT
export LARGE_CONFIGMAPS_OUT="${LARGE_CONFIGMAPS_FILE}"

render() {
  helm template "${RELEASE_NAME}" "${CHART_DIR}" -n "${NAMESPACE}" "${EXTRA_SETS[@]}" "${EXTRA_ARGS[@]}" "$@" \
    | python3 "${CHART_DIR}/scripts/lib/strip-image-digests.py" \
    | python3 "${CHART_DIR}/scripts/lib/disable-service-links.py" \
    | python3 "${CHART_DIR}/scripts/lib/exclude-pabc-migration-job.py" \
    | python3 "${CHART_DIR}/scripts/lib/exclude-helm-test-hooks.py" \
    | python3 "${CHART_DIR}/scripts/lib/exclude-zac-bundled-otel-collector.py" \
    | python3 "${CHART_DIR}/scripts/lib/fixup-merged-objecten-shape.py" \
    | python3 "${CHART_DIR}/scripts/lib/split-large-configmaps.py"
}

echo "Ensuring namespace '${NAMESPACE}' exists..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - > /dev/null

# storage-permissions-fix's own volume mount list depends on which profiles
# are enabled (see storage-hooks.yaml) - Jobs are immutable, so re-running
# this script with a *different* set of profiles than whatever's currently
# deployed would otherwise fail outright on this one resource. Unlike
# pabc-migrations (see scripts/lib/apply-pabc-migrations.sh's own guard, and
# why it exists), this Job is safe to unconditionally delete and recreate
# any time - it only ever does an idempotent chmod, nothing it could lose.
kubectl delete job storage-permissions-fix -n "${NAMESPACE}" --ignore-not-found

echo "Applying storage-hook PV/PVC pairs first (see this script's own comment for why)..."
render -s templates/storage-hooks.yaml | kubectl apply -n "${NAMESPACE}" -f -

if kubectl get job storage-permissions-fix -n "${NAMESPACE}" > /dev/null 2>&1; then
  echo "Waiting for the storage-permissions-fix Job to complete..."
  kubectl wait --for=condition=complete job/storage-permissions-fix -n "${NAMESPACE}" --timeout=60s
fi

if [ "${MONITORING_LOGGING_REQUESTED}" = true ]; then
  echo
  echo "Applying monitoring-logging's own CRDs first (see apply-monitoring-logging-crds.sh's own comment for why this is needed)..."
  "${CHART_DIR}/scripts/lib/apply-monitoring-logging-crds.sh"
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
# Counts every *kind* of apply failure seen so far, not just the immutable-
# spec one - confirmed live that matching a single substring ("error when
# applying patch") is fragile: other, genuinely different failures (a
# ServiceMonitor rejected by a strict-decoding CRD-schema mismatch; a
# PodMonitor whose own metadata.namespace is hardcoded to "kube-system",
# conflicting with this script's own `-n podiumd-minikube`) use entirely
# different wording and slipped through uncounted the same way the
# CRDs-missing case first did. Server-side rejections all start "Error from
# server (...)"; client-side ones (kubectl refuses before even reaching the
# API server) don't share that prefix, hence the second pattern. (Tried
# computing this structurally instead - total resources in the render minus
# successful "created"/"configured"/"unchanged" result lines - but that
# undercounted for a reason not fully run down; matching known error-line
# shapes directly is more legible anyway.)
actual_errors=$(( \
  $(grep -c "^Error from server (" <<< "${apply_output}" || true) \
  + $(grep -cE "no matches for kind|does not match the namespace|ensure CRDs are installed|cannot be handled as" <<< "${apply_output}" || true) \
))

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

if [ -s "${LARGE_CONFIGMAPS_FILE}" ]; then
  echo
  echo "Applying large ConfigMap(s) via --server-side (see split-large-configmaps.py's own comment)..."
  kubectl apply --server-side -n "${NAMESPACE}" -f "${LARGE_CONFIGMAPS_FILE}"
fi

echo
echo "Applying pabc-migrations (guarded - see scripts/lib/apply-pabc-migrations.sh)..."
"${CHART_DIR}/scripts/lib/apply-pabc-migrations.sh"

PRUNE_ARGS=("${NAMESPACE}")
if [ "${FORCE_PRUNE}" = true ]; then
  PRUNE_ARGS+=(--force)
fi

echo
echo "Pruning Deployments/StatefulSets/DaemonSets/Services/Secrets/Ingresses not part of this render (see prune-orphaned-workloads.py)..."
render | python3 "${CHART_DIR}/scripts/lib/prune-orphaned-workloads.py" "${PRUNE_ARGS[@]}"

# Checked against live cluster state, after pruning, not this invocation's
# own --set flags: the 'objecten' Deployment name is authoritative for
# "is the objecten profile actually part of what's deployed right now"
# either podiumd shape (classic or merged - see
# scripts/lib/detect-objecten-shape.sh), and correctly reflects a profile
# this call just turned off (pruned above) as absent, without this script
# needing its own separate copy of that enabled/disabled logic.
# seed-fixtures.sh's own seed() calls are each independently idempotent
# ("not done before" - see that script's own header), so calling it
# unconditionally here on every deploy.sh run is safe and fast once
# already seeded, not just on first deploy.
echo
if kubectl get deployment/objecten -n "${NAMESPACE}" > /dev/null 2>&1; then
  echo "Seeding fixture data (see scripts/lib/seed-fixtures.sh)..."
  "${CHART_DIR}/scripts/lib/seed-fixtures.sh"
else
  echo "'objecten' profile not deployed - skipping scripts/lib/seed-fixtures.sh."
fi

echo
echo "Done. Next: ./scripts/setup-tunnel.sh for external reachability, or run"
echo "the suite in tests/ to verify."
