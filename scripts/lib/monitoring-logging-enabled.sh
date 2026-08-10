#!/usr/bin/env bash
# Reads values.yaml's monitoringLogging.enabled the same way regardless of
# caller - sourced by show-podiumd-version.sh and deploy.sh so both stay in
# agreement about where this state lives, instead of each keeping its own
# copy of the same awk state machine to drift out of sync.
#
# Scoped to the monitoringLogging: block only (not a blind grep for
# "enabled:", which appears under plenty of other top-level keys in
# values.yaml too).
#
# Usage: source this file, then call monitoring_logging_enabled
# "<path-to-values.yaml>" - exits 0 (true) or 1 (false), so use directly in
# an `if`.
monitoring_logging_enabled() {
  local values_yaml="$1"
  awk '
    /^monitoringLogging:$/ { in_block = 1; next }
    in_block && /^[^[:space:]#]/ { in_block = 0 }
    in_block && /^[[:space:]]+enabled:[[:space:]]*true/ { found = 1 }
    END { exit !found }
  ' "${values_yaml}"
}
