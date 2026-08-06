#!/usr/bin/env bash
# Sourced (not executed) by setup-tunnel.sh and update-hosts.sh. Single
# source of truth for the *.local hostnames this chart's Ingresses can
# produce - found live: setup-tunnel.sh's own copy of this list had gone
# stale (missing several profile hostnames added since), and having it
# duplicated in a second script would only make that worse.
hosts_line() {
  local ip="$1"
  echo "${ip} zac.local keycloak.local openzaak.local openklant.local pabc.local solr.local objecten.local objecttypen.local opennotificaties.local openarchiefbeheer-web.local openarchiefbeheer-ui.local openformulieren-nginx.local openformulieren-web.local grafana.local mailpit.local"
}
