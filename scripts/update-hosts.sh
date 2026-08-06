#!/usr/bin/env bash
# Adds or refreshes this chart's *.local -> Traefik-IP line in /etc/hosts.
# Idempotent - safe to re-run any time (e.g. after the tunnel picks up a
# new IP, or after a new profile adds a new hostname): removes this
# chart's own previous line(s) first (matched by containing "zac.local",
# not just this script's own marker comment, so it also cleans up a line
# added by hand or by an older version of this script that predates the
# marker) before appending a fresh one - never accumulates duplicates or
# leaves a stale IP/an incomplete hostname list behind.
#
# Needs sudo to edit /etc/hosts - prompts once up front via `sudo -v`,
# same approach as setup-tunnel.sh's own privilege-caching step.
#
# Usage: ./scripts/update-hosts.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRAEFIK_NAMESPACE="traefik"
TRAEFIK_SERVICE="traefik"
MARKER="# podiumd-minikube"

source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"
source "${CHART_DIR}/scripts/lib/hosts-line.sh"

ip="$(kubectl get svc "${TRAEFIK_SERVICE}" -n "${TRAEFIK_NAMESPACE}" \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"

if [ -z "${ip}" ]; then
  echo "Traefik has no external IP yet - run ./scripts/setup-tunnel.sh first." >&2
  exit 1
fi

echo "Caching sudo credentials up front..."
sudo -v

if grep -qF "zac.local" /etc/hosts 2>/dev/null; then
  echo "Removing existing podiumd-minikube /etc/hosts line(s)..."
  sudo sed -i.bak "/zac\.local/d" /etc/hosts
fi

echo "Adding current entry (${ip})..."
echo "$(hosts_line "${ip}")  ${MARKER}" | sudo tee -a /etc/hosts > /dev/null

echo
echo "Done. /etc/hosts now has:"
grep -F "${MARKER}" /etc/hosts
