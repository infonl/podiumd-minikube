#!/usr/bin/env bash
# Provisions a fresh minikube cluster ready to receive this chart:
#   1. starts minikube with enough CPU/memory for the full stack
#      (all step 5 profiles enabled, not just the core ones)
#   2. installs Traefik, pinned to a version compatible with older `helm`
#      binaries (see the version note below) - a cluster prerequisite this
#      chart deliberately doesn't manage itself
#   3. runs `helm dependency update` (via scripts/lib/podiumd-dependency.sh,
#      so a local scripts/set-podiumd-version.sh override is respected too)
#      so the podiumd chart tarball is present
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

# NOTE for Apple Silicon + colima: don't set DOCKER_DEFAULT_PLATFORM=linux/amd64
# globally in this script. Doing so forces the minikube *node* container
# itself to be amd64 - but minikube's own kubeadm/kubelet/kubectl binary
# selection follows the minikube CLI's host GOARCH (arm64 on Apple Silicon),
# not the node container's platform, so it copies arm64 binaries into an
# amd64 rootfs and `kubeadm init` fails ("exec format error" / "no such file
# or directory" - the arm64 binaries have no matching ELF interpreter in the
# amd64 container). Confirmed live. Let the node be native arm64 (fast,
# reliable) and only force amd64 on the workload images pulled in step 4
# below - Kubernetes doesn't care that the node and pod images differ in
# architecture as long as the shared kernel can execute both, which colima's
# `--vz-rosetta` binfmt registration provides transparently.
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
  # --driver=docker explicitly: left on "auto", minikube silently falls back
  # to qemu2 on failure (see the DOCKER_DEFAULT_PLATFORM comment above) -
  # forcing it here turns that into a loud failure instead of a cluster that
  # looks fine but can't run this chart's images.
  minikube start -p "${PROFILE}" --driver=docker --cpus="${MINIKUBE_CPUS}" --memory="${MINIKUBE_MEMORY}"
fi

# `minikube start` sets kubectl's current-context itself, but the
# already-running branch above doesn't touch it at all - re-check
# explicitly before the first kubectl call either way (see that script's
# own header for why this can't be assumed to already be correct).
source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"

# The optional monitoring-logging dependency's own alloy subchart (see
# values.yaml's own monitoringLogging comment) hardcodes an AKS-only
# nodeSelector on its DaemonSet (kubernetes.azure.com/agentpool: userpool) -
# confirmed live that clearing it via a values.yaml/--set override doesn't
# work reliably this deep in the subchart tree on this project's pinned Helm
# v3.9.0 (see values.yaml's own alloy comment for the full story). Labeling
# the node to actually satisfy the selector instead is simpler and doesn't
# depend on Helm merge internals - harmless if monitoringLogging is never
# enabled, so this always runs, not just when it's going to be used.
kubectl label node minikube kubernetes.azure.com/agentpool=userpool --overwrite > /dev/null

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
# podiumd chart tarball to already be present. Goes through
# sync_podiumd_dependencies rather than a plain `helm dependency update`
# call, so .podiumd-versions.yaml (see scripts/set-podiumd-version.sh) is
# respected here too, not just in deploy.sh - exits with a clear message
# if you haven't run that script yet (Chart.yaml no longer holds a real
# version to fall back to at all).
echo "Running helm dependency update..."
source "${CHART_DIR}/scripts/lib/podiumd-dependency.sh"
sync_podiumd_dependencies

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
#
# monitoringLogging.enabled=true is included here even though it's not part
# of scripts/deploy.sh --full's own default set (see that script's own
# comment - it's a separate, explicit opt-in) - this render only decides
# what gets pre-pulled, so including it means those images (loki/alloy/
# grafana/tempo/kube-prometheus-stack) are already loaded if someone enables
# it later, without needing to re-run this script.
echo "Deriving the image list from the currently-selected podiumd version..."
source "${CHART_DIR}/scripts/lib/detect-objecten-shape.sh"
mapfile -t images < <(
  helm template podiumd-minikube "${CHART_DIR}" -n podiumd-minikube \
    --set wiremock.enabled=true \
    --set objecten.enabled=true --set podiumd.objecten.enabled=true \
    "${OBJECTEN_SHAPE_SETS[@]}" \
    --set opennotificaties.enabled=true --set podiumd.opennotificaties.enabled=true \
    --set openarchiefbeheer.enabled=true --set podiumd.openarchiefbeheer.enabled=true \
    --set openformulieren.enabled=true --set podiumd.openformulieren.enabled=true \
    --set metrics.enabled=true \
    --set monitoringLogging.enabled=true \
    2>/dev/null \
  | python3 "${CHART_DIR}/scripts/lib/strip-image-digests.py" \
  | grep -oE '^ *image: *"?[^"[:space:]]+' \
  | sed -E 's/^ *image: *"?//' \
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
      # --platform: this project's images are amd64-only (see CLAUDE.md) -
      # on an Apple Silicon host with a native-arch (arm64) minikube node,
      # docker would otherwise default to pulling an arm64 variant (or fail
      # outright for images with no arm64 build at all).
      docker pull --platform linux/amd64 "${img}" &
    done
    wait
    echo "Loading batch into minikube: ${batch[*]}"
    for img in "${batch[@]}"; do
      # `minikube image load <image-ref>` (with or without --daemon) fails
      # here with "unable to calculate manifest ... content digest ... not
      # found": it tries to resolve the image's manifest as if the node's
      # own architecture (arm64) has a matching variant, which an amd64-only
      # image doesn't have. Saving to a plain tarball first and loading
      # *that* sidesteps the arch-aware manifest lookup entirely - confirmed
      # live this loads amd64 images cleanly onto the arm64 node (the node's
      # containerd runs them fine via colima's Rosetta-backed emulation).
      tar_path="$(mktemp -t "minikube-image-XXXXXX").tar"
      (
        docker save --platform linux/amd64 "${img}" -o "${tar_path}" \
          && minikube image load -p "${PROFILE}" "${tar_path}"
        rm -f "${tar_path}"
      ) &
    done
    wait
  done
fi

echo
echo "Cluster provisioned. Next steps:"
echo "  1. ./scripts/deploy.sh (or ./scripts/deploy.sh --full for every optional profile;"
echo "     the loki/alloy/grafana/tempo stack vs. the metrics profile's raw templates is"
echo "     picked up automatically from values.yaml's monitoringLogging.enabled)."
echo "  2. ./scripts/setup-tunnel.sh for external reachability."
