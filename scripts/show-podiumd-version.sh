#!/usr/bin/env bash
# Prints what scripts/set-podiumd-version.sh currently has set, per
# dependency:
#   - .podiumd-versions.yaml: the ONLY source of truth (gitignored) -
#     Chart.yaml no longer holds a real version for either dependency at
#     all (just a placeholder - see its own comment there), so there's no
#     second place to reconcile against.
#   - charts/*.tgz: whether the resolved dependency's tarball has
#     actually been fetched yet - the one thing that determines what a
#     render right now would actually use, distinct from what's merely
#     *configured* above.
#   - values.yaml: monitoringLogging.enabled - unrelated to
#     monitoring-logging's own version/path; Helm fetches every declared
#     dependency regardless of its condition: value (see
#     set-podiumd-version.sh's own header), so this flag is the only
#     thing that says whether it's actually rendered once fetched.
#
# Usage:
#   ./scripts/show-podiumd-version.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALUES_YAML="${CHART_DIR}/values.yaml"

source "${CHART_DIR}/scripts/lib/monitoring-logging-enabled.sh"
source "${CHART_DIR}/scripts/lib/podiumd-dependency.sh"

# Unlike sync_podiumd_dependencies (which hard-stops on the first missing
# dependency via podiumd_require_versions_entries_error), this shows
# whatever IS configured for each dependency independently, so a partial
# .podiumd-versions.yaml (e.g. podiumd set, monitoring-logging never
# touched) still reports something useful for the one that IS set -
# accumulates missing ones and errors only once, at the very end.
MISSING=()

show_dependency() {
  local dep_key="$1" chart_yaml_dep_name="$2"
  echo "${chart_yaml_dep_name}:"

  if ! podiumd_resolve_dependency "${dep_key}"; then
    echo "  Not configured - run ./scripts/set-podiumd-version.sh to set it (see its own usage)."
    MISSING+=("${chart_yaml_dep_name}")
    return 0
  fi

  if [ "${PODIUMD_RESOLVED_MODE}" = "path" ]; then
    echo "  Local checkout: ${PODIUMD_RESOLVED_VALUE}"
    if [[ -f "${PODIUMD_RESOLVED_VALUE}/Chart.yaml" ]]; then
      local local_version
      local_version="$(awk -F': *' '/^version:/ { print $2; exit }' "${PODIUMD_RESOLVED_VALUE}/Chart.yaml")"
      echo "    (that checkout's own Chart.yaml currently declares version=${local_version} - re-packaged fresh on every deploy.sh/provision-cluster.sh run, so this can drift from what's in charts/ below until the next one)"
    else
      echo "    WARNING: no Chart.yaml found there - checkout may have moved or been removed." >&2
    fi
  else
    echo "  Version: ${PODIUMD_RESOLVED_VALUE}"
  fi

  if [ "${PODIUMD_RESOLVED_MODE}" = "path" ]; then
    echo "  Path mode always re-packages the checkout's current content on the next deploy.sh/provision-cluster.sh run, regardless of what's already in charts/ - see sync_podiumd_dependencies's own header for why."
  else
    local tgz="${CHART_DIR}/charts/${chart_yaml_dep_name}-${PODIUMD_RESOLVED_VALUE}.tgz"
    if [ -f "${tgz}" ]; then
      echo "  charts/${chart_yaml_dep_name}-${PODIUMD_RESOLVED_VALUE}.tgz already fetched - next deploy.sh/provision-cluster.sh run needs no network round-trip for this dependency."
    else
      echo "  Not yet fetched into charts/ - the next deploy.sh/provision-cluster.sh run will fetch it (needs network access)."
    fi
  fi
}

show_dependency podiumd podiumd
echo
show_dependency monitoringLogging monitoring-logging

echo
if monitoring_logging_enabled "${VALUES_YAML}"; then
  echo "monitoringLogging.enabled: true -> ENABLED, backing the metrics profile (values.yaml)"
else
  echo "monitoringLogging.enabled: false -> DISABLED - not rendered, even if its chart is"
  echo "configured/fetched above (values.yaml)"
fi

if [ "${#MISSING[@]}" -gt 0 ]; then
  echo
  podiumd_require_versions_entries_error "${MISSING[*]}"
fi
