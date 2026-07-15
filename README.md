yes# podiumd-minikube

A standalone Helm chart that reproduces the `dimpact-zaakafhandelcomponent`
docker-compose dev stack (ZAC + its ZGW dependencies: Open Zaak, Open
Klant, PABC, Objecten, Objecttypen, Open Notificaties, Open
Archiefbeheer, Open Formulieren) for local development on minikube.

For the *why* behind every design decision here — dependency choices,
resource-footprint tradeoffs, and every bug found and fixed along the
way — see [`.claude/plans/plan.md`](.claude/plans/plan.md). This README
covers the *how*.

## Prerequisites

- [minikube](https://minikube.sigs.k8s.io/), Docker, `kubectl`, `helm`
  (see `scripts/provision-cluster.sh` for the exact Traefik chart version
  this project is compatible with if your `helm` binary is old)
- Enough free RAM/CPU on the host for a sized-up minikube VM (default
  6 CPUs / 16Gi — see below). This isn't just a recommendation: the full
  stack (all optional profiles, ~35 pods) has been observed live pushing an
  under-provisioned cluster into severe CPU/memory thrashing that makes the
  whole API server unresponsive. `provision-cluster.sh` only applies this
  sizing when it *creates* a profile — if you already have a `minikube`
  profile running from before (e.g. from an older version of this project),
  it checks the running container's actual memory allocation on every run
  and warns if it's below 16Gi, with the exact command to raise it live
  (see "Troubleshooting" below)
- Python 3 + `pip` if you want to run the test suite

## Quick start

```bash
./scripts/provision-cluster.sh      # starts minikube, installs Traefik, pre-loads every image
./scripts/deploy.sh --full          # renders and applies the chart (every optional profile on)
./scripts/setup-tunnel.sh           # starts `minikube tunnel`, prints the /etc/hosts line to add
```

Then add the printed line to `/etc/hosts` (the script gives you the exact
`sudo tee -a` command), and open `http://zac.local` in a browser — it
redirects to Keycloak, and back to the authenticated app on login.

Leave off `--full` on `deploy.sh` to deploy just the core profile (ZAC,
Open Zaak, Open Klant, PABC, Postgres/Redis/Solr/Keycloak/WireMock) —
matches `values.yaml`'s own default, mirroring compose's own
no-profile-flags behavior.

## What's running

**Core (always on):** zac, openzaak, openklant, pabc, brp-personen-mock,
postgres, redis, solr, keycloak, wiremock.

**Optional profiles** (each is its own `values.yaml` top-level flag,
default `false` — `deploy.sh --full` turns all of them on):

| Profile | Adds |
|---|---|
| `objecten` | Objecten API + its celery worker (Objecttypen has no top-level flag of its own — `deploy.sh --full` enables it alongside `objecten` via `podiumd.objecttypen.enabled` directly) |
| `openarchiefbeheer` | Open Archiefbeheer (web + nginx + worker + beat) |
| `opennotificaties` | Open Notificaties + RabbitMQ |
| `openformulieren` | Open Formulieren (+ transitively needs `objecten`, `objecttypen`, `opennotificaties` enabled too — matches compose's own profile nesting) |
| `metrics` | otel-collector, Tempo, Prometheus, Grafana |
| `itest` | extra WireMock mappings (SmartDocuments/KVK/BAG), Greenmail, the `opa-tests` Job |

Ingress hostnames (all `*.local`, reachable once the tunnel + `/etc/hosts`
entry are set up): `zac`, `keycloak`, `openzaak`, `openklant`, `pabc`,
`solr`, `objecten`, `objecttypen`, `opennotificaties`,
`openarchiefbeheer-web`/`-ui`, `openformulieren-nginx`/`-web`, `grafana`,
`greenmail`.

## Scripts

| Script | What it does |
|---|---|
| `provision-cluster.sh` | Starts minikube (sized for the full stack), installs Traefik, pre-pulls/loads every image this chart references, runs `helm dependency update` |
| `deploy.sh` | Renders and applies the chart (`--full` for every optional profile) |
| `setup-tunnel.sh` | Starts `minikube tunnel` so Traefik gets a real IP reachable from the host; idempotent, prints the `/etc/hosts` line either way |
| `teardown-cluster.sh` | Deletes the entire minikube cluster (asks for confirmation; `--yes` to skip) |
| `set-podiumd-version.sh <version>` | Swaps the `podiumd` Helm dependency to a different version (`helm search repo dimpact/podiumd -l` to list available ones) — re-check the four intentional image-tag pins in `values.yaml` afterward, per that script's own comment |
| `apply-pabc-migrations.sh` | The **only** safe way to (re)create the `pabc-migrations` Job — it's not idempotent (clears PABC's database before reseeding), so this refuses to run against an already-seeded database unless `--force` is passed |

`deploy.sh` already calls `apply-pabc-migrations.sh` itself as its own last
step, every run — you don't need to run it by hand for a normal deploy,
first or repeat. It's excluded from the general manifest apply on purpose
(that Job clears PABC's database before reloading its seed dataset every
time it *runs*, so letting a plain unguarded `kubectl apply` recreate it —
which would happen silently if it were ever missing — isn't safe). You'd
only ever run it directly yourself in the one case `deploy.sh`'s own call
refuses: the Job is missing but PABC's database already has real data, and
you need to decide whether `--force` (wipe and reseed) is really intended.

`scripts/lib/` holds internal helpers that aren't meant to be run directly —
they're only ever piped into by the scripts above:

| Script | What it does |
|---|---|
| `strip-image-digests.py` | Helm post-renderer piped into automatically by `deploy.sh`/`provision-cluster.sh` — strips `@sha256:...` suffixes so images resolve to the tag-only references pre-loaded into minikube (which has no outbound network access) |
| `disable-service-links.py` | Helm post-renderer piped into automatically by `deploy.sh` — sets `enableServiceLinks: false` on every workload pod spec, avoiding Kubernetes' auto-injected `<SERVICE_NAME>_PORT`-style env vars colliding with app-expected ones of the same name |
| `exclude-pabc-migration-job.py` | Helm post-renderer piped into automatically by `deploy.sh` — drops the `pabc-migrations` Job from the general manifest apply, since `apply-pabc-migrations.sh` is the only safe way to (re)create it |

## Testing

```bash
cd tests
pip install -r requirements.txt
pytest
```

Live-cluster integration tests, not unit tests — see
[`tests/README.md`](tests/README.md) for full coverage, prerequisites, and
caveats (notably: one test in `test_pabc_migrations_guard.py` deliberately
mutates and restores real cluster state to prove a safety guard actually
works). Tests for profiles that aren't currently deployed auto-skip.

## Why not `helm install`?

This project uses `helm template | strip-image-digests.py | kubectl apply`
instead of `helm install`/`helm upgrade`. Helm's own release record embeds
the entire resolved chart — including the ~3.87MB `podiumd` dependency —
which exceeds Kubernetes' hardcoded 3MB API request-size limit (no flag
exists to raise it in current Kubernetes versions). One consequence: Helm's
install/upgrade hooks never fire, since they need a live Helm release that
this workflow never creates — `deploy.sh` handles the one place that
matters (`templates/storage-hooks.yaml`'s PV/PVC pre-provisioning) by
applying that file before the rest of the manifest instead. Full details in
`plan.md`'s step 4 notes.

## Troubleshooting

**Cluster becomes sluggish or unresponsive (`kubectl` hangs or times out on
TLS handshake), especially after switching between profile combinations a
few times.** Usually an under-provisioned minikube VM thrashing under the
full stack's real memory pressure, not an application bug — check with
`docker stats minikube` (for the docker driver) or `minikube ssh -- free -h`.
`provision-cluster.sh` checks for this on every run and warns if the running
profile is below its recommended 16Gi, but only for profiles it can inspect
via the docker driver. To raise it live, without restarting anything:
```bash
docker update --memory=16g --memory-swap=-1 minikube
```
This doesn't persist across `minikube delete` — that recreates the container
fresh from `MINIKUBE_MEMORY`, so it's a one-time fix per existing profile,
not something you need to repeat.

## Project structure

```
Chart.yaml, values.yaml, templates/   # the chart itself (this repo IS the chart, no nested wrapper)
vendor/dimpact-zaakafhandelcomponent/  # physical copies of file assets from that repo (see vendor/NOTES.md)
scripts/                               # cluster lifecycle + deploy-time tooling (see table above)
scripts/lib/                           # internal helpers, not run directly (see table above)
tests/                                 # live-cluster pytest suite
.claude/plans/plan.md                  # full design + build log
```
