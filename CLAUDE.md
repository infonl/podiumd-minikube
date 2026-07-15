# podiumd-minikube

A standalone Helm chart that reproduces the `dimpact-zaakafhandelcomponent`
docker-compose dev stack (ZAC + its ZGW dependencies) for local development on
minikube.

For usage instructions (provisioning, deploying, the test suite, script
reference) see [`README.md`](README.md). This file is oriented at AI coding
tools instead: cross-repo relationships and local paths a README shouldn't
hardcode, plus a pointer to the full design/build history.

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
`values.yaml`, `templates/`, `vendor/` live at the repo root, alongside
`scripts/` (cluster lifecycle + deploy-time tooling) and `tests/` (a
live-cluster pytest suite).

`vendor/dimpact-zaakafhandelcomponent/` holds physical copies of file assets
this chart needs from that repo (Keycloak realm JSON, WireMock mappings, DB
init/seed SQL, metrics configs, OPA policies, PABC's role/domain mapping
dataset) — never live cross-repo references. See `vendor/dimpact-zaakafhandelcomponent/NOTES.md`
for provenance per file.

## Current state

All six build-order steps in `.claude/plans/plan.md` are complete and have
been verified live against a real minikube cluster (not just rendered):
vendoring, chart skeleton, core raw templates, wiring the core apps, a full
live deploy + OIDC login flow through `http://zac.local`, and every optional
profile group (objecten, opennotificaties, openarchiefbeheer,
openformulieren, metrics, itest). `plan.md` is a build *log* at this point,
not a forward-looking plan — it records every bug found and fixed along the
way (many only discoverable by actually deploying, not by reading source),
in the order they were found. Read it when you need the reasoning behind a
specific `values.yaml` override or template decision; read `README.md` when
you just need to run the thing.
