#!/usr/bin/env bash
# Prints everything scripts/set-podiumd-version.sh can currently have set,
# read back from the three places that information actually lives in - no
# single one of them tells the whole story on its own:
#   - Chart.yaml: the podiumd/monitoring-logging dependency declarations
#     (repository + version constraint) - the source of truth for what
#     `helm dependency update` will fetch next
#   - Chart.lock: the version each dependency actually *resolved* to as of
#     the last `helm dependency update` run - only interesting on its own
#     when Chart.yaml's own version is "*" (i.e. --path mode), since a "*"
#     constraint can resolve to a different concrete version every time the
#     local checkout it points at changes. Stale the moment that checkout's
#     own Chart.yaml bumps without a fresh `helm dependency update` after -
#     this script has no way to detect that staleness, only report the
#     timestamp Chart.lock was last generated at.
#   - values.yaml: monitoringLogging.enabled - NOT in Chart.yaml/Chart.lock
#     at all, and unrelated to monitoring-logging's version field. Helm
#     fetches every declared dependency regardless of its condition: value
#     (see set-podiumd-version.sh's own header), so monitoring-logging's
#     version/repository are populated in Chart.yaml/Chart.lock even when
#     it's disabled - this flag is the only thing that says whether it's
#     actually rendered.
#
# Usage:
#   ./scripts/show-podiumd-version.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_YAML="${CHART_DIR}/Chart.yaml"
CHART_LOCK="${CHART_DIR}/Chart.lock"
VALUES_YAML="${CHART_DIR}/values.yaml"

source "${CHART_DIR}/scripts/lib/monitoring-logging-enabled.sh"

strip_value() {
  sed -E 's/^[^:]*:[[:space:]]*//; s/[[:space:]]*#.*$//; s/^"//; s/"$//'
}

# Scoped to the named dependency's own block only, same awk state-machine
# set-podiumd-version.sh's own set_dependency() uses - two dependencies
# share the "@dimpact" repository string, so an unscoped grep could read the
# wrong one's field.
chart_yaml_field() {
  local dep_name="$1" field="$2"
  awk -v dep="${dep_name}" -v field="${field}" '
    $0 ~ ("- name: " dep "$") { in_block = 1; next }
    /^  - name: / { in_block = 0; next }
    in_block && $0 ~ (field ":") { print; exit }
  ' "${CHART_YAML}" | strip_value
}

chart_lock_field() {
  local dep_name="$1" field="$2"
  [ -f "${CHART_LOCK}" ] || return 0
  awk -v dep="${dep_name}" -v field="${field}" '
    $0 ~ ("- name: " dep "$") { in_block = 1; next }
    /^- name: / { in_block = 0; next }
    in_block && $0 ~ ("^  " field ":") { print; exit }
  ' "${CHART_LOCK}" | strip_value
}

show_dependency() {
  local dep_name="$1"
  local repo version
  repo="$(chart_yaml_field "${dep_name}" repository)"
  version="$(chart_yaml_field "${dep_name}" version)"
  echo "${dep_name}:"
  echo "  Chart.yaml:  repository=${repo}  version=${version}"

  if [[ "${repo}" == file://* ]]; then
    local local_dir="${repo#file://}"
    echo "  --path mode: version \"*\" only means \"whatever this checkout currently declares\" -"
    echo "  Chart.yaml itself never holds a real version number while this is active."
    if [[ -f "${local_dir}/Chart.yaml" ]]; then
      local local_version
      local_version="$(awk -F': *' '/^version:/ { print $2; exit }' "${local_dir}/Chart.yaml")"
      echo "  Local checkout (${local_dir}) currently declares: version=${local_version}"
    else
      echo "  WARNING: no Chart.yaml found at ${local_dir} - checkout may have moved or been removed." >&2
    fi
  fi

  if [ -f "${CHART_LOCK}" ]; then
    local lock_repo lock_version generated
    lock_repo="$(chart_lock_field "${dep_name}" repository)"
    lock_version="$(chart_lock_field "${dep_name}" version)"
    generated="$(awk '/^generated:/ { sub(/^generated:[[:space:]]*/, ""); gsub(/"/, ""); print; exit }' "${CHART_LOCK}")"
    echo "  Chart.lock:  repository=${lock_repo}  version=${lock_version}  (resolved as of last 'helm dependency update', generated ${generated})"
  else
    echo "  Chart.lock not found - run 'helm dependency update' to resolve/fetch the pinned version."
  fi
}

show_dependency podiumd
echo
show_dependency monitoring-logging

echo
if monitoring_logging_enabled "${VALUES_YAML}"; then
  echo "monitoringLogging.enabled: true -> ENABLED, backing the metrics profile (values.yaml)"
else
  echo "monitoringLogging.enabled: false -> DISABLED - not rendered, even though its chart is"
  echo "still declared/fetched above (values.yaml)"
fi
