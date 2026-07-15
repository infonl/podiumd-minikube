# podiumd-minikube

A standalone Helm chart that reproduces the `dimpact-zaakafhandelcomponent`
docker-compose dev stack (ZAC + its ZGW dependencies) for local development on
minikube.

## Relationship to other repos

- **`dimpact-zaakafhandelcomponent`** (`~/development/werk/infonl-dimpact/infonl/dimpact-zaakafhandelcomponent`)
  — source of truth for the stack this chart reproduces:
  `docker-compose.yaml` + `docker-compose.override.yml`. Also publishes the
  `charts/zac` production Helm chart, which this project depends on remotely
  (`@zac` repo, `https://infonl.github.io/dimpact-zaakafhandelcomponent/`) —
  not as a local path dependency, since this repo stands alone.
- **PodiumD** (`~/development/werk/infonl-dimpact/dimpact-samenwerking/helm-charts/charts/podiumd`)
  — production umbrella chart for the same ZGW ecosystem (Azure-oriented:
  Keycloak Operator, Redis Operator HA, APISIX, cert-manager). This project
  reuses PodiumD's *dependency choices* (which Helm chart to use for each ZGW
  component: openzaak, openklant, objecten, objecttypen, opennotificaties,
  openarchiefbeheer, openforms, pabc, brp-personen-mock) but replaces its
  cloud-operator infrastructure with plain single-container Deployments
  suited to a single-node minikube box.
- **`podiumd-infra`** (`~/development/werk/infonl-dimpact/icatt-menselijk-digitaal/podiumd-infra`)
  — operational scripts/docs for real PodiumD environments (Traefik + Ingress
  patterns, cert-manager setup). Referenced for the Traefik ingress pattern
  used here, minus the production TLS/cert-manager/Let's Encrypt parts.

## Structure

This repo *is* the chart — no nested `charts/<name>` wrapper. `Chart.yaml`,
`values.yaml`, `templates/`, `vendor/` live at the repo root.

`vendor/dimpact-zaakafhandelcomponent/` holds physical copies of file assets
this chart needs from that repo (Keycloak realm JSON, WireMock mappings, DB
init/seed SQL, metrics configs, OPA policies) — never live cross-repo
references. See `vendor/NOTES.md` (once created) for provenance per file.

## Current state

See `.claude/plans/plan.md` for the full design plan (dependencies, wiring,
resource-footprint decisions, resolved issues). As of this writing the plan is
fully designed but **implementation has not started** — no `Chart.yaml`,
`values.yaml`, or `templates/` exist yet, and `vendor/` is empty. Next step:
plan build-order step 0 (vendoring file assets from
`dimpact-zaakafhandelcomponent`, including patching the Keycloak realm JSON's
redirect URIs and merging the three per-service Postgres seed scripts into
one).
