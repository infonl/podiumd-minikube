#!/usr/bin/env bash
# Sourced (not executed) by every script that runs kubectl against this
# project's cluster. Refuses to proceed unless the current kubectl context
# is exactly "minikube".
#
# Confirmed live (2026-08-05/06): kubectl's current-context silently
# drifted to an unrelated real Azure AKS cluster mid-session - root cause
# never fully determined. A `deploy.sh` run and a non-idempotent Job
# delete/recreate (scripts/lib/apply-pabc-migrations.sh) landed on that cluster's own
# `podiumd-minikube` namespace before anyone noticed, breaking its
# postgres Deployment. This project only ever targets the local `minikube`
# profile - never assume a context set correctly earlier in a session is
# still correct now; every mutating script re-checks this itself instead
# of relying on the caller having checked.
REQUIRED_CONTEXT="minikube"
current_context="$(kubectl config current-context 2>/dev/null || true)"
if [ "${current_context}" != "${REQUIRED_CONTEXT}" ]; then
  echo "REFUSING to proceed: kubectl's current-context is '${current_context:-<none>}', not '${REQUIRED_CONTEXT}'." >&2
  echo "This project only ever targets the local minikube cluster. Run:" >&2
  echo "  kubectl config use-context ${REQUIRED_CONTEXT}" >&2
  echo "and try again." >&2
  exit 1
fi
