#!/usr/bin/env bash
# Starts (or confirms) `minikube tunnel` so Traefik gets a real LoadBalancer
# IP reachable directly from the host - the alternative to reaching every
# service through the NodePort/Host-header workaround.
#
# Do NOT run this script itself with `sudo` - `minikube tunnel` handles its
# own privilege escalation internally for the one operation that needs it
# (adding the network route), and `sudo`-ing the whole command instead makes
# minikube look for its profile under root's home directory, where it
# doesn't exist ("Profile \"minikube\" not found").
#
# Usage: ./scripts/setup-tunnel.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TUNNEL_LOG="/tmp/minikube-tunnel.log"
TRAEFIK_NAMESPACE="traefik"
TRAEFIK_SERVICE="traefik"
TIMEOUT_SECONDS=30

# Not just a courtesy check: a wrong context here would read a *different*
# cluster's Traefik IP and generate an /etc/hosts line pointing your
# browser at it - see this repo's own incident notes for why this can't
# be assumed already correct.
source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"
source "${CHART_DIR}/scripts/lib/hosts-line.sh"

external_ip() {
  kubectl get svc "${TRAEFIK_SERVICE}" -n "${TRAEFIK_NAMESPACE}" \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true
}

# Whether an IP is *recorded* on the Service is not proof a tunnel is
# actually routing traffic to it - confirmed live: that field survives the
# `minikube tunnel` process dying (nothing un-assigns it), while the actual
# host route does not. Requests to that IP then just hang instead of
# erroring, which looks exactly like "the tunnel is up" until something
# actually tries to use it. A live process is the real signal - check that
# first, and only trust an existing IP if one is actually running.
if pgrep -f "minikube tunnel" > /dev/null 2>&1; then
  existing_ip="$(external_ip)"
  if [ -n "${existing_ip}" ]; then
    echo "A 'minikube tunnel' process is running (PID $(pgrep -f "minikube tunnel" | head -1)) and Traefik has an external IP (${existing_ip}) - already up."
    echo
    echo "Run ./scripts/update-hosts.sh to add/refresh the /etc/hosts entry for it."
    exit 0
  fi
  echo "A 'minikube tunnel' process is already running (PID $(pgrep -f "minikube tunnel" | head -1)) but Traefik has no external IP yet."
  echo "Waiting up to ${TIMEOUT_SECONDS}s in case it's still settling..."
else
  stale_ip="$(external_ip)"
  if [ -n "${stale_ip}" ]; then
    echo "Traefik has an external IP (${stale_ip}) but no 'minikube tunnel' process is running - stale from an earlier tunnel that's since died. Starting a fresh one..."
  fi

  echo "Caching sudo credentials up front, so the backgrounded tunnel process"
  echo "doesn't need to prompt for a password mid-run (it can't reliably do so once detached):"
  sudo -v

  echo "Starting 'minikube tunnel' in the background (log: ${TUNNEL_LOG})..."
  nohup minikube tunnel > "${TUNNEL_LOG}" 2>&1 &
  disown
fi

echo -n "Waiting for Traefik's external IP"
elapsed=0
while [ "${elapsed}" -lt "${TIMEOUT_SECONDS}" ]; do
  ip="$(external_ip)"
  if [ -n "${ip}" ]; then
    echo
    echo "Tunnel is up. Traefik external IP: ${ip}"
    echo
    echo "Run ./scripts/update-hosts.sh to add/refresh the /etc/hosts entry for it."
    exit 0
  fi
  echo -n "."
  sleep 2
  elapsed=$((elapsed + 2))
done

echo
echo "Timed out after ${TIMEOUT_SECONDS}s waiting for Traefik's external IP." >&2
echo "Tunnel log (${TUNNEL_LOG}):" >&2
tail -20 "${TUNNEL_LOG}" >&2 2>/dev/null || echo "  (no log file yet)" >&2
exit 1
