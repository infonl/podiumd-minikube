#!/usr/bin/env bash
# Empties the podiumd-minikube namespace back to a clean slate, without
# tearing down the whole minikube cluster itself (see teardown-cluster.sh
# for that) - every pod, Deployment, Service, ConfigMap, Secret, PVC,
# Job/CronJob and namespaced custom resource (Prometheus, PrometheusRule,
# ServiceMonitor, ...) goes with the namespace. This is NOT reversible:
# PABC's seeded migration data, every app's Postgres/Solr data, and every
# app's own persisted media/uploads are gone - there is no confirmation
# step built into `kubectl delete namespace` itself, which is why this
# script adds one.
#
# Also cleans up what deleting the namespace alone would leave behind,
# since none of it is namespaced:
#   - Every PersistentVolume still bound to this namespace (checked via
#     spec.claimRef.namespace, not a hardcoded name list) - this covers two
#     different cases that both need it: the six pre-provisioned PVs
#     storage-hooks.yaml creates for podiumd's own Azure-CSI-backed apps
#     (openzaak, openklant, opennotificaties, openarchiefbeheer,
#     openformulieren, objecten), deliberately `Retain` policy (see that
#     file's own comment) so they never go away on their own; and,
#     confirmed live, every *dynamically*-provisioned PV too (postgres,
#     solr, grafana, loki, tempo, kube-prom-prometheus) despite their
#     `Delete` reclaim policy - minikube's own storage-provisioner didn't
#     actually reclaim any of them after the namespace's cascading PVC
#     deletion, leaving all seven stuck in `Released` forever instead
#     (~24Gi of requested capacity, though confirmed live the real on-disk
#     usage was much smaller - 116M). Left alone, both cases are silent
#     leftover cruft at best and a future deploy.sh conflict at worst.
#   - Their own hostPath data - storage-hooks.yaml's own PVs under
#     /data/podiumd-minikube, dynamically-provisioned ones under
#     /tmp/hostpath-provisioner/podiumd-minikube - on the minikube node
#     itself. Deleting the PV object alone doesn't touch either; a fresh PV
#     reusing the same path would just remount the old data. Confirmed live
#     that /data/podiumd-minikube's parent directory is root-owned 0755
#     (only each app's own subdirectory is 0777, from
#     storage-permissions-fix), so both need `sudo` inside the node -
#     confirmed live that's passwordless there.
#   - monitoring-logging's own cluster-scoped RBAC/webhook objects
#     (ClusterRole/ClusterRoleBinding/*WebhookConfiguration), via the
#     app.kubernetes.io/instance=podiumd-minikube label every one of them
#     carries (confirmed live) - only present at all if
#     monitoringLogging.enabled was ever turned on.
#
# Deliberately NOT touched: the CustomResourceDefinitions
# apply-monitoring-logging-crds.sh installs (Prometheus/ServiceMonitor/...)
# - they carry no instance label to select on (applied raw from the
# dependency's own tarball, never templated), and leaving them installed
# is harmless - no actual resources exist once the namespace is gone, and
# deploy.sh re-applies them idempotently via `kubectl apply --server-side`
# regardless.
#
# Usage:
#   ./scripts/reset-namespace.sh          # asks for confirmation first
#   ./scripts/reset-namespace.sh --yes    # skips the confirmation prompt
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="podiumd-minikube"
PROFILE="minikube"
SKIP_CONFIRM=false

if [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; then
  SKIP_CONFIRM=true
fi

source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"

echo "This will permanently empty the '${NAMESPACE}' namespace:"
echo "  - every pod, Deployment, Service, Ingress, ConfigMap, Secret, PVC,"
echo "    Job/CronJob, and namespaced custom resource in it"
echo "  - all seeded data: Postgres, Solr, PABC's migration data, every"
echo "    app's persisted media/uploads"
echo "  - every PersistentVolume still bound to this namespace, whether"
echo "    Retain-policy (storage-hooks.yaml's own) or Delete-policy ones"
echo "    minikube's own storage-provisioner failed to reclaim on its own"
echo "    (confirmed live - see this script's own header), plus their"
echo "    hostPath data on the minikube node"
echo "  - monitoring-logging's own cluster-scoped RBAC/webhook objects,"
echo "    if that dependency was ever enabled"
echo
echo "It does NOT delete the minikube cluster itself (see teardown-cluster.sh"
echo "for that), and does not touch the CRDs apply-monitoring-logging-crds.sh"
echo "installs - harmless to leave, and deploy.sh re-applies them idempotently."
echo

if [ "${SKIP_CONFIRM}" = false ]; then
  read -r -p "Type 'yes' to continue: " confirmation
  if [ "${confirmation}" != "yes" ]; then
    echo "Aborted - nothing was deleted."
    exit 1
  fi
fi

echo "Deleting namespace '${NAMESPACE}'..."
kubectl delete namespace "${NAMESPACE}" --ignore-not-found

echo "Waiting for the namespace to fully finish deleting (finalizers can make this slow)..."
kubectl wait --for=delete "namespace/${NAMESPACE}" --timeout=120s 2>/dev/null || true

echo "Deleting any PersistentVolume still bound to '${NAMESPACE}' (not namespaced, so the namespace delete above never touched them - see this script's own header for why a name-based check isn't enough)..."
STALE_PVS="$(kubectl get pv -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for pv in data['items']:
    claim_ref = pv['spec'].get('claimRef') or {}
    if claim_ref.get('namespace') == '${NAMESPACE}':
        print(pv['metadata']['name'])
")"
if [ -n "${STALE_PVS}" ]; then
  # shellcheck disable=SC2086
  kubectl delete pv ${STALE_PVS}
else
  echo "  (none found)"
fi

echo "Clearing hostPath data under /data/${NAMESPACE} and /tmp/hostpath-provisioner/${NAMESPACE} on the minikube node..."
minikube ssh -p "${PROFILE}" -- "sudo rm -rf /data/${NAMESPACE}/* /tmp/hostpath-provisioner/${NAMESPACE}" 2>/dev/null \
  || echo "  WARNING: could not reach the minikube node to clear hostPath data - do this by hand if it matters." >&2

echo "Deleting monitoring-logging's own cluster-scoped RBAC/webhook objects (if any)..."
kubectl delete clusterrole,clusterrolebinding,mutatingwebhookconfiguration,validatingwebhookconfiguration \
  -l "app.kubernetes.io/instance=${NAMESPACE}" --ignore-not-found

echo
echo "Done. '${NAMESPACE}' is gone - run ./scripts/deploy.sh to redeploy from scratch."
