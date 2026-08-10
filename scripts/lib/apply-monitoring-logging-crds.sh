#!/usr/bin/env bash
# Applies every CustomResourceDefinition bundled inside the monitoring-logging
# dependency's own tarball (Prometheus/PrometheusRule/ServiceMonitor/
# PodMonitor/... from its nested kube-prometheus-stack+crds subchart, plus a
# few more from its nested alloy/loki subcharts).
#
# Why this needs to exist: `helm template` never renders a chart's `crds/`
# directory - only `helm install`/`helm upgrade` install CRDs automatically,
# and this project never runs either (see deploy.sh's own header for why).
# Confirmed live: without this, every Prometheus/PrometheusRule/PodMonitor
# object in the render fails with "no matches for kind ... ensure CRDs are
# installed first" - and deploy.sh's own error-counting only recognized the
# unrelated, expected "spec is immutable" errors, so this whole class of
# failure went completely unnoticed the first several times.
#
# Extracts CRDs from charts/monitoring-logging-*.tgz - the dependency
# tarball `helm dependency update` already fetched, sitting on disk in this
# project's own charts/ directory already. Not a live cross-repo reference:
# purely local, offline, and only touches this project's own vendored copy.
# Filtered by content (`kind: CustomResourceDefinition`), not by a `crds/`
# path convention - the actual CRD files are scattered across several
# different nesting depths in this dependency tree, and matching on what
# they actually declare is simpler and more robust than guessing paths.
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

TARBALL="$(ls "${CHART_DIR}"/charts/monitoring-logging-*.tgz 2>/dev/null | head -1)"
if [ -z "${TARBALL}" ]; then
  echo "apply-monitoring-logging-crds.sh: no charts/monitoring-logging-*.tgz found - run 'helm dependency update' first (provision-cluster.sh does this)." >&2
  exit 1
fi

EXTRACT_DIR="$(mktemp -d)"
CRD_FILE="$(mktemp)"
trap 'rm -rf "${EXTRACT_DIR}" "${CRD_FILE}"' EXIT
tar -xzf "${TARBALL}" -C "${EXTRACT_DIR}"

find "${EXTRACT_DIR}" -name "*.yaml" -exec grep -l "^kind: CustomResourceDefinition$" {} + \
  | sort \
  | xargs cat > "${CRD_FILE}"

if [ ! -s "${CRD_FILE}" ]; then
  echo "apply-monitoring-logging-crds.sh: found no CustomResourceDefinition in ${TARBALL} - unexpected, check the dependency version." >&2
  exit 1
fi

# --server-side: some of these CRDs (kube-prometheus-stack's own especially)
# are large enough to hit the same client-side apply annotation limit as
# split-large-configmaps.py's own ConfigMaps - see that script's header.
# CRDs are cluster-scoped, so there's no -n/--namespace here on purpose.
kubectl apply --server-side -f "${CRD_FILE}"
