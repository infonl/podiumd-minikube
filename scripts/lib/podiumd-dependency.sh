#!/usr/bin/env bash
# Shared library (sourced, not executed) for reading/writing this project's
# local, gitignored dependency-version file (.podiumd-versions.yaml) and
# syncing Chart.yaml/charts/*.tgz to match it - used by
# set-podiumd-version.sh (writes it, then syncs immediately) and
# deploy.sh/provision-cluster.sh (sync on every run, so it's always what
# actually gets deployed, not just whatever happened to be fetched the
# last time someone ran `helm dependency update` by hand - confirmed live
# this project had exactly that gap: a podiumd version bump committed to
# Chart.yaml/Chart.lock with nobody re-running `helm dependency update`
# after pulling it, silently deploying a stale, already-fetched older
# version instead).
#
# Chart.yaml no longer holds a real version for either dependency at all -
# just a placeholder (see its own comment there) - .podiumd-versions.yaml
# is the *only* source of truth, on purpose: every previous "shared
# default in Chart.yaml, personal override elsewhere" design considered
# here still let a developer's local choice leak into a git-tracked file
# the moment `helm dependency update` regenerated Chart.lock to match it.
# There is no default: if .podiumd-versions.yaml doesn't exist yet, or is
# missing a dependency's entry, every entry point here refuses to guess
# and tells the caller to run set-podiumd-version.sh instead.
#
# This is safe for rendering - confirmed live: `helm template`/`helm
# install` never re-validate a loaded subchart's version against the
# parent Chart.yaml's own declared dependency version constraint at all
# (only `helm dependency update`/`build` do, and only to decide what to
# *fetch* next). Proved directly: hand-edited Chart.yaml's podiumd version
# to a nonsense "9.9.9" with the real fetched dependency still at 4.8.3 in
# charts/, and `helm template` rendered clean using the physically-present
# 4.8.3 chart regardless. So temporarily pointing Chart.yaml at the real
# resolved value, running `helm dependency update`, then restoring
# Chart.yaml's placeholder content is completely safe: the already-fetched
# charts/*.tgz produced by that temporary edit stays exactly as fetched,
# and Chart.yaml's on-disk (therefore git-tracked) content afterward is
# unchanged either way. Chart.lock gets the same treatment, for the
# identical reason - it's also git-tracked, and would otherwise leak the
# real resolved version into a committed file the moment `helm dependency
# update` regenerates it.
#
# .podiumd-versions.yaml shape (gitignored; create/update it with
# set-podiumd-version.sh, never by hand-editing Chart.yaml):
#   podiumd:
#     version: "4.8.1"       # registry mode
#   # or:
#   podiumd:
#     path: "/abs/dir"        # --path mode
#   monitoringLogging:        # always required too, even when disabled -
#     version: "1.0.13"       # Helm fetches every declared dependency
#   # or:                     # regardless of its condition: value (see
#   monitoringLogging:        # values.yaml's own monitoringLogging
#     path: "/abs/dir"        # comment), so a real, fetchable version is
#                              # needed either way

PODIUMD_LIB_CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PODIUMD_VERSIONS_FILE="${PODIUMD_LIB_CHART_DIR}/.podiumd-versions.yaml"
PODIUMD_CHART_YAML="${PODIUMD_LIB_CHART_DIR}/Chart.yaml"
PODIUMD_CHART_LOCK="${PODIUMD_LIB_CHART_DIR}/Chart.lock"

# Scopes a repository:/version: replacement to the named dependency's own
# Chart.yaml block only - two dependencies share the "@dimpact" repository
# string, so an unscoped edit would risk touching the wrong one.
_podiumd_set_chart_yaml_dependency() {
  local dep_name="$1" new_repo="$2" new_version="$3"
  awk -v dep="${dep_name}" -v repo="${new_repo}" -v ver="${new_version}" '
    $0 ~ ("- name: " dep "$") { in_block = 1; print; next }
    /^  - name: / { in_block = 0; print; next }
    in_block && /repository:/ {
      sub(/repository: "[^"]*"/, "repository: \"" repo "\"")
      print; next
    }
    in_block && /version:[[:space:]]*"[^"]*"/ {
      sub(/version:[[:space:]]*"[^"]*"/, "version: \"" ver "\"")
      print; next
    }
    { print }
  ' "${PODIUMD_CHART_YAML}" > "${PODIUMD_CHART_YAML}.tmp"
  mv "${PODIUMD_CHART_YAML}.tmp" "${PODIUMD_CHART_YAML}"
}

# Writes/updates one dependency's entry in .podiumd-versions.yaml, in
# "version" or "path" mode, preserving the other dependency's existing
# entry untouched. python3+PyYAML rather than hand-rolled awk/sed - this
# file may get hand-edited by a developer, and a real YAML parser handles
# whatever formatting they use instead of requiring it to match a specific
# script-generated layout.
podiumd_write_version_entry() {
  local dep_key="$1" mode="$2" value="$3"
  python3 - "${PODIUMD_VERSIONS_FILE}" "${dep_key}" "${mode}" "${value}" <<'EOF'
import sys
import yaml

path, dep_key, mode, value = sys.argv[1:5]
try:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
except FileNotFoundError:
    data = {}

data[dep_key] = {mode: value}

with open(path, "w") as f:
    yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
EOF
}

# Reads one field ("version" or "path") for one dependency
# ("podiumd"/"monitoringLogging") from .podiumd-versions.yaml. Empty
# output means "not set" - covers both "file doesn't exist yet" and "file
# exists but doesn't mention this dependency".
podiumd_read_version_entry() {
  local dep_key="$1" field="$2"
  [ -f "${PODIUMD_VERSIONS_FILE}" ] || return 0
  python3 - "${PODIUMD_VERSIONS_FILE}" "${dep_key}" "${field}" <<'EOF'
import sys
import yaml

path, dep_key, field = sys.argv[1:4]
with open(path) as f:
    data = yaml.safe_load(f) or {}
print(((data.get(dep_key) or {}).get(field)) or "", end="")
EOF
}

# Resolves one dependency's (mode, value) pair from .podiumd-versions.yaml
# into PODIUMD_RESOLVED_MODE/PODIUMD_RESOLVED_VALUE globals (not
# echo+capture - a path value can contain spaces, which word-splitting a
# captured string would corrupt). Returns 1, leaving those globals
# untouched, when no entry exists for this dependency - callers check the
# return code and report every missing dependency together (see
# podiumd_require_versions_entries below), rather than dying on whichever
# one happens to be checked first.
podiumd_resolve_dependency() {
  local dep_key="$1"
  local override_path override_version
  override_path="$(podiumd_read_version_entry "${dep_key}" path)"
  override_version="$(podiumd_read_version_entry "${dep_key}" version)"
  if [ -n "${override_path}" ]; then
    PODIUMD_RESOLVED_MODE="path"
    PODIUMD_RESOLVED_VALUE="${override_path}"
  elif [ -n "${override_version}" ]; then
    PODIUMD_RESOLVED_MODE="version"
    PODIUMD_RESOLVED_VALUE="${override_version}"
  else
    return 1
  fi
}

# Prints the standard "run set-podiumd-version.sh" guidance for one or
# more dependency keys with no usable entry, then exits 1. Shared by
# sync_podiumd_dependencies and show-podiumd-version.sh so the message
# stays identical in both places.
podiumd_require_versions_entries_error() {
  echo "No usable .podiumd-versions.yaml entry found for: $*" >&2
  echo "Run ./scripts/set-podiumd-version.sh <version> <monitoring-logging-version>" >&2
  echo "(or <version> --disable-monitoring-logging, or --path <dir> [--disable-monitoring-logging])" >&2
  echo "to create/update it - see that script's own header for the full usage." >&2
  exit 1
}

# Main entry point - call this before every render (deploy.sh,
# provision-cluster.sh) and after every .podiumd-versions.yaml write
# (set-podiumd-version.sh), so charts/*.tgz always matches whatever's
# currently configured there, regardless of when/whether anyone last ran
# this by hand. Hard-errors (via podiumd_require_versions_entries_error)
# if either dependency has no usable entry at all - there is no fallback
# to Chart.yaml, on purpose (see this file's own header).
#
# Resolves BOTH dependencies and, if either needs fetching, edits BOTH of
# Chart.yaml's dependency blocks together before a single `helm dependency
# update` call, not one dependency at a time - `helm dependency update`
# always re-resolves every declared dependency in one pass, so editing
# only one block at a time would make each call's *other* dependency
# briefly read back at whatever Chart.yaml's already-reverted placeholder
# says, failing that fetch (or worse, matching some unrelated real
# release) and clobbering a previous call's correct fetch for it. One
# combined edit -> one `helm dependency update` -> one revert avoids that
# entirely.
#
# Skips the whole cycle (and its network round-trip) when neither
# dependency actually needs fetching - in version mode, when its own
# target tarball is already present in charts/; path mode always needs a
# fresh `helm dependency update` (it re-packages the local checkout's
# current content every time this runs, not a fixed version string to
# check a filename against).
sync_podiumd_dependencies() {
  local missing=()
  local podiumd_mode podiumd_value monlog_mode monlog_value

  if podiumd_resolve_dependency podiumd; then
    podiumd_mode="${PODIUMD_RESOLVED_MODE}"
    podiumd_value="${PODIUMD_RESOLVED_VALUE}"
  else
    missing+=(podiumd)
  fi
  if podiumd_resolve_dependency monitoringLogging; then
    monlog_mode="${PODIUMD_RESOLVED_MODE}"
    monlog_value="${PODIUMD_RESOLVED_VALUE}"
  else
    missing+=(monitoring-logging)
  fi
  if [ "${#missing[@]}" -gt 0 ]; then
    podiumd_require_versions_entries_error "${missing[*]}"
  fi

  local podiumd_needs_fetch=true monlog_needs_fetch=true
  if [ "${podiumd_mode}" = "version" ] && ls "${PODIUMD_LIB_CHART_DIR}/charts/podiumd-${podiumd_value}.tgz" > /dev/null 2>&1; then
    podiumd_needs_fetch=false
  fi
  if [ "${monlog_mode}" = "version" ] && ls "${PODIUMD_LIB_CHART_DIR}/charts/monitoring-logging-${monlog_value}.tgz" > /dev/null 2>&1; then
    monlog_needs_fetch=false
  fi
  if [ "${podiumd_needs_fetch}" = false ] && [ "${monlog_needs_fetch}" = false ]; then
    return 0
  fi

  local chart_yaml_backup chart_lock_backup=""
  chart_yaml_backup="$(mktemp)"
  cp "${PODIUMD_CHART_YAML}" "${chart_yaml_backup}"
  if [ -f "${PODIUMD_CHART_LOCK}" ]; then
    chart_lock_backup="$(mktemp)"
    cp "${PODIUMD_CHART_LOCK}" "${chart_lock_backup}"
  fi

  if [ "${podiumd_mode}" = "path" ]; then
    local podiumd_abs_path
    podiumd_abs_path="$(cd "${podiumd_value}" && pwd)"
    _podiumd_set_chart_yaml_dependency podiumd "file://${podiumd_abs_path}" "*"
  else
    _podiumd_set_chart_yaml_dependency podiumd "@dimpact" "${podiumd_value}"
  fi
  if [ "${monlog_mode}" = "path" ]; then
    local monlog_abs_path
    monlog_abs_path="$(cd "${monlog_value}" && pwd)"
    _podiumd_set_chart_yaml_dependency monitoring-logging "file://${monlog_abs_path}" "*"
  else
    _podiumd_set_chart_yaml_dependency monitoring-logging "@dimpact" "${monlog_value}"
  fi

  local fetch_status=0
  helm dependency update "${PODIUMD_LIB_CHART_DIR}" || fetch_status=$?

  # Restore Chart.yaml/Chart.lock's own on-disk content regardless of
  # whether the fetch above succeeded - explicit restore-then-propagate
  # rather than a RETURN/EXIT trap: traps are script-global, not scoped
  # to "this one call", and this function's two dependency edits share a
  # single combined edit/restore pass rather than one trap per call. A
  # hard kill signal mid-`helm dependency update` is the one gap this
  # doesn't cover - an accepted, small, precedented risk level for this
  # codebase (see e.g. mv-based "atomic" replacements elsewhere here,
  # which have the same class of gap).
  cp "${chart_yaml_backup}" "${PODIUMD_CHART_YAML}"
  rm -f "${chart_yaml_backup}"
  if [ -n "${chart_lock_backup}" ]; then
    cp "${chart_lock_backup}" "${PODIUMD_CHART_LOCK}"
    rm -f "${chart_lock_backup}"
  fi

  return "${fetch_status}"
}
