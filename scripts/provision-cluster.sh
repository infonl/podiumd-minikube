#!/usr/bin/env bash
# Provisions a fresh minikube cluster ready to receive this chart:
#   1. starts minikube with enough CPU/memory for the full stack
#      (all step 5 profiles enabled, not just the core ones)
#   2. installs Traefik, pinned to a version compatible with older `helm`
#      binaries (see the version note below) - a cluster prerequisite this
#      chart deliberately doesn't manage itself
#   3. runs `helm dependency update` so the podiumd chart tarball is present
#   4. pre-pulls and loads every image this chart can reference into
#      minikube - its inner Docker has no internet access at all, so any
#      image not already loaded fails to pull once a pod actually needs it
#
# After this finishes: scripts/deploy.sh to render + apply the chart, then
# scripts/setup-tunnel.sh for external reachability.
#
# Usage:
#   ./scripts/provision-cluster.sh
#   MINIKUBE_CPUS=8 MINIKUBE_MEMORY=24576 ./scripts/provision-cluster.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="minikube"

# Empirically, all core + step 5 profiles together run ~35 pods under real
# load (load average 4-6 observed live) - sized above the chart's documented
# 4 CPU/8Gi core-only minimum accordingly. Override via env vars if your
# machine has less to spare, or you only ever run the core profile.
MINIKUBE_CPUS="${MINIKUBE_CPUS:-6}"
MINIKUBE_MEMORY="${MINIKUBE_MEMORY:-16384}"

TRAEFIK_NAMESPACE="traefik"
# Pinned, not latest: newer Traefik chart versions use Go 1.18+ template
# features (`break`) that older `helm` binaries (e.g. v3.9.0) fail to parse
# at all. Bump this only after confirming your local `helm version` is new
# enough, or upgrade helm itself first.
TRAEFIK_CHART_VERSION="34.4.0"

# --- 1. minikube ---
if minikube status -p "${PROFILE}" > /dev/null 2>&1; then
  echo "minikube profile '${PROFILE}' is already running - leaving it as-is."
  echo "(delete it first with scripts/teardown-cluster.sh if you want a genuinely fresh start)"

  # Found live: an already-running profile skips MINIKUBE_MEMORY entirely
  # (it only applies to `minikube start`), so a profile created before this
  # script existed - or resized down some other way - stays silently
  # under-provisioned. On the docker driver this container hitting its own
  # memory limit under the full stack's real load (~35 pods) caused severe
  # CPU/memory thrashing that made the whole cluster's API server
  # unresponsive (see plan.md's step 5 notes) - not an obvious crash, so
  # worth checking every run rather than only at profile-creation time.
  # Only meaningful for the docker driver - other minikube drivers are
  # VM-based, not a plain container this can inspect the same way.
  if command -v docker > /dev/null 2>&1 \
    && current_bytes="$(docker inspect "${PROFILE}" --format '{{.HostConfig.Memory}}' 2>/dev/null)" \
    && [ -n "${current_bytes}" ] && [ "${current_bytes}" -gt 0 ]; then
    requested_bytes=$(( MINIKUBE_MEMORY * 1024 * 1024 ))
    if [ "${current_bytes}" -lt "${requested_bytes}" ]; then
      current_gib=$(( current_bytes / 1024 / 1024 / 1024 ))
      requested_gib=$(( MINIKUBE_MEMORY / 1024 ))
      echo >&2
      echo "WARNING: profile '${PROFILE}' is only allocated ${current_gib}GiB, below the" >&2
      echo "${requested_gib}GiB this script requests for a fresh profile. Raise it now, live," >&2
      echo "without restarting the container or any pod inside it:" >&2
      echo "  docker update --memory=${requested_gib}g --memory-swap=-1 ${PROFILE}" >&2
      echo "This doesn't persist across a future 'minikube delete' - that recreates the" >&2
      echo "container fresh from MINIKUBE_MEMORY, so it's a one-time fix for this profile." >&2
    fi
  fi
else
  echo "Starting minikube (cpus=${MINIKUBE_CPUS}, memory=${MINIKUBE_MEMORY}MB)..."
  minikube start -p "${PROFILE}" --cpus="${MINIKUBE_CPUS}" --memory="${MINIKUBE_MEMORY}"
fi

# --- 2. Traefik ---
if kubectl get deployment traefik -n "${TRAEFIK_NAMESPACE}" > /dev/null 2>&1; then
  echo "Traefik already installed in namespace '${TRAEFIK_NAMESPACE}' - skipping."
else
  echo "Installing Traefik ${TRAEFIK_CHART_VERSION}..."
  helm repo add traefik https://traefik.github.io/charts > /dev/null 2>&1 || true
  helm repo update traefik > /dev/null
  helm upgrade --install traefik traefik/traefik \
    --version "${TRAEFIK_CHART_VERSION}" \
    -n "${TRAEFIK_NAMESPACE}" --create-namespace
fi

# --- 3. helm dependency ---
# Must run before deriving the image list below - that render needs the
# podiumd chart tarball to already be present.
echo "Running helm dependency update..."
helm dependency update "${CHART_DIR}" > /dev/null

# --- 4. images ---
# The image list is derived by actually rendering the chart with every
# profile flag on, piped through the same digest-stripping post-renderer
# used at deploy time (scripts/lib/strip-image-digests.py) - not a hardcoded
# list. A hardcoded list would silently go stale the moment
# scripts/set-podiumd-version.sh selects a podiumd release whose bundled
# charts default to different image tags (confirmed live: podiumd 4.7.8
# and 4.8.1 bundle genuinely different nginx-unprivileged versions, for
# example) - this way, whichever podiumd version is currently selected is
# what actually gets pre-pulled, every time.
echo "Deriving the image list from the currently-selected podiumd version..."
mapfile -t images < <(
  helm template podiumd-minikube "${CHART_DIR}" -n podiumd-minikube \
    --set itest.enabled=true \
    --set objecten.enabled=true --set podiumd.objecten.enabled=true \
    --set podiumd.objecttypen.enabled=true \
    --set opennotificaties.enabled=true --set podiumd.opennotificaties.enabled=true \
    --set openarchiefbeheer.enabled=true --set podiumd.openarchiefbeheer.enabled=true \
    --set openformulieren.enabled=true --set podiumd.openformulieren.enabled=true \
    --set metrics.enabled=true \
    2>/dev/null \
  | python3 "${CHART_DIR}/scripts/lib/strip-image-digests.py" \
  | grep -oE '^\s*image:\s*"?[^"[:space:]]+' \
  | sed -E 's/^\s*image:\s*"?//' \
  | sort -u
)
echo "${#images[@]} image(s) referenced by this chart's fully-enabled render."

# Pulled on the host, then loaded into minikube - not pulled directly inside
# minikube, since its inner Docker daemon has no network access at all.
# Batched (not fully parallel) after an earlier full-parallel run of 12
# `minikube image load`s exhausted /tmp on the host with "no space left on
# device" - a moderate batch size gets most of the speedup without that.
BATCH_SIZE=6

echo "Checking which are already loaded in minikube..."
mapfile -t loaded < <(minikube image ls -p "${PROFILE}" 2>/dev/null)
to_fetch=()
for img in "${images[@]}"; do
  found=false
  for l in "${loaded[@]}"; do
    if [[ "${l}" == *"${img}" ]]; then
      found=true
      break
    fi
  done
  if [ "${found}" = false ]; then
    to_fetch+=("${img}")
  fi
done

if [ "${#to_fetch[@]}" -eq 0 ]; then
  echo "All images already loaded - nothing to pull."
else
  echo "${#to_fetch[@]} image(s) need pulling + loading: ${to_fetch[*]}"
  for ((i = 0; i < ${#to_fetch[@]}; i += BATCH_SIZE)); do
    batch=("${to_fetch[@]:i:BATCH_SIZE}")
    echo "Pulling batch: ${batch[*]}"
    for img in "${batch[@]}"; do
      docker pull "${img}" &
    done
    wait
    echo "Loading batch into minikube: ${batch[*]}"
    for img in "${batch[@]}"; do
      minikube image load -p "${PROFILE}" "${img}" &
    done
    wait
  done
fi

echo
echo "Cluster provisioned. Next steps:"
echo "  1. ./scripts/deploy.sh (or ./scripts/deploy.sh --full for every optional profile)."
echo "  2. ./scripts/setup-tunnel.sh for external reachability."
