#!/usr/bin/env bash
# Shows how host traffic actually reaches each app in this stack: not a
# per-service host port the way docker-compose worked (each container
# publishing its own port), but a single host-reachable entry point -
# Traefik's LoadBalancer IP on port 80 (see scripts/setup-tunnel.sh) -
# multiplexing every *.local hostname to a different backend Service:port
# by HTTP Host header. This script reads the live cluster's actual Ingress
# objects (kubectl get ingress -n podiumd-minikube), not a static list, so
# it only ever shows whatever profiles are really deployed right now - and
# resolves each backend's named port (e.g. "http") to the real number via
# the target Service, since Ingress specs mix numeric ports and names
# depending on how each upstream chart authored its own Ingress template.
#
# Traffic type per row is a small hand-maintained annotation (this
# project's own knowledge of what each app actually is), not something
# derivable from Kubernetes metadata - kept in the TRAFFIC_TYPE lookup
# below.
#
# Usage:
#   ./scripts/show-port-mappings.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="podiumd-minikube"
TRAEFIK_NAMESPACE="traefik"
TRAEFIK_SERVICE="traefik"

source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"

# One-line description per Ingress hostname - purely descriptive, not read
# by anything else. Falls back to "(no description on file)" for any host
# this list hasn't caught up with yet, rather than silently omitting it -
# see this project's own "no silent gaps" habit (plan.md).
declare -A TRAFFIC_TYPE=(
  [zac.local]="Web app UI - redirects unauthenticated requests to Keycloak (OIDC), see tests/test_login_flow.py"
  [keycloak.local]="Identity provider - OIDC login/token endpoints + its own admin console"
  [openzaak.local]="ZGW REST API + Django admin (username/password, see tests/test_django_admin_login.py)"
  [openklant.local]="ZGW REST API + Django admin"
  [pabc.local]="REST API (authorization/role-mapping data ZAC queries)"
  [solr.local]="Search engine - redirects / to its own /solr/ admin UI"
  [objecten.local]="Objects API (REST) + Django admin"
  [objecttypen.local]="Objecttypes API (REST) + Django admin - only exists on the classic podiumd shape, see scripts/lib/detect-objecten-shape.sh"
  [opennotificaties.local]="Notifications API (REST) + Django admin"
  [openarchiefbeheer-web.local]="SPA (served by the shared nginx sidecar backend)"
  [openarchiefbeheer-ui.local]="Same nginx sidecar as -web.local, second Ingress hostname for compose parity"
  [openformulieren-nginx.local]="Forms SPA/API (served by the shared nginx sidecar backend)"
  [openformulieren-web.local]="Same nginx sidecar as -nginx.local, second Ingress hostname for compose parity"
  [grafana.local]="Metrics dashboard UI (own auth, not this project's django-admin family)"
  [mailpit.local]="SMTP test server's own web UI - no auth"
)

traefik_ip="$(kubectl get svc "${TRAEFIK_SERVICE}" -n "${TRAEFIK_NAMESPACE}" \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"

echo "== Host entry point =="
if [ -n "${traefik_ip}" ]; then
  echo "Traefik LoadBalancer IP: ${traefik_ip} (reachable once 'minikube tunnel' is running - see scripts/setup-tunnel.sh)"
else
  echo "Traefik has no external IP yet - 'minikube tunnel' isn't running (see scripts/setup-tunnel.sh)."
fi
echo "  port 80  (web)        - HTTP, all traffic below - every hostname multiplexed onto this ONE port by Host header"
echo "  port 443 (websecure)  - TCP listener exists but unused: no Ingress here sets spec.tls, so nothing terminates HTTPS on it"
echo

# Resolves a Service's named port to its numeric port. Ingress backends in
# this cluster mix both forms (some upstream charts' own Ingress templates
# reference a numeric port.number, others a symbolic port.name) - normalize
# to a number here so the table below is consistent either way.
resolve_port() {
  local service="$1" port="$2"
  if [[ "${port}" =~ ^[0-9]+$ ]]; then
    echo "${port}"
    return
  fi
  kubectl get svc "${service}" -n "${NAMESPACE}" \
    -o jsonpath="{.spec.ports[?(@.name==\"${port}\")].port}" 2>/dev/null || echo "?"
}

printf "%-30s %-32s %s\n" "HOST" "BACKEND (svc:port -> real port)" "TRAFFIC TYPE"
printf "%-30s %-32s %s\n" "----" "-------------------------------" "------------"

# Same reasoning as deploy.sh's own LARGE_CONFIGMAPS_FILE: the while loop
# below runs in a pipeline subshell, so a plain variable set inside it
# wouldn't survive back to this shell - a temp file does. mktemp+trap
# instead of a fixed $$-based name so an interrupted run doesn't leave
# stale files behind.
SEEN_HOSTS_FILE="$(mktemp)"
trap 'rm -f "${SEEN_HOSTS_FILE}"' EXIT

kubectl get ingress -n "${NAMESPACE}" -o jsonpath=\
'{range .items[*]}{range .spec.rules[*]}{.host}{"\t"}{range .http.paths[*]}{.backend.service.name}{" "}{.backend.service.port.number}{.backend.service.port.name}{"\n"}{end}{end}{end}' \
  | sort -u \
  | while IFS=$'\t' read -r host backend; do
      svc_name="$(awk '{print $1}' <<< "${backend}")"
      svc_port="$(awk '{print $2}' <<< "${backend}")"
      real_port="$(resolve_port "${svc_name}" "${svc_port}")"
      traffic="${TRAFFIC_TYPE[${host}]:-(no description on file)}"
      printf "%-30s %-32s %s\n" "${host}" "${svc_name}:${svc_port} -> ${real_port}" "${traffic}"
      echo "${host}" >> "${SEEN_HOSTS_FILE}"
    done

# Duplicate hostnames mean two Ingress objects both claim it - found live
# once already: templates/metrics/'s own grafana.local Ingress and
# monitoring-logging's bundled one can both exist simultaneously if
# monitoringLogging.enabled was ever toggled without the manual cleanup
# pass values.yaml's own comment on that key calls out (kubectl apply never
# deletes resources that drop out of a render). Surfaced here instead of
# silently picking one - Traefik itself resolves the conflict by whichever
# rule it happened to load, which host actually wins isn't guaranteed.
dupes="$(sort "${SEEN_HOSTS_FILE}" | uniq -d)"
if [ -n "${dupes}" ]; then
  echo
  echo "WARNING: hostname(s) claimed by more than one Ingress object - Traefik's" >&2
  echo "own routing decision between them isn't guaranteed. Likely a leftover" >&2
  echo "Ingress from toggling monitoringLogging.enabled without the manual" >&2
  echo "cleanup pass (see values.yaml's own comment on that key):" >&2
  echo "${dupes}" | sed 's/^/  /' >&2
fi
