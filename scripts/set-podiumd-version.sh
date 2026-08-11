#!/usr/bin/env bash
# Sets the podiumd Helm dependency (and, alongside it, the optional
# monitoring-logging dependency - see values.yaml's own monitoringLogging
# comment) to a different version or a local chart checkout, then
# immediately syncs charts/*.tgz to match it.
#
# The selection lives in .podiumd-versions.yaml (gitignored, created on
# first use here) - NOT in Chart.yaml, which no longer holds a real
# version for either dependency at all (just a placeholder - see its own
# comment there). This is the *only* place either dependency's version is
# recorded: deploy.sh/provision-cluster.sh refuse to proceed with a clear
# message if you haven't run this at least once. Found live that editing
# Chart.yaml directly (this script's old behavior) meant every version
# choice showed up as an uncommitted - or worse, accidentally committed -
# shared-file diff, purely because of a purely-local, single-developer
# choice. See scripts/lib/podiumd-dependency.sh's own header for the full
# mechanism (why leaving Chart.yaml/Chart.lock's own on-disk content
# untouched here is safe: `helm template`/`install` never re-validate a
# loaded subchart's version against Chart.yaml's declared constraint at
# all, confirmed live).
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
#   ./scripts/set-podiumd-version.sh <version> <monitoring-logging-version>
#   ./scripts/set-podiumd-version.sh <version> --disable-monitoring-logging
#   ./scripts/set-podiumd-version.sh --path <dir> [--disable-monitoring-logging]
# List available published versions:
#   helm search repo dimpact/podiumd -l
#   helm search repo dimpact/monitoring-logging -l
#
# --path points podiumd's dependency straight at a local chart checkout
# (e.g. dimpact-samenwerking/helm-charts/charts/podiumd) instead of the
# @dimpact repo alias - useful for testing unreleased podiumd changes
# without publishing them first. Every deploy.sh/provision-cluster.sh run
# re-packages that checkout's *current* content (via
# scripts/lib/podiumd-dependency.sh's sync_podiumd_dependencies, called
# automatically), so local edits to it show up on the next deploy without
# re-running this script.
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
#
# In plain <version> mode the second argument is mandatory - there is no
# implicit default, on purpose: omitting it used to silently disable
# monitoring-logging, which was easy to do by accident. You must now say
# which you mean:
#   - --disable-monitoring-logging: values.yaml's monitoringLogging.enabled
#     is set to false. .podiumd-versions.yaml's own monitoringLogging
#     entry (if any) is left untouched - Helm still fetches it via
#     sync_podiumd_dependencies below regardless (per values.yaml's own
#     condition:, Helm always downloads every declared dependency
#     regardless of its condition value - only *rendering* is gated), it
#     just won't be deployed/running. If NO monitoring-logging entry has
#     ever been recorded yet (there's nothing to "leave untouched" and
#     Chart.yaml no longer has a fallback to fall back to either), this
#     refuses and tells you to provide a real version at least once
#     first - see the error message itself for the exact command.
#   - a monitoring-logging version: sets it in .podiumd-versions.yaml AND
#     enables it - there is no way to set a version without also
#     enabling it.
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALUES_YAML="${CHART_DIR}/values.yaml"

source "${CHART_DIR}/scripts/lib/podiumd-dependency.sh"

# Scoped the same way as podiumd-dependency.sh's own Chart.yaml editor,
# but against values.yaml's top-level monitoringLogging: block instead of
# a Chart.yaml dependency block - flips only that block's own enabled:
# line. Kept local to this script - nothing else needs to write this
# flag (show-podiumd-version.sh only ever reads it, via
# scripts/lib/monitoring-logging-enabled.sh).
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

# Helm still needs a real, fetchable monitoring-logging version even when
# disabled (see this script's own header) - refuses here, with a clear
# fix, rather than letting sync_podiumd_dependencies fail later with a
# more generic message once nothing at all has ever been recorded for it.
require_prior_monitoring_logging_entry_or_die() {
  local example_podiumd_version="$1"
  if [ -z "$(podiumd_read_version_entry monitoringLogging path)" ] \
    && [ -z "$(podiumd_read_version_entry monitoringLogging version)" ]; then
    echo "ERROR: no monitoring-logging version has ever been recorded in .podiumd-versions.yaml yet." >&2
    echo "--disable-monitoring-logging doesn't set one, and there's no fallback to fall back to -" >&2
    echo "Helm still needs a real version to fetch it, even when it won't be rendered (see" >&2
    echo "values.yaml's own monitoringLogging comment)." >&2
    echo >&2
    echo "Run this once with a real monitoring-logging version first, e.g.:" >&2
    echo "  ./scripts/set-podiumd-version.sh ${example_podiumd_version} <monitoring-logging-version>" >&2
    echo "then re-run with --disable-monitoring-logging afterward if you still want it disabled." >&2
    exit 1
  fi
}

if [[ "${1:-}" == "--path" ]]; then
  LOCAL_PATH="${2:?Usage: set-podiumd-version.sh --path <dir> [--disable-monitoring-logging]}"
  [[ -d "${LOCAL_PATH}" ]] || { echo "Not a directory: ${LOCAL_PATH}" >&2; exit 1; }
  [[ -f "${LOCAL_PATH}/Chart.yaml" ]] || { echo "No Chart.yaml found in: ${LOCAL_PATH}" >&2; exit 1; }
  ABS_PATH="$(cd "${LOCAL_PATH}" && pwd)"

  if [[ "${3:-}" == "--disable-monitoring-logging" ]]; then
    require_prior_monitoring_logging_entry_or_die "--path ${ABS_PATH}"
  fi

  podiumd_write_version_entry podiumd path "${ABS_PATH}"

  if [[ "${3:-}" == "--disable-monitoring-logging" ]]; then
    set_monitoring_logging_enabled false
    echo "monitoring-logging disabled (--disable-monitoring-logging) - values.yaml's monitoringLogging.enabled set to false; .podiumd-versions.yaml's own monitoringLogging entry left untouched, so it's still fetched below, just not rendered/deployed."
  else
    MONITORING_LOGGING_PATH="$(dirname "${ABS_PATH}")/monitoring-logging"
    if [[ -d "${MONITORING_LOGGING_PATH}" && -f "${MONITORING_LOGGING_PATH}/Chart.yaml" ]]; then
      podiumd_write_version_entry monitoringLogging path "${MONITORING_LOGGING_PATH}"
      set_monitoring_logging_enabled true
      echo "monitoring-logging dependency set to local path ${MONITORING_LOGGING_PATH} (sibling of podiumd's own --path); values.yaml's monitoringLogging.enabled set to true."
    else
      echo "WARNING: no sibling monitoring-logging/ directory found next to ${ABS_PATH}." >&2
      require_prior_monitoring_logging_entry_or_die "--path ${ABS_PATH}"
      set_monitoring_logging_enabled false
      echo "WARNING: left .podiumd-versions.yaml's existing monitoringLogging entry unchanged, but set values.yaml's monitoringLogging.enabled to false." >&2
    fi
  fi

  sync_podiumd_dependencies

  echo "podiumd dependency set to local path ${ABS_PATH} (.podiumd-versions.yaml); charts/*.tgz synced."
else
  NEW_VERSION="${1:?Usage: set-podiumd-version.sh <version> <monitoring-logging-version>|<version> --disable-monitoring-logging|--path <dir> [--disable-monitoring-logging]}"
  ARG2="${2:?Usage: set-podiumd-version.sh <version> <monitoring-logging-version>|<version> --disable-monitoring-logging|--path <dir> [--disable-monitoring-logging] - the second argument is mandatory here: pass a monitoring-logging version (enables it) or --disable-monitoring-logging explicitly}"

  if [[ "${ARG2}" == "--disable-monitoring-logging" ]]; then
    require_prior_monitoring_logging_entry_or_die "${NEW_VERSION}"
  fi

  podiumd_write_version_entry podiumd version "${NEW_VERSION}"

  if [[ "${ARG2}" == "--disable-monitoring-logging" ]]; then
    set_monitoring_logging_enabled false
  else
    podiumd_write_version_entry monitoringLogging version "${ARG2}"
    set_monitoring_logging_enabled true
  fi

  sync_podiumd_dependencies

  echo "podiumd dependency set to ${NEW_VERSION} (.podiumd-versions.yaml); charts/*.tgz synced."
  if [[ "${ARG2}" == "--disable-monitoring-logging" ]]; then
    echo "monitoring-logging disabled - values.yaml's monitoringLogging.enabled set to false. .podiumd-versions.yaml's own monitoringLogging entry left untouched (still fetched, just not rendered/deployed)."
  else
    echo "monitoring-logging dependency set to ${ARG2}; values.yaml's monitoringLogging.enabled set to true."
  fi
fi
