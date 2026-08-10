#!/usr/bin/env bash
# Swaps the podiumd Helm dependency (and, alongside it, the optional
# monitoring-logging dependency - see values.yaml's own monitoringLogging
# comment) to a different version or a local chart checkout, and re-runs
# `helm dependency update` in one step. Helm dependency repository/version
# live in Chart.yaml and can't be templated/overridden via values.yaml or
# --set, so this is the "easily configurable" mechanism instead.
#
# After swapping podiumd: re-check the four intentional image.tag version
# pins in values.yaml (podiumd.openzaak, .objecten, .opennotificaties,
# .openformulieren - each one's own comment explains why it's pinned away
# from that chart's bundled default). Everything else is deliberately NOT
# pinned here - any other image reference tracks whatever the newly
# selected podiumd version's bundled charts default to, with
# scripts/strip-image-digests.py handling digest-qualified defaults as a
# post-renderer instead. A newly selected podiumd version could change
# what any of those four charts bundle by default - if it now already
# matches docker-compose.yaml's pinned version, the override is a
# redundant no-op; if it diverges in some new way, the override may need
# updating (or, if the underlying reason no longer applies, removing).
#
# Usage:
#   ./scripts/set-podiumd-version.sh <version> [monitoring-logging-version]
#   ./scripts/set-podiumd-version.sh <version> --disable-monitoring-logging
#   ./scripts/set-podiumd-version.sh --path <dir> [--disable-monitoring-logging]
# List available published versions:
#   helm search repo dimpact/podiumd -l
#   helm search repo dimpact/monitoring-logging -l
#
# --path points podiumd's dependency straight at a local chart checkout
# (e.g. dimpact-samenwerking/helm-charts/charts/podiumd) via a file://
# repository instead of the @dimpact repo alias - useful for testing
# unreleased podiumd changes without publishing them first. Helm still
# resolves the version constraint against that checkout's own Chart.yaml,
# so this sets version to "*" to accept whatever it currently declares
# (confirmed live: helm errors out on any pinned constraint against a
# file:// dependency unless it happens to match exactly).
#
# monitoring-logging follows podiumd's own --path automatically, pointed
# at the sibling "monitoring-logging" directory next to whatever podiumd
# directory was given (both charts live side by side in the same
# dimpact-samenwerking/helm-charts monorepo in every checkout seen so
# far) - per the user's own explicit instruction, not a guess.
#
# In plain <version> mode there is no such shortcut: podiumd and
# monitoring-logging are two independently-versioned charts in that same
# monorepo (podiumd's own 4.x series vs monitoring-logging's own 1.0.x
# series) - no formula relates a given podiumd version to "the"
# monitoring-logging version. The one thing that *is* true: since both
# charts live in the same monorepo, whatever monitoring-logging version
# was checked in at the exact commit podiumd's own version was tagged is
# a well-defined, look-up-able fact - confirmed live by checking out
# dimpact-samenwerking/helm-charts and running (no live cross-repo
# reference from this script itself - this project stays standalone;
# this is just how the correlation was found once, by hand):
#   git show podiumd-<version>:charts/monitoring-logging/Chart.yaml
# That's how "1.0.13" (this repo's current default, in Chart.yaml) was
# found for podiumd 4.8.1.
#
# If you don't pass the optional second argument (or pass
# --disable-monitoring-logging explicitly instead), monitoring-logging is
# disabled: values.yaml's monitoringLogging.enabled is set to false, and its
# Chart.yaml dependency entry (repository/version) is left completely
# untouched - it's still declared, so `helm dependency update` still fetches
# it below (per values.yaml's own condition:, Helm always downloads every
# declared dependency regardless of its condition value - only *rendering*
# is gated), it just won't be deployed/running. Passing a monitoring-logging
# version has the opposite effect: it's set in Chart.yaml AND enabled.
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_YAML="${CHART_DIR}/Chart.yaml"
VALUES_YAML="${CHART_DIR}/values.yaml"

# Scopes a repository:/version: replacement to the named dependency's own
# block only (awk state machine, not a blind file-wide sed) - two
# dependencies now share the same "@dimpact" repository string and this
# project's own comment style, so an unscoped regex would silently touch
# the wrong one. $3/$4 may be empty - empty means "leave as-is".
set_dependency() {
  local dep_name="$1" new_repo="$2" new_version="${3:-}"
  awk -v dep="${dep_name}" -v repo="${new_repo}" -v ver="${new_version}" '
    $0 ~ ("- name: " dep "$") { in_block = 1; print; next }
    /^  - name: / { in_block = 0; print; next }
    in_block && /repository:/ {
      sub(/repository: "[^"]*"/, "repository: \"" repo "\"")
      print; next
    }
    in_block && ver != "" && /version:[[:space:]]*"[^"]*"/ {
      sub(/version:[[:space:]]*"[^"]*"/, "version: \"" ver "\"")
      print; next
    }
    { print }
  ' "${CHART_YAML}" > "${CHART_YAML}.tmp"
  mv "${CHART_YAML}.tmp" "${CHART_YAML}"
}

# Scoped the same way as set_dependency above, but against values.yaml's
# top-level monitoringLogging: block instead of a Chart.yaml dependency
# block - flips only that block's own enabled: line.
set_monitoring_logging_enabled() {
  local val="$1"
  awk -v val="${val}" '
    /^monitoringLogging:$/ { in_block = 1; print; next }
    in_block && /^[^[:space:]#]/ { in_block = 0 }
    in_block && /^[[:space:]]+enabled:/ {
      sub(/enabled:[[:space:]]*(true|false)/, "enabled: " val)
      print; next
    }
    { print }
  ' "${VALUES_YAML}" > "${VALUES_YAML}.tmp"
  mv "${VALUES_YAML}.tmp" "${VALUES_YAML}"
}

if [[ "${1:-}" == "--path" ]]; then
  LOCAL_PATH="${2:?Usage: set-podiumd-version.sh --path <dir> [--disable-monitoring-logging]}"
  [[ -d "${LOCAL_PATH}" ]] || { echo "Not a directory: ${LOCAL_PATH}" >&2; exit 1; }
  [[ -f "${LOCAL_PATH}/Chart.yaml" ]] || { echo "No Chart.yaml found in: ${LOCAL_PATH}" >&2; exit 1; }
  ABS_PATH="$(cd "${LOCAL_PATH}" && pwd)"

  set_dependency podiumd "file://${ABS_PATH}" "*"

  if [[ "${3:-}" == "--disable-monitoring-logging" ]]; then
    set_monitoring_logging_enabled false
    echo "monitoring-logging disabled (--disable-monitoring-logging) - values.yaml's monitoringLogging.enabled set to false; its Chart.yaml dependency entry left untouched, so it's still fetched by helm dependency update below, just not rendered/deployed."
  else
    MONITORING_LOGGING_PATH="$(dirname "${ABS_PATH}")/monitoring-logging"
    if [[ -d "${MONITORING_LOGGING_PATH}" && -f "${MONITORING_LOGGING_PATH}/Chart.yaml" ]]; then
      set_dependency monitoring-logging "file://${MONITORING_LOGGING_PATH}" "*"
      set_monitoring_logging_enabled true
      echo "monitoring-logging dependency set to local path ${MONITORING_LOGGING_PATH} (sibling of podiumd's own --path); values.yaml's monitoringLogging.enabled set to true."
    else
      set_monitoring_logging_enabled false
      echo "WARNING: no sibling monitoring-logging/ directory found next to ${ABS_PATH} - left monitoring-logging's Chart.yaml dependency unchanged, but set values.yaml's monitoringLogging.enabled to false." >&2
    fi
  fi

  helm dependency update "${CHART_DIR}"

  echo "podiumd dependency set to local path ${ABS_PATH}; helm dependency update re-run."
else
  NEW_VERSION="${1:?Usage: set-podiumd-version.sh <version> [monitoring-logging-version]|<version> --disable-monitoring-logging|--path <dir>}"
  ARG2="${2:-}"

  set_dependency podiumd "@dimpact" "${NEW_VERSION}"

  if [[ -n "${ARG2}" && "${ARG2}" != "--disable-monitoring-logging" ]]; then
    set_dependency monitoring-logging "@dimpact" "${ARG2}"
    set_monitoring_logging_enabled true
  else
    set_monitoring_logging_enabled false
  fi

  helm dependency update "${CHART_DIR}"

  echo "podiumd dependency set to ${NEW_VERSION}; helm dependency update re-run."
  if [[ -n "${ARG2}" && "${ARG2}" != "--disable-monitoring-logging" ]]; then
    echo "monitoring-logging dependency set to ${ARG2}; values.yaml's monitoringLogging.enabled set to true."
  else
    echo "monitoring-logging disabled - values.yaml's monitoringLogging.enabled set to false. Its Chart.yaml dependency entry/version left untouched (still fetched by helm dependency update, just not rendered/deployed)."
  fi
fi
