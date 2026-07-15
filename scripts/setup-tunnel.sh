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

TUNNEL_LOG="/tmp/minikube-tunnel.log"
TRAEFIK_NAMESPACE="traefik"
TRAEFIK_SERVICE="traefik"
TIMEOUT_SECONDS=30

hosts_line() {
  local ip="$1"
  echo "${ip} zac.local keycloak.local openzaak.local openklant.local pabc.local solr.local objecten.local objecttypen.local opennotificaties.local openarchiefbeheer-web.local openarchiefbeheer-ui.local openformulieren-nginx.local openformulieren-web.local grafana.local greenmail.local"
}

external_ip() {
  kubectl get svc "${TRAEFIK_SERVICE}" -n "${TRAEFIK_NAMESPACE}" \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true
}

existing_ip="$(external_ip)"
if [ -n "${existing_ip}" ]; then
  echo "Traefik already has an external IP (${existing_ip}) - tunnel appears to be running already."
  echo
  echo "Add this to /etc/hosts if you haven't already:"
  echo "  echo \"$(hosts_line "${existing_ip}")\" | sudo tee -a /etc/hosts"
  exit 0
fi

if pgrep -f "minikube tunnel" > /dev/null 2>&1; then
  echo "A 'minikube tunnel' process is already running (PID $(pgrep -f "minikube tunnel" | head -1)) but Traefik has no external IP yet."
  echo "Waiting up to ${TIMEOUT_SECONDS}s in case it's still settling..."
else
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
    echo "Add this to /etc/hosts if you haven't already:"
    echo "  echo \"$(hosts_line "${ip}")\" | sudo tee -a /etc/hosts"
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
