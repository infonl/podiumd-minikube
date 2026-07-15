#!/usr/bin/env bash
# Deletes the entire minikube cluster this chart was deployed to - every pod,
# PV/PVC (including hostPath data under /data/podiumd-minikube on the
# minikube node), namespace, and the underlying VM/container itself. This is
# NOT reversible: there is no confirmation step built into `minikube delete`
# itself, which is why this script adds one.
#
# Also stops any running `minikube tunnel` process first (see
# scripts/setup-tunnel.sh) - it becomes a useless orphaned process once the
# cluster it's tunneling into is gone.
#
# Usage:
#   ./scripts/teardown-cluster.sh          # asks for confirmation first
#   ./scripts/teardown-cluster.sh --yes    # skips the confirmation prompt
set -euo pipefail

PROFILE="minikube"
SKIP_CONFIRM=false

if [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; then
  SKIP_CONFIRM=true
fi

echo "This will permanently delete the '${PROFILE}' minikube cluster:"
echo "  - every pod, Deployment, Service, Ingress, PV/PVC in it"
echo "  - all hostPath data under /data/podiumd-minikube on the minikube node"
echo "    (Postgres, Solr, and every app's persisted media/uploads)"
echo "  - the minikube VM/container itself"
echo
echo "It does NOT touch this chart's own files, git history, or anything"
echo "outside the minikube cluster (e.g. locally pulled/loaded Docker images"
echo "on the host survive, so a fresh cluster can be re-populated without"
echo "re-pulling everything from the internet)."
echo

if [ "${SKIP_CONFIRM}" = false ]; then
  read -r -p "Type 'yes' to continue: " confirmation
  if [ "${confirmation}" != "yes" ]; then
    echo "Aborted - nothing was deleted."
    exit 1
  fi
fi

if pgrep -f "minikube tunnel" > /dev/null 2>&1; then
  echo "Stopping the running 'minikube tunnel' process..."
  pkill -f "minikube tunnel" || true
fi

echo "Deleting minikube profile '${PROFILE}'..."
minikube delete -p "${PROFILE}"

echo
echo "Done. Remember: any '*.local' entries you added to /etc/hosts pointing"
echo "at the old Traefik IP are now stale - remove them, or update them once"
echo "a new cluster + tunnel are up again."
