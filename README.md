# podiumd-minikube

A standalone Helm chart that reproduces the `dimpact-zaakafhandelcomponent`
docker-compose dev stack (ZAC + its ZGW dependencies: Open Zaak, Open
Klant, PABC, Objecten, Objecttypen, Open Notificaties, Open
Archiefbeheer, Open Formulieren) for local development on minikube.

## Prerequisites

- [minikube](https://minikube.sigs.k8s.io/), Docker, `kubectl`, `helm`
  (see `scripts/provision-cluster` for the Traefik chart version pin if
  your `helm` binary is old)
- 6 CPUs / 16Gi free for the minikube VM (default sizing) — see
  Troubleshooting if the cluster becomes sluggish
- Python 3 + `pip` if you want to run the test suite
- **Apple Silicon Mac with no Docker Desktop**: see [`mac.md`](mac.md) for
  the colima setup this project's tooling needs instead

## Quick start

```bash
./scripts/set-podiumd-version <version> --disable-monitoring-logging  # required — see below
                                     # (or --path <dir> for a local podiumd chart checkout)
./scripts/provision-cluster      # starts minikube, installs Traefik, pre-loads every image
./scripts/deploy --full          # renders and applies the chart (every optional profile on)
./scripts/setup-tunnel           # starts `minikube tunnel`, prints the /etc/hosts line to add
```

`scripts/set-podiumd-version` is required on a fresh clone — `Chart.yaml` holds
no real podiumd/monitoring-logging version, just a placeholder; the actual
version lives in `.podiumd-versions.yaml` (gitignored, created by this
script). `scripts/deploy`/`scripts/provision-cluster` both refuse with a clear
message if you haven't run it yet.

Then add the printed line to `/etc/hosts` and open `http://zac.local` in a
browser — it redirects to Keycloak, and back to the authenticated app on
login.

Leave off `--full` on `scripts/deploy` to deploy just the core profile (ZAC,
Open Zaak, Open Klant, PABC, Postgres/Redis/Solr/Keycloak/WireMock),
matching `values.yaml`'s own default.

## What's running

**Core (always on):** zac, openzaak, openklant, pabc, brp-personen-mock,
postgres, redis, solr, keycloak, wiremock, mailpit (SMTP test server —
every app's email settings point at it).

**Optional profiles** (each its own `values.yaml` flag, off by default —
`scripts/deploy --full` turns all of them on):

| Profile | Adds |
|---|---|
| `objecten` | Objecten API + celery worker (Objecttypen has no flag of its own — `--full` enables it alongside `objecten`) |
| `openarchiefbeheer` | Open Archiefbeheer (web + nginx + worker + beat) |
| `opennotificaties` | Open Notificaties + RabbitMQ |
| `openformulieren` | Open Formulieren (transitively needs `objecten`, `objecttypen`, `opennotificaties`) |
| `metrics` | otel-collector, Tempo, Prometheus, Grafana (or the `monitoringLogging` alternative below) |
| `wiremock` | extra WireMock mappings (SmartDocuments/KVK/BAG) |

`metrics` has two implementations, picked by `values.yaml`'s
`monitoringLogging.enabled` (not a `scripts/deploy` flag):

- **Default** (`false`): the four raw components above.
- **`true`**: swaps them for the `monitoring-logging` Helm dependency —
  the same chart used in production, re-tuned for a single-node box. Adds
  Loki + Alloy + kube-prometheus-stack + Pushgateway on top — meaningfully
  heavier (~a dozen extra pods). Still needs `metrics.enabled=true` too.
  Set it with `./scripts/set-podiumd-version <version>
  <monitoring-logging-version>`; `scripts/deploy` handles the rest (CRDs, ZAC's
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

Measured on a full `scripts/deploy --full`, idle-ish, 20Gi-capped container:

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
./scripts/set-podiumd-version <version> --disable-monitoring-logging
```

`scripts/deploy` cleans up the other implementation's leftover resources
automatically either direction.

## Scripts

| Script | What it does |
|---|---|
| `scripts/provision-cluster` | Starts minikube (sized for the full stack), installs Traefik, pre-pulls every image, runs `helm dependency update` |
| `scripts/deploy [--force-prune]` | Syncs `charts/*.tgz` against `.podiumd-versions.yaml`, renders and applies the chart (`--full` for every profile), prunes resources left over from a different profile set (`--force-prune` to confirm an unusually large prune), applies `pabc-migrations`, and seeds fixture data if `objecten` is enabled |
| `scripts/setup-tunnel` | Starts `minikube tunnel`; idempotent |
| `scripts/teardown-cluster` | Deletes the entire minikube cluster (asks for confirmation; `--yes` to skip) |
| `scripts/reset-namespace` | Empties the namespace without deleting the cluster — wipes all seeded data (asks for confirmation; `--yes` to skip) |
| `scripts/set-podiumd-version <version> <monitoring-logging-version\|--disable-monitoring-logging>` | Sets both Helm dependency versions in `.podiumd-versions.yaml` (never `Chart.yaml`) and fetches them. `--path <dir>` points `podiumd` at a local checkout instead, auto-detecting a sibling `monitoring-logging/` directory |
| `scripts/show-podiumd-version` | Prints the current version selection and fetch status per dependency |
| `scripts/show-port-mappings` | Prints how host traffic reaches each deployed app (Ingress hostname → backend Service:port) |
| `scripts/expose-postgres [local-port]` | Port-forwards the shared Postgres instance to localhost for a GUI tool |
| `scripts/flush-redis [--db N]` | Flushes the shared Redis instance (`FLUSHALL` by default) |
| `scripts/update-hosts` | Writes the `*.local` → Traefik IP line to `/etc/hosts` (uses sudo) |
| `scripts/deploy-extended` | `deploy --full` without metrics and WireMock mappings, with openzaak's and openklant's Celery workers |
| `scripts/apply-pabc-migrations [--force]` | The only safe way to (re)create the `pabc-migrations` Job — refuses against an already-seeded database unless `--force` |
| `scripts/seed-fixtures` | Loads the objecten/objecttypen demo data; skips apps already seeded (`deploy` runs it too) |

`scripts/reset-namespace` vs `scripts/teardown-cluster`: use `scripts/reset-namespace` to
wipe app data and redeploy clean (keeps the cluster/Traefik/images);
`scripts/teardown-cluster` only when the cluster itself is broken.

The scripts are Python (every one takes `--help`); their shared code is the
`scripts/lib/` package, for example `manifests.py` (the fixups applied to
`helm template` output), `dependency.py` (`.podiumd-versions.yaml` and
`charts/*.tgz`) and `kube.py` (kubectl and the minikube-context guard).

## Testing

```bash
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r requirements.txt
playwright install chromium
cd tests
pytest
```

Live-cluster integration tests, not unit tests — see
[`tests/README.md`](tests/README.md) for full coverage and caveats. Tests
for profiles that aren't currently deployed auto-skip.

The scripts have offline unit tests in `scripts/tests/`. Before committing,
run every check on all Python code (ruff, shellcheck, jscpd, pymarkdown,
vulture, bandit, basedpyright, pylint, and those unit tests):

```bash
./run_python_checks
```

## Why not `helm install`?

Helm's own release record embeds the entire resolved chart — including
the ~3.87MB `podiumd` dependency — which exceeds Kubernetes' 3MB API
request-size limit. This project uses `helm template | kubectl apply`
instead, which means Helm's install/upgrade hooks never fire —
`scripts/deploy` handles the one place that matters
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

```text
Chart.yaml, values.yaml, templates/   # the chart itself (this repo IS the chart, no nested wrapper)
vendor/dimpact-zaakafhandelcomponent/  # physical copies of file assets from that repo (see vendor/NOTES.md)
scripts/                               # cluster lifecycle + deploy-time tooling (see table above)
scripts/lib/                           # shared Python package for the scripts
scripts/tests/                         # offline unit tests for scripts/
tests/                                 # live-cluster pytest suite
run_python_checks, pyproject.toml      # lint/type/test checks for all Python code
```
