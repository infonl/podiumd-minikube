#!/usr/bin/env bash
# Reconciles the *live*, already-imported Keycloak realm's own
# "zaakafhandelcomponent" client pkce.code.challenge.method attribute to
# match ZAC_EXPERIMENTAL_PKCE (set by deploy.sh from
# scripts/lib/zac-experimental-pkce.sh) - runs unconditionally, every
# deploy.sh run (keycloak is core/always-on in this chart, no profile
# guard needed the way seed-fixtures.sh has for objecten).
#
# Why this has to exist at all, separate from
# scripts/lib/fixup-zac-pkce-realm.py's own ConfigMap patch: Keycloak's
# own `--import-realm` (templates/keycloak/deployment.yaml's own args)
# only ever imports a realm that doesn't already exist yet - confirmed
# live via its own boot log ("Realm 'zaakafhandelcomponent' already
# exists. Import skipped"). This project's own Keycloak persists to the
# shared Postgres instance (KC_DB=postgres in that same deployment.yaml,
# not an ephemeral/ in-pod store), so that's true of every restart after
# the very first one, indefinitely - a values.yaml/vendored-realm.json
# edit alone never reaches an already-provisioned cluster's live realm,
# no matter how many times the pod restarts. Confirmed live the hard way
# once already before this script existed: patched by hand via `kcadm.sh`
# inside the running pod - this is that same fix, made idempotent and
# automatic instead.
#
# Safe to run unconditionally on every deploy: `kcadm.sh update` to a
# value it already has is a no-op, and the client itself is part of the
# vendored realm.json every deploy already relies on existing.
set -euo pipefail

NAMESPACE="podiumd-minikube"
REALM="zaakafhandelcomponent"
CLIENT_ID="zaakafhandelcomponent"
# Matches templates/keycloak/deployment.yaml's own KC_BOOTSTRAP_ADMIN_USERNAME/
# KC_BOOTSTRAP_ADMIN_PASSWORD literals exactly - no values.yaml field
# overrides these, so hardcoding here can't drift out of sync silently.
ADMIN_USER="admin"
ADMIN_PASSWORD="admin"

if ! kubectl get deployment/keycloak -n "${NAMESPACE}" > /dev/null 2>&1; then
  echo "'keycloak' not found - skipping sync-zac-pkce-realm.sh."
  exit 0
fi

TARGET="S256"
if [ "${ZAC_EXPERIMENTAL_PKCE:-false}" != true ]; then
  TARGET=""
fi

echo "Reconciling Keycloak's live '${REALM}' realm PKCE requirement (target: '${TARGET}')..."

# One retry loop covering both login and client lookup, not just the
# latter - confirmed live that right after a fresh deploy (e.g. following
# reset-namespace.sh) Keycloak's own port isn't listening yet at all,
# which fails the *login* step outright ("Connection refused"), not just
# an empty client lookup. `kcadm.sh config credentials` itself doesn't
# fail loudly enough to distinguish "server not up yet" from "bad
# creds" through its own exit code alone here, so this just retries the
# whole sequence from scratch each pass rather than trying to tell those
# apart.
CLIENT_UUID=""
deadline=$((SECONDS + 90))
while [ "${SECONDS}" -lt "${deadline}" ]; do
  if kubectl exec -n "${NAMESPACE}" deploy/keycloak -- /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 --realm master --user "${ADMIN_USER}" --password "${ADMIN_PASSWORD}" > /dev/null 2>&1; then
    CLIENT_UUID="$(kubectl exec -n "${NAMESPACE}" deploy/keycloak -- /opt/keycloak/bin/kcadm.sh get clients \
      -r "${REALM}" -q "clientId=${CLIENT_ID}" --fields id --format csv --noquotes 2>/dev/null | tr -d '[:space:]')"
    [ -n "${CLIENT_UUID}" ] && break
  fi
  sleep 3
done

if [ -z "${CLIENT_UUID}" ]; then
  echo "WARNING: could not reach Keycloak or find '${CLIENT_ID}' in realm" >&2
  echo "'${REALM}' after 90s - either it's still starting, or the vendored" >&2
  echo "realm import hasn't completed yet. Not syncing PKCE this run - safe to" >&2
  echo "re-run deploy.sh once Keycloak is up." >&2
  exit 0
fi

kubectl exec -n "${NAMESPACE}" deploy/keycloak -- /opt/keycloak/bin/kcadm.sh update "clients/${CLIENT_UUID}" \
  -r "${REALM}" -s "attributes.\"pkce.code.challenge.method\"=${TARGET}" > /dev/null

echo "Keycloak's live '${CLIENT_ID}' client PKCE requirement set to '${TARGET}'."
