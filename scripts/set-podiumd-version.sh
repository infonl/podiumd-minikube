#!/usr/bin/env bash
# Swaps the podiumd Helm dependency to a different version and re-runs
# `helm dependency update` in one step. Helm dependency versions live in
# Chart.yaml and can't be templated/overridden via values.yaml or --set, so
# this is the "easily configurable" mechanism instead.
#
# After swapping: re-check the four intentional image.tag version pins in
# values.yaml (podiumd.openzaak, .objecten, .opennotificaties,
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
# Usage: ./scripts/set-podiumd-version.sh <version>
# List available versions: helm search repo dimpact/podiumd -l
set -euo pipefail

NEW_VERSION="${1:?Usage: set-podiumd-version.sh <version>}"
CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sed -i.bak "s/version: \"[^\"]*\"  *# podiumd version/version: \"${NEW_VERSION}\"  # podiumd version/" "${CHART_DIR}/Chart.yaml"
rm -f "${CHART_DIR}/Chart.yaml.bak"

helm dependency update "${CHART_DIR}"

echo "podiumd dependency set to ${NEW_VERSION}; helm dependency update re-run."
