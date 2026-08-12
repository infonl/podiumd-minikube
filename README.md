# podiumd-minikube

A standalone Helm chart that reproduces the `dimpact-zaakafhandelcomponent`
docker-compose dev stack (ZAC + its ZGW dependencies: Open Zaak, Open
Klant, PABC, Objecten, Objecttypen, Open Notificaties, Open
Archiefbeheer, Open Formulieren) for local development on minikube.

## Prerequisites

- [minikube](https://minikube.sigs.k8s.io/), Docker, `kubectl`, `helm`
  (see `scripts/provision-cluster.sh` for the Traefik chart version pin if
  your `helm` binary is old)
- 6 CPUs / 16Gi free for the minikube VM (default sizing) — see
  Troubleshooting if the cluster becomes sluggish
- Python 3 + `pip` if you want to run the test suite
- **Apple Silicon Mac with no Docker Desktop**: see [`mac.md`](mac.md) for
  the colima setup this project's tooling needs instead

## Quick start

```bash
./scripts/set-podiumd-version.sh <version> --disable-monitoring-logging  # required — see below
                                     # (or --path <dir> for a local podiumd chart checkout)
./scripts/provision-cluster.sh      # starts minikube, installs Traefik, pre-loads every image
./scripts/deploy.sh --full          # renders and applies the chart (every optional profile on)
./scripts/setup-tunnel.sh           # starts `minikube tunnel`, prints the /etc/hosts line to add
```

`set-podiumd-version.sh` is required on a fresh clone — `Chart.yaml` holds
no real podiumd/monitoring-logging version, just a placeholder; the actual
version lives in `.podiumd-versions.yaml` (gitignored, created by this
script). `deploy.sh`/`provision-cluster.sh` both refuse with a clear
message if you haven't run it yet.

Then add the printed line to `/etc/hosts` and open `http://zac.local` in a
browser — it redirects to Keycloak, and back to the authenticated app on
login.

Leave off `--full` on `deploy.sh` to deploy just the core profile (ZAC,
Open Zaak, Open Klant, PABC, Postgres/Redis/Solr/Keycloak/WireMock),
matching `values.yaml`'s own default.

## What's running

**Core (always on):** zac, openzaak, openklant, pabc, brp-personen-mock,
postgres, redis, solr, keycloak, wiremock, mailpit (SMTP test server —
every app's email settings point at it).

**Optional profiles** (each its own `values.yaml` flag, off by default —
`deploy.sh --full` turns all of them on):

| Profile | Adds |
|---|---|
| `objecten` | Objecten API + celery worker (Objecttypen has no flag of its own — `--full` enables it alongside `objecten`) |
| `openarchiefbeheer` | Open Archiefbeheer (web + nginx + worker + beat) |
| `opennotificaties` | Open Notificaties + RabbitMQ |
| `openformulieren` | Open Formulieren (transitively needs `objecten`, `objecttypen`, `opennotificaties`) |
| `metrics` | otel-collector, Tempo, Prometheus, Grafana (or the `monitoringLogging` alternative below) |
| `wiremock` | extra WireMock mappings (SmartDocuments/KVK/BAG) |

`metrics` has two implementations, picked by `values.yaml`'s
`monitoringLogging.enabled` (not a `deploy.sh` flag):

- **Default** (`false`): the four raw components above.
- **`true`**: swaps them for the `monitoring-logging` Helm dependency —
  the same chart used in production, re-tuned for a single-node box. Adds
  Loki + Alloy + kube-prometheus-stack + Pushgateway on top — meaningfully
  heavier (~a dozen extra pods). Still needs `metrics.enabled=true` too.
  Set it with `./scripts/set-podiumd-version.sh <version>
  <monitoring-logging-version>`; `deploy.sh` handles the rest (CRDs, ZAC's
  OTLP endpoint) automatically. Never runs alongside the default
  implementation.

Ingress hostnames (all `*.local`, once the tunnel + `/etc/hosts` entry are
set up): `zac`, `keycloak`, `openzaak`, `openklant`, `pabc`, `solr`,
`objecten`, `objecttypen`, `opennotificaties`,
`openarchiefbeheer-web`/`-ui`, `openformulieren-nginx`/`-web`, `grafana`,
`mailpit`.

## Resource usage

No `metrics-server` is installed, so `kubectl top` isn't available — use
`docker stats minikube --no-stream` (real usage) and `kubectl describe
node minikube` (requested/limited).

Measured on a full `deploy.sh --full`, idle-ish, 20Gi-capped container:

| | `monitoringLogging.enabled=true` | `=false` |
|---|---|---|
| CPU requests | 3465m / 8 (43%) | 3260m / 8 (40%) |
| CPU limits | 2650m / 8 (33%) | 200m / 8 (2%) |
| Memory requests | 10058Mi / 32Gi (31%) | 9416Mi / 32Gi (29%) |
| Memory limits | 8308Mi / 32Gi (25%) | 5652Mi / 32Gi (17%) |
| Memory, real | ~17.8Gi / 20Gi (**~89%**) | ~17.0Gi / 20Gi (**~85%**) |

`monitoringLogging.enabled` is the biggest lever to reduce footprint, but
the real saving is modest (~4pp / ~0.8Gi) compared to the *limits* column,
which are ceilings, not actual consumption. Disable it with:

```bash
./scripts/set-podiumd-version.sh <version> --disable-monitoring-logging
```

`deploy.sh` cleans up the other implementation's leftover resources
automatically either direction.

## Scripts

| Script | What it does |
|---|---|
| `provision-cluster.sh` | Starts minikube (sized for the full stack), installs Traefik, pre-pulls every image, runs `helm dependency update` |
| `deploy.sh [--force-prune]` | Syncs `charts/*.tgz` against `.podiumd-versions.yaml`, renders and applies the chart (`--full` for every profile), prunes resources left over from a different profile set (`--force-prune` to confirm an unusually large prune), applies `pabc-migrations`, and seeds fixture data if `objecten` is enabled |
| `setup-tunnel.sh` | Starts `minikube tunnel`; idempotent |
| `teardown-cluster.sh` | Deletes the entire minikube cluster (asks for confirmation; `--yes` to skip) |
| `reset-namespace.sh` | Empties the namespace without deleting the cluster — wipes all seeded data (asks for confirmation; `--yes` to skip) |
| `set-podiumd-version.sh <version> <monitoring-logging-version\|--disable-monitoring-logging>` | Sets both Helm dependency versions in `.podiumd-versions.yaml` (never `Chart.yaml`) and fetches them. `--path <dir>` points `podiumd` at a local checkout instead, auto-detecting a sibling `monitoring-logging/` directory |
| `show-podiumd-version.sh` | Prints the current version selection and fetch status per dependency |
| `show-port-mappings.sh` | Prints how host traffic reaches each deployed app (Ingress hostname → backend Service:port) |
| `expose-postgres.sh [local-port]` | Port-forwards the shared Postgres instance to localhost for a GUI tool |
| `flush-redis.sh [--db N]` | Flushes the shared Redis instance (`FLUSHALL` by default) |

`reset-namespace.sh` vs `teardown-cluster.sh`: use `reset-namespace.sh` to
wipe app data and redeploy clean (keeps the cluster/Traefik/images);
`teardown-cluster.sh` only when the cluster itself is broken.

`scripts/lib/` holds internal helpers plus two scripts also safe to run
by hand directly:

| Script | What it does |
|---|---|
| `apply-pabc-migrations.sh` | The only safe way to (re)create the `pabc-migrations` Job — refuses against an already-seeded database unless `--force` |
| `seed-fixtures.sh` | Loads demo fixture data into objecten/objecttypen — idempotent, skips apps already seeded |
| `podiumd-dependency.sh` | Reads/writes `.podiumd-versions.yaml`, syncs `charts/*.tgz` |
| `detect-objecten-shape.sh`, `monitoring-logging-enabled.sh` | Sourced helpers for shape detection / reading `monitoringLogging.enabled` |
| `strip-image-digests.py`, `disable-service-links.py`, `exclude-pabc-migration-job.py` | Helm post-renderers piped in by `deploy.sh` |

## Testing

```bash
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r tests/requirements.txt
playwright install chromium
cd tests
pytest
```

Live-cluster integration tests, not unit tests — see
[`tests/README.md`](tests/README.md) for full coverage and caveats. Tests
for profiles that aren't currently deployed auto-skip.

## Why not `helm install`?

Helm's own release record embeds the entire resolved chart — including
the ~3.87MB `podiumd` dependency — which exceeds Kubernetes' 3MB API
request-size limit. This project uses `helm template | kubectl apply`
instead, which means Helm's install/upgrade hooks never fire —
`deploy.sh` handles the one place that matters
(`templates/storage-hooks.yaml`'s PV/PVC pre-provisioning) as a separate
step instead.

## Troubleshooting

**Cluster becomes sluggish or unresponsive, especially after switching
profile combinations a few times.** Usually an under-provisioned minikube
VM thrashing under memory pressure — check with `docker stats minikube`
or `minikube ssh -- free -h`. Raise it live without restarting:

```bash
docker update --memory=16g --memory-swap=-1 minikube
```

This doesn't persist across `minikube delete`.

## Project structure

```
Chart.yaml, values.yaml, templates/   # the chart itself (this repo IS the chart, no nested wrapper)
vendor/dimpact-zaakafhandelcomponent/  # physical copies of file assets from that repo (see vendor/NOTES.md)
scripts/                               # cluster lifecycle + deploy-time tooling (see table above)
scripts/lib/                           # mostly internal helpers, plus two exceptions (see table above)
tests/                                 # live-cluster pytest suite
```
