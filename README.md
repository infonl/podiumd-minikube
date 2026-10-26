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
  git config core.hooksPath .githooks   # every commit runs ./run_python_checks first
  ```

  `bin/<command>` runs `podiumd-minikube <command>` from `.venv` without
  activating it.
- **Apple Silicon Mac with no Docker Desktop**: see [`mac.md`](mac.md) for
  the colima setup this project's tooling needs instead

## Quick start

```bash
podiumd-minikube set-podiumd-version <podiumd> <monitoring-logging>  # required — see below
                                     # (each a published version or a local chart checkout)
podiumd-minikube provision-cluster      # starts minikube with Cilium, installs NGINX Gateway Fabric, pre-loads every image
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
HTTPRoute (`src/podiumd_minikube/gateway.py`), with ExternalsPodiumD's live
proxy and client settings policies (no request-body limit, 128k buffers).
The edge Service has the fixed ClusterIP `10.96.0.200`, which `minikube
tunnel` exposes, so the `/etc/hosts` line stays valid across rebuilds.

### Network

As ExternalsPodiumD's ontw and most test clusters: Cilium 1.19 is the CNI and
replaces kube-proxy, NetworkPolicies are enforced, Hubble and the L7 proxy
are off (`src/podiumd_minikube/cni.py`). The only policy is the Keycloak
operator's. A cluster provisioned before Cilium still runs kindnet;
`start-cluster` and `provision-cluster` refuse it: run `teardown-cluster`,
then `provision-cluster` (this deletes the volumes).

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
Open Zaak, Open Klant, PABC, Postgres/Redis/Solr/Keycloak, the BAG mock),
matching `values.yaml`'s own default.

## What's running

**Core (always on):** zac, openzaak, openklant, pabc, brp-personen-mock,
postgres, Keycloak, Redis and Solr (podiumd's keycloak-operator with realm
`podiumd`, redis-operator and ZAC's SolrCloud, as both reference environments), mailpit (SMTP test server — every app's
email settings point at it), and the api-proxy with a BAG mock: apps call
BRP, KvK and BAG at `http://api-proxy/...` as in ExternalsPodiumD.
`https://api-proxy.local` serves the same paths to tests:

| Path | Answered by |
|---|---|
| `/haalcentraal/api/brp/` | brp-personen-mock |
| `/api/v2/zoeken`, `/api/v1/basisprofielen`, `/api/v1/vestigingsprofielen` | KvK's test API (`https://api.kvk.nl/test/...`, needs internet), as both reference projects |
| `/lvbag/individuelebevragingen/v2/` | `templates/bag-mock`: nginx serving the vendored answers for one address and one search |

**Optional profiles** (each its own `values.yaml` flag, off by default —
`podiumd-minikube deploy --full` turns all of them on):

| Profile | Adds |
|---|---|
| `objecten` | Objecten API + celery worker. Objecttypen has no flag of its own: on podiumd 4.9.* `--full` enables it alongside `objecten`; from 4.10 Open Object serves both APIs, and `objecttypen.local` is an alias of it (detected from the chart) |
| `openarchiefbeheer` | Open Archiefbeheer (web + nginx + worker + beat) |
| `opennotificaties` | Open Notificaties |
| `openformulieren` | Open Formulieren (transitively needs `objecten`, `objecttypen`, `opennotificaties`) |
| `openinwoner` | Open Inwoner, with Elasticsearch through the ECK operator (as ExternalsPodiumD and podiumd-infra) |
| `ita` | Interne Taakafhandeling on `https://ita.local` (needs `objecten` and the KISS objecttypes) |
| `kiss` | KISS (chart name `contact`) on `https://contact.local`, its Elasticsearch, Kibana and podiumd-adapter (needs `objecten`) |
| `omc` | OMC 1.17.19 rejects real notifications (see `plan.md`; podiumd-tests marks it as an expected failure). OMC on `https://omc.local`; `podiumd.omc.settings.notify.api.baseUrl` takes a Notify mock (needs `objecten`, `opennotificaties`) |
| `referentielijsten` | Referentielijsten on `https://referentielijsten.local` |
| `openbeheer` | Open Beheer on `https://openbeheer.local` (needs `objecten`) |
| `openvtb` | Open VTB on `https://openvtb.local`; with `omc`, a Job creates OMC's API token there |
| `zaakbrug` | ZaakBrug (ZDS to ZGW) on `https://zaakbrug.local`: Frank!Framework console with Keycloak login and the SOAP endpoints, on Open Zaak (client `zaakbrug`) |
| `zgw-office-addin` | ZGW Office add-in on `https://office-addin.local`, with placeholder MSAL ids: the manifest is served, Office sign-in fails (no Entra app); its backend calls Open Zaak as client `office-add-in` |
| `frankgateway` | Frank!Gateway's outway (with etcd) and OpenBao on `https://openbao.local`; apps then call BRP/KvK/BAG at `http://frankgateway-outway:9080/...` instead of the api-proxy. OpenBao's seal key, root token and recovery key live in `.openbao/` (gitignored) |
| `clamav` | ClamAV's clamd on `clamav:3310` with only the EICAR test signature (no freshclam, no internet); Open Inwoner's virus scan setting is test wiring (podiumd-tests) |
| `metrics` | monitoring-logging, as both reference environments: Grafana on `https://grafana.local`, Prometheus (scrapes ZAC, and the ClamAV, Redis and ECK operator exporters), Loki with Alloy, the otel-collector |

Ingress hostnames (all `*.local`, once the tunnel + `/etc/hosts` entry are
set up): `zac`, `keycloak`, `openzaak`, `openklant`, `pabc`, `solr`,
`objecten`, `objecttypen`, `opennotificaties`,
`openarchiefbeheer-web`/`-ui`, `openformulieren-nginx`/`-web`, `grafana`,
`mailpit`.

## Resource usage

`podiumd-minikube show-cluster-status` shows the node's and each container's memory
and CPU against the budget and the baseline, and whether the cluster has
settled (no `metrics-server`, so no `kubectl top`). It also shows what the last
deploy applied (its command and the podiumd and monitoring-logging selections,
from ConfigMap `podiumd-minikube-deployed`) and the running containers per chart.

Budgets (`tests/test_memory.py`): the default profile at most 9 GiB for the
minikube node (measured 7.0 GiB, 2026-10-14), `--full` at most 17 GiB. Measured with `--full` after a full
podiumd-tests run (2026-10-20, with Open VTB, ZaakBrug, the Office add-in and
the exporters): node 16.0 GiB, containers 13.9 GiB, of which ZaakBrug 0.8 GiB
and monitoring-logging (profile `metrics`) about 1.2 GiB.

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
| `podiumd-minikube provision-cluster` | Starts minikube (sized for the full stack) with Cilium, installs NGINX Gateway Fabric, pre-pulls every image (only those the host lacks), runs `helm dependency update`; removes the host's older tags of the chart's image repositories |
| `podiumd-minikube deploy [--full] [--otel] [--skip-roles] [--force-prune]` | Syncs `charts/*.tgz` against `.podiumd-versions.yaml`, renders and applies the chart (`--full` for every profile), prunes resources left over from a different profile set (`--force-prune` to confirm an unusually large prune). The realm import creates the realm and client roles and the groups (`keycloak.config.skipRoles/skipGroups: false`, as the chart docs advise for O/T); `--skip-roles` uses the chart default true instead, as long-lived realms do; a fresh realm then lacks the roles and podiumd-tests' bootstrap fails. `--otel`: the Maykin apps and ZAC send OpenTelemetry metrics to the metrics profile's collector, as ExternalsPodiumD dim1/info/mayk (off by default: about 50 MiB per Maykin web pod, and `--full` is near its 17 GiB budget). Warns about fixed-name Jobs ExternalsPodiumD does not delete before `helm upgrade` |
| `podiumd-minikube show-cluster-status` | Node and per-container memory and CPU (against the budget and `tests/memory-baseline.json`), the last deploy, running containers per chart, profiles, recent restarts and failed Jobs, tunnel, lock and disk; reads only (`--top N`, `--all`) |
| `podiumd-minikube start-cluster` | After a reboot: starts minikube, waits until every pod reports its state since the start and the apps are ready, restarts ZAC once if its boot failed (Open Zaak was down), then runs `setup-tunnel` |
| `podiumd-minikube cluster-lock` | Shows, takes, releases or breaks the lock on the shared cluster; `run` holds it around a command (see "Shared cluster: the lock") |
| `podiumd-minikube setup-tunnel` | Adds the route to minikube's service network (sudo, in the foreground) and runs `minikube tunnel` in the background; idempotent. `setup-tunnel stop` stops it and removes the route |
| `podiumd-minikube teardown-cluster` | Deletes the entire minikube cluster and the host's older tags of the chart's image repositories (asks for confirmation; `--yes` to skip) |
| `podiumd-minikube reset-namespace` | Empties the namespace without deleting the cluster — wipes all seeded data (asks for confirmation; `--yes` to skip) |
| `podiumd-minikube set-podiumd-version <podiumd> [<monitoring-logging>]` | Selects each Helm dependency in `.podiumd-versions.yaml` (never `Chart.yaml`) and fetches them. Each argument is a published version or a local chart checkout directory, so one can be a release and the other a branch. Without `<monitoring-logging>`: a podiumd checkout's sibling `monitoring-logging/`, else the recorded selection |
| `podiumd-minikube show-podiumd-version` | Prints the current version selection and fetch status per dependency |
| `podiumd-minikube show-port-mappings` | Prints how host traffic reaches each deployed app (Ingress hostname → backend Service:port) |
| `podiumd-minikube expose-postgres [local-port]` | Port-forwards the shared Postgres instance to localhost for a GUI tool |
| `podiumd-minikube replicas on\|off` | `on`: Redis at 3 pods, Keycloak at 2 (the chart's counts) for tests that need more than one pod; `off`: one each, as `values.yaml` and every deploy. Patches the live objects, no deploy (`on` about 80 s) |
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
`flush-redis`, `replicas` and `setup-tunnel stop` take it
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

### Code for older podiumd releases

Code, values and templates needed only for releases before podiumd X.Y carry
a `podiumd<X.Y:` comment with the reason (for example `podiumd<4.10:` for
the separate objecttypen subchart). `unit_tests/test_release_leftovers.py`
holds `OLDEST_PODIUMD`, the oldest release deployed here (now 4.9.4); raise it
when a release is dropped, and the test lists every marker that has become
dead. The same test fails on an `image.tag` under `podiumd:` in
`values.yaml`: podiumd pins its images itself, and a pin of ours goes stale
with the next release.

## Why not `helm install`?

Helm's own release record embeds the entire resolved chart — including
the ~3.87MB `podiumd` dependency — which exceeds Kubernetes' 3MB API
request-size limit. This project uses `helm template | kubectl apply`
instead, which means Helm's install/upgrade hooks never fire —
`podiumd-minikube deploy` handles the one place that matters
(`templates/storage-hooks.yaml`'s PV/PVC pre-provisioning) as a separate
step instead.

The render does run the chart's `lookup` against the cluster, as `helm upgrade`
does (`helm template --dry-run=server --take-ownership`, `deploy.LOOKUP_ARGS`).
What stays untested: hook order and weights, `.Release.Revision` above 1, Helm's
three-way merge, and the "field is immutable" failure of a changed fixed-name
Job (deploy recreates its Jobs, and warns about fixed-name Jobs ExternalsPodiumD
does not delete first).

## Differences from the estates

What minikube does differently from ExternalsPodiumD and podiumd-infra, and what
that leaves untested here (podiumd-tests then needs a real environment).

| Area | minikube | Estates | Untested here |
|---|---|---|---|
| Nodes | 1 node; required pod anti-affinity dropped (`manifests.allow_rolling_updates`) | several nodes | scheduling and spreading, node failure, zone outages |
| Replicas | 1 everywhere: Redis, Keycloak (`instances: 1`), Elasticsearch, Solr, ZooKeeper, web pods | Redis 3 (ExternalsPodiumD relabels the master in office hours), Keycloak 2 (kees00 with a PDB), Open Inwoner ES 2, KISS ES 3 | failover, Redis master switch, Keycloak cluster cache, PDBs |
| Storage | hostPath ReadWriteOnce (`templates/storage-hooks.yaml`) | Azure Files SMB/NFS, shared between pods | multi-pod shared media, Azure Files permissions and latency |
| Postgres | in-cluster PostGIS 15, owned by postgres with grants and the Azure extension allow-list | Azure Flexible Server 15 | Azure TLS and network rules, connection limits, server parameters |
| Edge | NGINX Gateway Fabric with HTTPRoutes generated from the chart's Ingresses (`podiumd_minikube.gateway`); local CA | ExternalsPodiumD: hand-written HTTPRoutes with `X-Forwarded-*`; public certificates (kees00: NGF since 2026-10-20) | a route the generator derives but the estates write differently, public TLS |
| Pod tuning | service links off, GC and malloc settings, `GOMEMLIMIT`, the local CA in every pod (`manifests.fix_up`) | chart defaults | behaviour that depends on heap size or GC, CA handling through the chart's own options |
| Keycloak | does not trust the local CA, so the admin IdP stand-in's backchannel is in-cluster http | public https | the IdP's TLS to the acceptance Keycloak |
| External services | BAG mock with two answers, Mailpit without STARTTLS, ClamAV with only EICAR, IdP stand-in `acc-mock`, Office add-in with placeholder MSAL ids, no e-Suite | Kadaster BAG, relay SMTP, full ClamAV, Dimpact's acceptance Keycloak, Entra ID, e-Suite on some | real data shapes, STARTTLS, real signatures, Office sign-in, e-Suite |
| Monitoring | Grafana anonymous, Loki filesystem store, opt-in (`metrics` profile); 12 CRDs of unused kinds not applied (`crds.UNUSED_MONITORING_LOGGING_CRDS`: Alertmanager, ThanosRuler, PrometheusAgent, ScrapeConfig, Grafana Agent, rollout operator) | Grafana through Keycloak client `monitoring`; ExternalsPodiumD Loki distributed; all CRDs | Grafana login, Loki at scale |
| Clients | no datamigratie client, no `mi` export | on where those run | datamigratie, MI SFTP export |

The service links minikube turns off cannot clash on an estate: the Services
that broke Solr and the Open Notificaties worker (`solr`, `rabbitmq`) are gone
in podiumd 4.10, the chart sets the `<app>_PORT` variables apps read
explicitly (Open VTB's values say why), and `POSTGRES_PORT` would only clash
with minikube's in-cluster `postgres` Service.

## Troubleshooting

**Cluster becomes sluggish or unresponsive, especially after switching
profile combinations a few times.** Usually an under-provisioned minikube
VM thrashing under memory pressure — check with `docker stats minikube`
or `minikube ssh -- free -h`. `provision-cluster` sizes the node at half the
memory Docker sees (override with `MINIKUBE_MEMORY` in MB); `deploy --full` and
`provision-cluster` warn when the running node is below the 17 GiB a
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
