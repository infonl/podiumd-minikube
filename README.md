# podiumd-minikube

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
./scripts/set-podiumd-version.sh <version> --disable-monitoring-logging  # optional, defaults to the version pinned in Chart.yaml
                                     # (or --path <dir> for a local podiumd chart checkout)
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
postgres, redis, solr, keycloak, wiremock, mailpit (SMTP test server —
every app's email settings point at it, unauthenticated, regardless of
which optional profiles are on).

**Optional profiles** (each is its own `values.yaml` top-level flag,
default `false` — `deploy.sh --full` turns all of them on):

| Profile | Adds |
|---|---|
| `objecten` | Objecten API + its celery worker (Objecttypen has no top-level flag of its own — `deploy.sh --full` enables it alongside `objecten` via `podiumd.objecttypen.enabled` directly) |
| `openarchiefbeheer` | Open Archiefbeheer (web + nginx + worker + beat) |
| `opennotificaties` | Open Notificaties + RabbitMQ |
| `openformulieren` | Open Formulieren (+ transitively needs `objecten`, `objecttypen`, `opennotificaties` enabled too — matches compose's own profile nesting) |
| `metrics` | otel-collector, Tempo, Prometheus, Grafana (or the `monitoringLogging` alternative below) |
| `wiremock` | extra WireMock mappings (SmartDocuments/KVK/BAG) |

`metrics` has two interchangeable implementations, picked by
`values.yaml`'s `monitoringLogging.enabled` — not a `deploy.sh` flag, see
below:

- **Default** (`monitoringLogging.enabled=false`, unchanged): the four raw
  components above, defined directly in `templates/metrics/`.
- **`monitoringLogging.enabled=true`**: supersedes those four with the
  optional `monitoring-logging` Helm dependency instead — the same PodiumD
  chart used in production, re-tuned here for a single-node minikube box
  (`SingleBinary` Loki instead of `Distributed`+MinIO, no AKS node
  selectors, anonymous Grafana admin instead of Keycloak OAuth, `standard`
  storage class). Adds Loki + Alloy (log aggregation/shipping) and
  kube-prometheus-stack + Prometheus Pushgateway on top of the same
  Tempo/Grafana/otel-collector functionality — meaningfully heavier
  (roughly a dozen extra pods). Still needs `metrics.enabled=true` too
  (independent flag, on by default with `--full`) — `monitoringLogging`
  only picks the implementation, it doesn't turn the profile on by itself.
  Set it persistently with `./scripts/set-podiumd-version.sh <version>
  <monitoring-logging-version>` (or edit `values.yaml` directly);
  `deploy.sh` then detects it automatically and repoints ZAC's OTLP
  endpoint at the new otel-collector for you — see that script's own
  `--help`-style comment. Never runs alongside the default implementation —
  enabling it turns the raw `templates/metrics/` resources off to avoid two
  Grafanas/Tempos/otel-collectors at once.

Ingress hostnames (all `*.local`, reachable once the tunnel + `/etc/hosts`
entry are set up): `zac`, `keycloak`, `openzaak`, `openklant`, `pabc`,
`solr`, `objecten`, `objecttypen`, `opennotificaties`,
`openarchiefbeheer-web`/`-ui`, `openformulieren-nginx`/`-web`, `grafana`,
`mailpit`.

## Resource usage

No `metrics-server` is installed in this cluster, so `kubectl top` isn't
available. Measure the same way this project's own live investigations
have: `docker stats minikube --no-stream` (real usage, docker driver only)
for the actual number, and `kubectl describe node minikube`'s own
"Allocated resources" section for what's been requested/limited at the
Kubernetes scheduling level (usually lower than real usage, since not
every container sets both).

Measured on a full `deploy.sh --full` (every optional profile) against an
idle-ish cluster (no active load beyond the apps' own background/health-
check traffic), on a 20Gi-capped minikube container, with and without
`monitoringLogging.enabled` for comparison:

| | `monitoringLogging.enabled=true` | `monitoringLogging.enabled=false` |
|---|---|---|
| CPU requests | 3465m / 8 (43%) | 3260m / 8 (40%) |
| CPU limits | 2650m / 8 (33%) | 200m / 8 (2%) |
| Memory requests | 10058Mi / 32Gi (31%) | 9416Mi / 32Gi (29%) |
| Memory limits | 8308Mi / 32Gi (25%) | 5652Mi / 32Gi (17%) |
| CPU, real (docker stats) | 64–86% (bursty) | 30–229% (bursty) |
| Memory, real (docker stats) | ~17.8Gi / 20Gi (**~89%**) | ~17.0Gi / 20Gi (**~85%**) |

**`monitoringLogging.enabled` is still the single biggest lever available**
to reduce this footprint, but the *real* saving is more modest than the
dozen-fewer-pods headline suggests — only ~4 percentage points / ~0.8Gi in
practice, measured the same way (settled ~5 minutes post-deploy) on both
sides. Most of what that flag adds (kube-state-metrics, prometheus-
operator, node-exporter, Pushgateway) is genuinely lightweight; the real
weight is Loki + Alloy + Grafana + kube-prometheus-stack's own Prometheus,
and even those are already re-tuned for a single-node box (see "What's
running" above). The *limits* column drops far more dramatically than real
usage does - limits are ceilings, not actual consumption, so don't read
that column as the expected savings.

If you're not specifically testing the monitoring-logging implementation,
disable it with:

```bash
./scripts/set-podiumd-version.sh <version> --disable-monitoring-logging
```

(or edit `values.yaml`'s `monitoringLogging.enabled` directly) and
redeploy — see "What's running" above for what that trades away
(Loki/Alloy's log-shipping pipeline has no raw-templates equivalent at
all). No single running process was found to be a leak or runaway in
either state — both real-usage figures above are the sum of ~35-40 normal
pods, not a bug. `deploy.sh` cleans up the *other* implementation's
leftover resources automatically either direction, including
Prometheus/PrometheusRule/ServiceMonitor/PodMonitor custom resources (see
`prune-orphaned-workloads.py`'s own header for why those needed separate
handling from plain Deployments/StatefulSets/DaemonSets).

## Scripts

| Script | What it does |
|---|---|
| `provision-cluster.sh` | Starts minikube (sized for the full stack), installs Traefik, pre-pulls/loads every image this chart references, runs `helm dependency update` |
| `deploy.sh` | Renders and applies the chart (`--full` for every optional profile). Whether the `metrics` profile is backed by the heavier loki/alloy/grafana/tempo/kube-prometheus-stack dependency or the lightweight raw templates isn't a flag here — it's read straight from `values.yaml`'s `monitoringLogging.enabled` (see "What's running" above), and this script applies the two things that implementation needs automatically (its CRDs first, ZAC's OTLP endpoint repointed). Afterward, deletes any Deployment/StatefulSet/DaemonSet left in the namespace from an earlier run with different profile flags that isn't part of the current render at all — plain `kubectl apply` never removes resources that drop out of a render, so switching profiles (or toggling `monitoringLogging.enabled`) would otherwise leave the old ones running alongside the new ones indefinitely |
| `setup-tunnel.sh` | Starts `minikube tunnel` so Traefik gets a real IP reachable from the host; idempotent, prints the `/etc/hosts` line either way |
| `teardown-cluster.sh` | Deletes the entire minikube cluster (asks for confirmation; `--yes` to skip) |
| `reset-namespace.sh` | Empties the `podiumd-minikube` namespace back to a clean slate without deleting the minikube cluster itself - every pod/Deployment/Service/PVC/Job in it, the six Retain-policy PersistentVolumes and their hostPath data storage-hooks.yaml creates, and monitoring-logging's own cluster-scoped RBAC/webhook objects (if that dependency was ever enabled). Wipes all seeded data, including PABC's migration data (asks for confirmation; `--yes` to skip) - run `deploy.sh` afterward to redeploy from scratch |
| `set-podiumd-version.sh <version> <monitoring-logging-version\|--disable-monitoring-logging>` | Swaps the `podiumd` Helm dependency to a different version (`helm search repo dimpact/podiumd -l` to list available ones), and the `monitoring-logging` dependency alongside it — the two are independently-versioned charts in the same monorepo with no formula relating them (see the script's own header for how to look up the exact co-released version by hand). The second argument is mandatory, on purpose — no implicit default: pass a monitoring-logging version to set it in `values.yaml`/enable it, or `--disable-monitoring-logging` to explicitly disable it (its Chart.yaml dependency entry is left as-is either way, so it's still fetched by `helm dependency update`, just not rendered/deployed if disabled). `set-podiumd-version.sh --path <dir>` points `podiumd` at a local chart checkout instead (e.g. for testing unreleased podiumd changes) via a `file://` dependency, and automatically points `monitoring-logging` at the sibling `monitoring-logging/` directory next to it too (same `--disable-monitoring-logging` override applies, still optional here since it's auto-detected instead of a version you'd otherwise have to specify) — re-check the four intentional image-tag pins in `values.yaml` afterward either way, per that script's own comment |
| `show-podiumd-version.sh` | Prints everything `set-podiumd-version.sh` can have set: `Chart.yaml`'s declared repository/version for both dependencies, `Chart.lock`'s last-resolved version (only informative on its own in `--path` mode, where `Chart.yaml`'s own version is just `"*"`), and `values.yaml`'s `monitoringLogging.enabled` flag — none of these alone shows the full picture |
| `apply-pabc-migrations.sh` | The **only** safe way to (re)create the `pabc-migrations` Job — it's not idempotent (clears PABC's database before reseeding), so this refuses to run against an already-seeded database unless `--force` is passed |
| `seed-fixtures.sh` | Loads demo/fixture data into objecten/objecttypen (or `openobject`, whichever shape is currently selected — see `set-podiumd-version.sh`) via `manage.py loaddata`, matching docker-compose's own `*-import` containers for these apps. Run manually after `deploy.sh` once the objecten profile is up — safe to re-run |
| `show-port-mappings.sh` | Prints how host traffic actually reaches each currently-deployed app: not a per-service host port like docker-compose (every hostname is multiplexed onto Traefik's single LoadBalancer port 80 by HTTP Host header instead), so this reads the live cluster's Ingress objects and shows each hostname's backend `Service:port` (resolved to a real port number) plus a one-line description of what kind of traffic it is. Also flags any hostname claimed by more than one Ingress object (a real leftover-resource smell, e.g. from toggling `monitoringLogging.enabled` without the manual cleanup pass) |
| `expose-postgres.sh [local-port]` | Port-forwards the single shared Postgres instance to `localhost:5432` (or a different local port, e.g. if you already run Postgres locally) for a GUI tool (pgAdmin, DBeaver, ...) — prints the `postgres`/`postgres` superuser credentials and every database name on that instance (every app's database lives on this one server, not one server per app). Idempotent — running it again while already forwarding just reprints the connection info instead of starting a duplicate |

`reset-namespace.sh` vs `teardown-cluster.sh`: use `reset-namespace.sh`
to wipe app data and redeploy clean (faster - keeps the cluster,
Traefik, and loaded images); reach for `teardown-cluster.sh` only when
the cluster/VM itself is broken or you want a genuinely fresh minikube
profile (slower to recover from - needs `provision-cluster.sh` again
afterward, not just `deploy.sh`).

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
they're only ever piped into or sourced by the scripts above:

| Script | What it does |
|---|---|
| `strip-image-digests.py` | Helm post-renderer piped into automatically by `deploy.sh`/`provision-cluster.sh` — strips `@sha256:...` suffixes so images resolve to the tag-only references pre-loaded into minikube (which has no outbound network access) |
| `disable-service-links.py` | Helm post-renderer piped into automatically by `deploy.sh` — sets `enableServiceLinks: false` on every workload pod spec, avoiding Kubernetes' auto-injected `<SERVICE_NAME>_PORT`-style env vars colliding with app-expected ones of the same name |
| `exclude-pabc-migration-job.py` | Helm post-renderer piped into automatically by `deploy.sh` — drops the `pabc-migrations` Job from the general manifest apply, since `apply-pabc-migrations.sh` is the only safe way to (re)create it |
| `detect-objecten-shape.sh` | Sourced automatically by `deploy.sh`/`provision-cluster.sh` — detects whether the currently-selected podiumd version still has `objecten`/`objecttypen` as two separate subcharts or has merged them into `openobject` (aliased to `objecten`), and emits the right `--set` flags for whichever shape is active, so switching podiumd versions via `set-podiumd-version.sh` (including `--path` to an unreleased checkout) keeps working either way |
| `monitoring-logging-enabled.sh` | Sourced by `deploy.sh` and `show-podiumd-version.sh` — reads `values.yaml`'s `monitoringLogging.enabled` the same way in both places, so they can't drift out of sync about where that state lives |

## Testing

```bash
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r tests/requirements.txt
playwright install chromium
cd tests
pytest
```

`.venv` only needs creating once; after that, just `source .venv/bin/activate`
before running `pytest` again. `playwright install chromium` also only
needs running once per venv (downloads a Chromium build for the
browser-based test).

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
