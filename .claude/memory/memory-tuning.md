# Memory tuning: what is set, where, and how to check it

The capacity settings that keep `--full` small (see [[laptop-resources]]).
Each one changes capacity only, never behaviour. Before changing one of these
places, or adding a component, go through the checks below; a setting that
silently drops out (a chart upgrade, a refactor) is a regression.

**Why:** the user asked (2026-10-08) for one place to repeat these checks and
prevent regressions; each setting was found and measured separately.

## Guard

`tests/test_memory.py` (part of the live suite): node within the budget, no
container more than 20% + 64 MiB above `tests/memory-baseline.json`. Refresh
the baseline only after an intended change, on a settled cluster (it refuses
while a container is younger than 5 minutes), and after podiumd-tests' full
tier has run: the working set includes active page cache and warm worker
memory, so a baseline taken after our suite alone is too low (Open Inwoner
473 -> 758 MiB, Postgres 180 -> 337 MiB after a full tier, no leak):
`pytest tests/test_memory.py --update-memory-baseline`.

## Settings

| Runtime / app | Setting | Where | Measured effect |
|---|---|---|---|
| Celery workers (Open Zaak, Open Formulieren, Open Inwoner incl. low-latency) | concurrency 1 | `values.yaml` `worker.concurrency`; Open Inwoner: `extraEnvVars` `CELERY_WORKER_CONCURRENCY` (its chart ignores `worker.concurrency`) | -2.3 GiB |
| uWSGI (all Maykin apps) | 1 process; 2 threads, or podiumd's own (Open Zaak, Open Klant 4) | `settings.uwsgi.processes`/`threads`; Open Archiefbeheer: `extraEnvVars` `UWSGI_PROCESSES`/`UWSGI_THREADS` (its chart ignores the keys). Never 1 x 1: an app that calls itself would block | -1.3 GiB (4 -> 2), then web pods 3243 -> 2225 MiB (2 -> 1) |
| Elasticsearch (KISS, Open Inwoner) | heap 256m, `node.processors: 1`, limit 1536Mi; the capacity variables reach ECK pod templates through `limit_runtimes` | `values.yaml` nodeSets `podTemplate` `ES_JAVA_OPTS`; old gen peaks under 100 MiB. KISS keeps about 600 MiB outside the heap, so 1Gi leaves too little room (it OOMKilled at 512m) | -1.2 GiB (heap 512m), then 1143 -> 862 and 955 -> 669 MiB (256m), then KISS 994 -> 697 MiB, 186 -> 60 threads (processors 1 and MALLOC_ARENA_MAX) |
| .NET (brp-personen-mock, contact-web, ita-web, pabc, adapter) | workstation GC, no background GC, `GCConserveMemory` 7 | `manifests.CAPACITY_ENV`, added to every container by `manifests.limit_runtimes` | -0.9 GiB (workstation GC), then -0.11 GiB |
| Go runtimes with a memory limit (Tempo, Grafana, Prometheus, otel-collector, OpenBao, etcd, Mailpit, ECK operator) | `GOMEMLIMIT` = the container's memory limit (downward API `resourceFieldRef: limits.memory`) | `manifests.limit_runtimes`, for every container with a memory limit (other runtimes ignore it) | Tempo under a full tier: OOMKilled twice at 256Mi without it, peak 156 MiB and no restarts with it |
| glibc malloc (Python, JVM, .NET; musl images ignore it) | `MALLOC_ARENA_MAX=2` | `manifests.CAPACITY_ENV` | after a full tier: containers 12884 -> 12466 MiB; Open Formulieren worker -98, Open Zaak worker -83, the 23 Maykin containers -294 MiB |
| Control plane (kube-apiserver, etcd; static pods, no memory limit) | `GOGC=50` | `provision.tune_control_plane`, run by `start_node` (provision-cluster and start-cluster): minikube start rewrites `/etc/kubernetes/manifests` | after a full tier: kube-apiserver 913 -> 400 MiB, etcd 159 -> 90 MiB |
| APISIX (Frank!Gateway outway) | 2 nginx workers | `CAPACITY_ENV` `APISIX_WORKER_PROCESSES` (`auto` started 25) | -0.45 GiB |
| ZAC (WildFly bootable jar) | `-Xms64m -Xmx768m`, G1 with `-XX:G1PeriodicGCInterval=60000 -XX:MinHeapFreeRatio=10 -XX:MaxHeapFreeRatio=30` | `values.yaml` `zac.javaOptions` (`_JAVA_OPTIONS`) | 1056 -> 882 MiB (heap 555 -> 390 committed). Not the serial GC: it kept the heap near -Xmx (+133 MiB) |
| Keycloak (Quarkus) | `-Xms64m -Xmx512m -XX:-UseG1GC -XX:+UseSerialGC` | `templates/keycloak/deployment.yaml` `JAVA_OPTS_APPEND`; kc.sh sets `-XX:+UseG1GC`, so `-XX:-UseG1GC` is required | 592 -> 416 MiB |
| Solr | `-Xms64m -Xmx512m`, `GC_TUNE=-XX:+UseSerialGC` | `templates/solr/deployment.yaml` | 425 -> 214 MiB |
| WireMock | nothing: its 256Mi limit gives a 64 MiB heap and the serial GC by default | `templates/wiremock/deployment.yaml` limit | |
| Kibana | off | `podiumd.kiss-eck.eck-kibana.enabled: false` | -0.7 GiB |
| ClamAV | EICAR-only signature database | `podiumd.clamav` + `templates/clamav` | 19 MiB instead of 973 |
| KISS contact-web | 1 replica | `podiumd.kiss.replicaCount` (chart default 2) | -0.12 GiB |
| minikube node | 6 CPUs: quota and cpuset 0-5 | `provision.start_node` (`MINIKUBE_CPUS`): `docker update --cpus=6 --cpuset-cpus=0-5 minikube`; the quota alone still showed 24 CPUs to `nproc` (24 nginx workers) | caps CPU-scaled defaults: edge nginx 24 -> 6 workers (96 -> 54 MiB); full tier 7:31 -> 6:36 |

## Checks

- First `./scripts/show-cluster-status`: node and containers against the
  budget and the baseline (`!` above the tolerance), and whether the cluster
  has settled.
- A GC change is measured per app after a warm-up (the suite) and 5 idle
  minutes: the serial GC suits small, low-allocation heaps (Keycloak, Solr);
  for ZAC it grew the heap.
- Every JVM: `kubectl exec <pod> -- sh -c 'tr "\0" " " < /proc/1/cmdline'` and
  its env; exactly one `-XX:+Use*GC` may be on, else the JVM does not start.
  ZAC's heap and non-heap: WildFly management `http://<zac pod IP>:9990/metrics`
  (`base_memory_*`), e.g. from the openzaak pod with python's urllib.
- Celery: `ps`/`/proc/*/cmdline` in the worker shows `-c 1`; uWSGI: count the
  `uwsgi` processes in the web pod (master + http + 1 worker = 3; Open
  Archiefbeheer has no http router: 2).
- Control plane: `kubectl -n kube-system get pod kube-apiserver-minikube etcd-minikube -o jsonpath='{..env}'` shows `GOGC=50`; kube-apiserver's `/metrics` `go_gc_gogc_percent` is 50.
- Elasticsearch: `_nodes/os` shows `allocated_processors` 1; the java process' environ has `MALLOC_ARENA_MAX=2` (ECK builds the pods, so a fixup that misses its pod templates misses them).
- .NET: the env of the five apps has `DOTNET_gcServer=0`,
  `DOTNET_gcConcurrent=0`, `DOTNET_GCConserveMemory=7`.
- Per-process cost: PSS from `/proc/<pid>/smaps_rollup` (a Celery child
  100-195 MiB, a uWSGI process 160-240 MiB); per container: the working set
  from `crictl stats` (what `test_memory.py` records).
- New component: state its request/limit and settled usage in plan.md, set
  an explicit heap for a JVM, and check what it sizes from the CPU count.

Related: [[laptop-resources]], [[reference-environments]].
