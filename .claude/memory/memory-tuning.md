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
| uWSGI (all Maykin apps) | 2 processes | `settings.uwsgi.processes`; Open Archiefbeheer: `extraEnvVars` `UWSGI_PROCESSES` (its chart ignores the key) | -1.3 GiB |
| Elasticsearch (KISS, Open Inwoner) | heap 512m, limit 1536Mi | `values.yaml` nodeSets `podTemplate` `ES_JAVA_OPTS`; 1Gi limit OOMKilled KISS's | -1.2 GiB |
| .NET (brp-personen-mock, contact-web, ita-web, pabc, adapter) | workstation GC, no background GC, `GCConserveMemory` 7 | `manifests.CAPACITY_ENV`, added to every container by `manifests.limit_runtimes` | -0.9 GiB (workstation GC), then -0.11 GiB |
| APISIX (Frank!Gateway outway) | 2 nginx workers | `CAPACITY_ENV` `APISIX_WORKER_PROCESSES` (`auto` started 25) | -0.45 GiB |
| ZAC (WildFly bootable jar) | `-Xms64m -Xmx768m`, G1 with `-XX:G1PeriodicGCInterval=60000 -XX:MinHeapFreeRatio=10 -XX:MaxHeapFreeRatio=30` | `values.yaml` `zac.javaOptions` (`_JAVA_OPTIONS`) | 1056 -> 882 MiB (heap 555 -> 390 committed). Not the serial GC: it kept the heap near -Xmx (+133 MiB) |
| Keycloak (Quarkus) | `-Xms64m -Xmx512m -XX:-UseG1GC -XX:+UseSerialGC` | `templates/keycloak/deployment.yaml` `JAVA_OPTS_APPEND`; kc.sh sets `-XX:+UseG1GC`, so `-XX:-UseG1GC` is required | 592 -> 416 MiB |
| Solr | `-Xms64m -Xmx512m`, `GC_TUNE=-XX:+UseSerialGC` | `templates/solr/deployment.yaml` | 425 -> 214 MiB |
| WireMock | nothing: its 256Mi limit gives a 64 MiB heap and the serial GC by default | `templates/wiremock/deployment.yaml` limit | |
| Kibana | off | `podiumd.kiss-eck.eck-kibana.enabled: false` | -0.7 GiB |
| ClamAV | EICAR-only signature database | `podiumd.clamav` + `templates/clamav` | 19 MiB instead of 973 |
| KISS contact-web | 1 replica | `podiumd.kiss.replicaCount` (chart default 2) | -0.12 GiB |
| minikube node | 6 CPUs | `provision` `--cpus` (`MINIKUBE_CPUS`); a node made without it: `docker update --cpus=6 minikube` | caps CPU-scaled defaults |

## Checks

- A GC change is measured per app after a warm-up (the suite) and 5 idle
  minutes: the serial GC suits small, low-allocation heaps (Keycloak, Solr);
  for ZAC it grew the heap.
- Every JVM: `kubectl exec <pod> -- sh -c 'tr "\0" " " < /proc/1/cmdline'` and
  its env; exactly one `-XX:+Use*GC` may be on, else the JVM does not start.
  ZAC's heap and non-heap: WildFly management `http://<zac pod IP>:9990/metrics`
  (`base_memory_*`), e.g. from the openzaak pod with python's urllib.
- Celery: `ps`/`/proc/*/cmdline` in the worker shows `-c 1`; uWSGI: count the
  `uwsgi` processes in the web pod (master + http + 2 workers = 4).
- .NET: the env of the five apps has `DOTNET_gcServer=0`,
  `DOTNET_gcConcurrent=0`, `DOTNET_GCConserveMemory=7`.
- Per-process cost: PSS from `/proc/<pid>/smaps_rollup` (a Celery child
  100-195 MiB, a uWSGI process 160-240 MiB); per container: the working set
  from `crictl stats` (what `test_memory.py` records).
- New component: state its request/limit and settled usage in plan.md, set
  an explicit heap for a JVM, and check what it sizes from the CPU count.

Related: [[laptop-resources]], [[reference-environments]].
