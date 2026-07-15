#!/usr/bin/env bash
# Swaps the podiumd Helm dependency to a different version and re-runs
# `helm dependency update` in one step. Helm dependency versions live in
# Chart.yaml and can't be templated/overridden via values.yaml or --set, so
# this is the "easily configurable" mechanism instead.
#
# Usage: ./scripts/set-podiumd-version.sh <version>
# List available versions: helm search repo dimpact/podiumd -l
set -euo pipefail

NEW_VERSION="${1:?Usage: set-podiumd-version.sh <version>}"
CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sed -i.bak "s/version: \"[^\"]*\"  *# podiumd version/version: \"${NEW_VERSION}\"  # podiumd version/" "${CHART_DIR}/Chart.yaml"
rm -f "${CHART_DIR}/Chart.yaml.bak"

helm dependency update "${CHART_DIR}"

echo "podiumd dependency set to ${NEW_VERSION}; helm dependency update re-run."
