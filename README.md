# podiumd-minikube

A standalone Helm chart that reproduces the `dimpact-zaakafhandelcomponent`
docker-compose dev stack (ZAC + its ZGW dependencies: Open Zaak, Open
Klant, PABC, Objecten, Objecttypen, Open Notificaties, Open
Archiefbeheer, Open Formulieren) for local development on minikube.

## Prerequisites

- [minikube](https://minikube.sigs.k8s.io/), Docker, `kubectl`, `helm`
  (3.8 or newer, for the NGINX Gateway Fabric OCI chart)
- 6 CPUs / 16Gi free for the minikube VM (default sizing) — see
  Troubleshooting if the cluster becomes sluggish
- Python 3.12 or newer, with this project installed in `.venv`:

  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -e '.[dev]'
  source .venv/bin/activate   # puts podiumd-minikube on PATH
  ```

  `bin/<command>` runs `podiumd-minikube <command>` from `.venv` without
  activating it.
- **Apple Silicon Mac with no Docker Desktop**: see [`mac.md`](mac.md) for
  the colima setup this project's tooling needs instead

## Quick start

```bash
podiumd-minikube set-podiumd-version <version> <monitoring-logging-version>  # required — see below
                                     # (or --path <dir> for a local podiumd chart checkout)
podiumd-minikube provision-cluster      # starts minikube, installs NGINX Gateway Fabric, pre-loads every image
podiumd-minikube deploy --full          # renders and applies the chart (every optional profile on)
podiumd-minikube setup-tunnel           # asks sudo for the route, then runs `minikube tunnel` in the background
```

After a reboot or `minikube stop`, `podiumd-minikube start-cluster` brings everything
back: it starts minikube, waits for the apps, restarts ZAC if its boot failed
because Open Zaak was not up yet, and starts the tunnel.

`podiumd-minikube set-podiumd-version` is required on a fresh clone — `Chart.yaml` holds
no real podiumd/monitoring-logging version, just a placeholder; the actual
version lives in `.podiumd-versions.yaml` (gitignored, created by this
script). `podiumd-minikube deploy`/`podiumd-minikube provision-cluster` both refuse with a clear
message if you haven't run it yet.

Then run `podiumd-minikube update-hosts` (adds the `*.local` names to `/etc/hosts`)
and open `https://zac.local` in a browser — it redirects to Keycloak, and back
to the authenticated app on login.

### Edge

As ExternalsPodiumD: NGINX Gateway Fabric with Gateway `public-gateway` in
namespace `ingress-basic`; `deploy` turns every rendered Ingress into an
HTTPRoute (`src/podiumd_minikube/gateway.py`). Two consequences of its nginx, both as
there: request bodies above 1 MB get 413, and request bodies are buffered.
One deviation: larger response-header buffers, for KISS's login cookie
(`.claude/plans/plan.md`). The edge Service has the fixed ClusterIP
`10.96.0.200`, which `minikube tunnel` exposes, so the `/etc/hosts` line
stays valid across rebuilds.

### HTTPS

Every host serves HTTPS (and still plain HTTP), as in the real PodiumD
environments, with a certificate from a local CA instead of Let's Encrypt:

- `provision-cluster` creates the CA once per checkout in `.pki/` (gitignored)
  and installs cert-manager with a ClusterIssuer for it; `deploy` issues one
  certificate for all ingress hosts and serves it from the edge.
- To trust it, import `.pki/ca.crt` in your browser, or pass it to clients:
  `curl --cacert .pki/ca.crt https://openzaak.local/`.
- Inside the cluster the `*.local` hosts resolve to the edge and every pod
  trusts the CA, so the apps call each other on `https://<app>.local`.

Leave off `--full` on `podiumd-minikube deploy` to deploy just the core profile (ZAC,
Open Zaak, Open Klant, PABC, Postgres/Redis/Solr/Keycloak/WireMock),
matching `values.yaml`'s own default.

## What's running

**Core (always on):** zac, openzaak, openklant, pabc, brp-personen-mock,
postgres, redis, solr, keycloak, mailpit (SMTP test server — every app's
email settings point at it), and the api-proxy with WireMock: apps call
BRP, KvK and BAG at `http://api-proxy/...` as in ExternalsPodiumD.
`https://api-proxy.local` serves the same paths to tests:

| Path | Answered by |
|---|---|
| `/haalcentraal/api/brp/` | brp-personen-mock |
| `/api/v2/zoeken`, `/api/v1/basisprofielen`, `/api/v1/vestigingsprofielen` | KvK's test API (`https://api.kvk.nl/test/...`, needs internet), as both reference projects |
| `/lvbag/individuelebevragingen/v2/` | WireMock (BAG mappings) |

**Optional profiles** (each its own `values.yaml` flag, off by default —
`podiumd-minikube deploy --full` turns all of them on):

| Profile | Adds |
|---|---|
| `objecten` | Objecten API + celery worker (Objecttypen has no flag of its own — `--full` enables it alongside `objecten`) |
| `openarchiefbeheer` | Open Archiefbeheer (web + nginx + worker + beat) |
| `opennotificaties` | Open Notificaties + RabbitMQ |
| `openformulieren` | Open Formulieren (transitively needs `objecten`, `objecttypen`, `opennotificaties`) |
| `openinwoner` | Open Inwoner, with Elasticsearch through the ECK operator (as ExternalsPodiumD and podiumd-infra) |
| `ita` | Interne Taakafhandeling on `https://ita.local` (needs `objecten` and the KISS objecttypes) |
| `kiss` | KISS (chart name `contact`) on `https://contact.local`, its Elasticsearch, Kibana and podiumd-adapter (needs `objecten`) |
| `omc` | OMC 1.17.19 rejects real notifications (see `plan.md`; podiumd-tests marks it as an expected failure). OMC on `https://omc.local`; `podiumd.omc.settings.notify.api.baseUrl` takes a Notify mock (needs `objecten`, `opennotificaties`) |
| `referentielijsten` | Referentielijsten on `https://referentielijsten.local` |
| `openbeheer` | Open Beheer on `https://openbeheer.local` (needs `objecten`) |
| `frankgateway` | Frank!Gateway's outway (with etcd) and OpenBao on `https://openbao.local`; apps then call BRP/KvK/BAG at `http://frankgateway-outway:9080/...` instead of the api-proxy. OpenBao's seal key, root token and recovery key live in `.openbao/` (gitignored) |
| `clamav` | ClamAV's clamd on `clamav:3310` with only the EICAR test signature (no freshclam, no internet); Open Inwoner's virus scan setting is test wiring (podiumd-tests) |
| `metrics` | monitoring-logging, as both reference environments: Grafana on `https://grafana.local`, Prometheus (scrapes ZAC), Loki with Alloy, the otel-collector |

Ingress hostnames (all `*.local`, once the tunnel + `/etc/hosts` entry are
set up): `zac`, `keycloak`, `openzaak`, `openklant`, `pabc`, `solr`,
`objecten`, `objecttypen`, `opennotificaties`,
`openarchiefbeheer-web`/`-ui`, `openformulieren-nginx`/`-web`, `grafana`,
`mailpit`.

## Resource usage

`podiumd-minikube show-cluster-status` shows the node's and each container's memory
and CPU against the budget and the baseline, and whether the cluster has
settled (no `metrics-server`, so no `kubectl top`).

Budgets (`tests/test_memory.py`): the default profile at most 16 GiB for the
minikube node, `--full` at most 20 GiB. Measured with `--full` after a full
podiumd-tests run (2026-10-08): node 12.3 GiB, containers 11.2 GiB.

`tests/memory-baseline.json` holds that measurement per container; the test
fails when a container grows more than 20% + 64 MiB above it. Refresh it
after an intended change, on a settled cluster and after a full test run:

```bash
pytest tests/test_memory.py --update-memory-baseline
```

The node gets 6 CPUs (`MINIKUBE_CPUS`), pinned to the host's first CPUs
(`--cpuset-cpus=0-5`) so that programs sizing themselves by the CPU count
see 6 instead of all of the host's. On Intel and AMD hybrid CPUs the fast
cores come first.

What keeps it small (heaps, worker counts, GC settings, `MALLOC_ARENA_MAX`,
`GOMEMLIMIT`, the control plane's `GOGC`) and how to check each setting:
[`.claude/memory/memory-tuning.md`](.claude/memory/memory-tuning.md).

## Commands

| Command | What it does |
|---|---|
| `podiumd-minikube provision-cluster` | Starts minikube (sized for the full stack), installs NGINX Gateway Fabric, pre-pulls every image, runs `helm dependency update` |
| `podiumd-minikube deploy [--force-prune]` | Syncs `charts/*.tgz` against `.podiumd-versions.yaml`, renders and applies the chart (`--full` for every profile), prunes resources left over from a different profile set (`--force-prune` to confirm an unusually large prune), and seeds fixture data if `objecten` is enabled |
| `podiumd-minikube show-cluster-status` | Node and per-container memory and CPU (against the budget and `tests/memory-baseline.json`), profiles, recent restarts and failed Jobs, tunnel, lock and disk; reads only (`--top N`, `--all`) |
| `podiumd-minikube start-cluster` | After a reboot: starts minikube, waits until every pod reports its state since the start and the apps are ready, restarts ZAC once if its boot failed (Open Zaak was down), then runs `setup-tunnel` |
| `podiumd-minikube cluster-lock` | Shows, takes, releases or breaks the lock on the shared cluster; `run` holds it around a command (see "Shared cluster: the lock") |
| `podiumd-minikube setup-tunnel` | Adds the route to minikube's service network (sudo, in the foreground) and runs `minikube tunnel` in the background; idempotent. `setup-tunnel stop` stops it and removes the route |
| `podiumd-minikube teardown-cluster` | Deletes the entire minikube cluster (asks for confirmation; `--yes` to skip) |
| `podiumd-minikube reset-namespace` | Empties the namespace without deleting the cluster — wipes all seeded data (asks for confirmation; `--yes` to skip) |
| `podiumd-minikube set-podiumd-version <version> <monitoring-logging-version>` | Sets both Helm dependency versions in `.podiumd-versions.yaml` (never `Chart.yaml`) and fetches them. `--path <dir>` points `podiumd` at a local checkout instead, auto-detecting a sibling `monitoring-logging/` directory |
| `podiumd-minikube show-podiumd-version` | Prints the current version selection and fetch status per dependency |
| `podiumd-minikube show-port-mappings` | Prints how host traffic reaches each deployed app (Ingress hostname → backend Service:port) |
| `podiumd-minikube expose-postgres [local-port]` | Port-forwards the shared Postgres instance to localhost for a GUI tool |
| `podiumd-minikube flush-redis [--db N]` | Flushes the shared Redis instance (`FLUSHALL` by default) |
| `podiumd-minikube update-hosts` | Writes the `*.local` → edge IP line to `/etc/hosts` (uses sudo) |
| `podiumd-minikube deploy-extended` | `deploy --full` without metrics, with openzaak's and openklant's Celery workers |

`podiumd-minikube reset-namespace` vs `podiumd-minikube teardown-cluster`: use `podiumd-minikube reset-namespace` to
wipe app data and redeploy clean (keeps the cluster, the edge and the images);
`podiumd-minikube teardown-cluster` only when the cluster itself is broken.

`podiumd-minikube --help` lists the commands, `podiumd-minikube <command>
--help` explains one. They are defined in `src/podiumd_minikube/cli.py`; the
logic is in the rest of the package, for example `manifests.py` (the fixups applied to
`helm template` output), `dependency.py` (`.podiumd-versions.yaml` and
`charts/*.tgz`) and `kube.py` (kubectl and the minikube-context guard).

## Shared cluster: the lock

Several people and agents (podiumd-tests' CLI among them) change the same
cluster, so whoever changes it holds the lock file first
(`../.minikube-lock`, one line: who, what, start time; `MINIKUBE_LOCK_FILE`
overrides the path). `deploy`, `deploy-extended`, `reset-namespace`,
`teardown-cluster`, `start-cluster`, `provision-cluster`,
`flush-redis` and `setup-tunnel stop` take it
themselves and refuse while someone else holds it. Reading (kubectl get,
logs, the test suite) needs no lock.

```bash
podiumd-minikube cluster-lock status                         # who holds it, since when
podiumd-minikube cluster-lock take "kubectl cleanup in openzaak"   # around manual changes
podiumd-minikube cluster-lock release
podiumd-minikube cluster-lock run "rebuild" -- sh -c 'podiumd-minikube teardown-cluster -y && podiumd-minikube provision-cluster'
podiumd-minikube cluster-lock break                          # only when its holder is gone
```

## Testing

```bash
cd tests
pytest
```

These live tests check that this project's code did its job on the cluster
(fixups, templates, commands, `values.yaml` wiring); see
[`tests/README.md`](tests/README.md). How the applications behave is tested
by podiumd-tests; after a deploy, `podiumd-tests run --env minikube --tier
smoke` checks that they answer (read-only, no lock).

The commands have offline unit tests in `unit_tests/`. Before committing,
run every check on all Python code (ruff, shellcheck, jscpd, pymarkdown,
vulture, bandit, basedpyright, pylint, and those unit tests); it upgrades
`.venv` first, at most once a day. GitHub Actions runs the same checks
(`.github/workflows/checks.yml`):

```bash
./run_python_checks
```

## Why not `helm install`?

Helm's own release record embeds the entire resolved chart — including
the ~3.87MB `podiumd` dependency — which exceeds Kubernetes' 3MB API
request-size limit. This project uses `helm template | kubectl apply`
instead, which means Helm's install/upgrade hooks never fire —
`podiumd-minikube deploy` handles the one place that matters
(`templates/storage-hooks.yaml`'s PV/PVC pre-provisioning) as a separate
step instead.

## Troubleshooting

**Cluster becomes sluggish or unresponsive, especially after switching
profile combinations a few times.** Usually an under-provisioned minikube
VM thrashing under memory pressure — check with `docker stats minikube`
or `minikube ssh -- free -h`. `provision-cluster` sizes the node at half the
memory Docker sees (override with `MINIKUBE_MEMORY` in MB); `deploy --full` and
`provision-cluster` warn when the running node is below the 20 GiB a
`deploy --full` needs, and print the command to raise it live without
restarting:

```bash
docker update --memory=<GiB>g --memory-swap=-1 minikube
```

This doesn't persist across `minikube delete`.

## Project structure

```text
Chart.yaml, values.yaml, templates/   # the chart itself (this repo IS the chart, no nested wrapper)
vendor/dimpact-zaakafhandelcomponent/  # physical copies of file assets from that repo (see vendor/NOTES.md)
src/podiumd_minikube/                  # the podiumd-minikube command (see table above)
bin/                                   # bin/<command> runs podiumd-minikube <command>
unit_tests/                            # offline unit tests for src/
tests/                                 # live-cluster pytest suite
run_python_checks, pyproject.toml      # package, dependencies and checks for all Python code
```
