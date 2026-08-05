#!/usr/bin/env bash
# Swaps the podiumd Helm dependency to a different version (or a local
# chart checkout) and re-runs `helm dependency update` in one step. Helm
# dependency repository/version live in Chart.yaml and can't be
# templated/overridden via values.yaml or --set, so this is the "easily
# configurable" mechanism instead.
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
# Usage:
#   ./scripts/set-podiumd-version.sh <version>     # published version
#   ./scripts/set-podiumd-version.sh --path <dir>  # local chart checkout
# List available published versions: helm search repo dimpact/podiumd -l
#
# --path points the dependency straight at a local podiumd chart checkout
# (e.g. dimpact-samenwerking/helm-charts/charts/podiumd) via a file://
# repository instead of the @dimpact repo alias - useful for testing
# unreleased podiumd changes without publishing them first. Helm still
# resolves the version constraint against that checkout's own Chart.yaml,
# so this sets version to "*" to accept whatever it currently declares
# (confirmed live: helm errors out on any pinned constraint against a
# file:// dependency unless it happens to match exactly).
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${1:-}" == "--path" ]]; then
  LOCAL_PATH="${2:?Usage: set-podiumd-version.sh --path <dir>}"
  [[ -d "${LOCAL_PATH}" ]] || { echo "Not a directory: ${LOCAL_PATH}" >&2; exit 1; }
  [[ -f "${LOCAL_PATH}/Chart.yaml" ]] || { echo "No Chart.yaml found in: ${LOCAL_PATH}" >&2; exit 1; }
  ABS_PATH="$(cd "${LOCAL_PATH}" && pwd)"

  sed -i.bak \
    -e "s#repository: \".*\"#repository: \"file://${ABS_PATH}\"#" \
    -e "s/version: \"[^\"]*\"  *# podiumd version/version: \"*\"  # podiumd version/" \
    "${CHART_DIR}/Chart.yaml"
  rm -f "${CHART_DIR}/Chart.yaml.bak"

  helm dependency update "${CHART_DIR}"

  echo "podiumd dependency set to local path ${ABS_PATH}; helm dependency update re-run."
else
  NEW_VERSION="${1:?Usage: set-podiumd-version.sh <version>|--path <dir>}"

  sed -i.bak \
    -e "s#repository: \".*\"#repository: \"@dimpact\"#" \
    -e "s/version: \"[^\"]*\"  *# podiumd version/version: \"${NEW_VERSION}\"  # podiumd version/" \
    "${CHART_DIR}/Chart.yaml"
  rm -f "${CHART_DIR}/Chart.yaml.bak"

  helm dependency update "${CHART_DIR}"

  echo "podiumd dependency set to ${NEW_VERSION}; helm dependency update re-run."
fi
