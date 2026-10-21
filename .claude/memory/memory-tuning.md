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
| uWSGI (all Maykin apps) | 1 process; 2 threads, or podiumd's own (Open Zaak, Open Klant 4). Open Inwoner: `UWSGI_MAX_REQUESTS=1000` (image 100, as the other Maykin apps): the first request on a recycled worker takes 2.5-6 s, which a single worker made every request wait for | `settings.uwsgi.processes`/`threads`; Open Archiefbeheer: `extraEnvVars` `UWSGI_PROCESSES`/`UWSGI_THREADS` (its chart ignores the keys). Open VTB: also 1 replica (chart 2). Never 1 x 1: an app that calls itself would block | -1.3 GiB (4 -> 2), then web pods 3243 -> 2225 MiB (2 -> 1) |
| Elasticsearch (KISS, Open Inwoner) | heap 192m, `node.processors: 1`, `xpack.ml.enabled: false`, limit 1536Mi; the capacity variables reach ECK pod templates through `limit_runtimes` | `values.yaml` nodeSets `podTemplate` `ES_JAVA_OPTS` (one anchor); old gen peaks at 100 MiB after a full tier. KISS keeps about 600 MiB outside the heap, so 1Gi leaves too little room (it OOMKilled at 512m) | -1.2 GiB (heap 512m), then 1143 -> 862 and 955 -> 669 MiB (256m), then KISS 994 -> 697 MiB, 186 -> 60 threads (processors 1 and MALLOC_ARENA_MAX), then ML off: KISS 704 -> 669, Open Inwoner 681 -> 647 MiB (no ML native controller); launcher JVM (stays resident beside the server) `CLI_JAVA_OPTS=-Xmx32m -XX:TieredStopAtLevel=1` in `CAPACITY_ENV`: PSS 98/97 -> 76/81 MiB; heap 192m: after a full tier 581 and 570 MiB, no breaker trips |
| .NET (brp-personen-mock, contact-web, ita-web, pabc, adapter) | workstation GC, no background GC, `GCConserveMemory` 7 | `manifests.CAPACITY_ENV`, added to every container by `manifests.limit_runtimes` | -0.9 GiB (workstation GC), then -0.11 GiB |
| Go runtimes with a memory limit (Grafana, Prometheus, OpenBao, etcd, Mailpit, ECK operator) | `GOMEMLIMIT` = the container's memory limit (downward API `resourceFieldRef: limits.memory`) | `manifests.limit_runtimes`, for every container with a memory limit (other runtimes ignore it) | Tempo (since removed) under a full tier: OOMKilled twice at 256Mi without it, peak 156 MiB and no restarts with it |
| glibc malloc (Python, JVM, .NET; musl images ignore it) | `MALLOC_ARENA_MAX=2` | `manifests.CAPACITY_ENV` | after a full tier: containers 12884 -> 12466 MiB; Open Formulieren worker -98, Open Zaak worker -83, the 23 Maykin containers -294 MiB |
| Go: transparent huge pages (host THP `always`) | `GODEBUG=disablethp=1` | `manifests.GO_NO_HUGE_PAGES`, in `CAPACITY_ENV` and `provision.CONTROL_PLANE_ENV` (kube-apiserver, etcd, kube-controller-manager, kube-scheduler); containerd and the kubelet through a systemd drop-in (`provision.tune_node_services`) | released heap stayed charged in partly freed 2 MiB pages: 514 MiB over all containers plus 230 on kube-apiserver; 82 MiB after. kubelet, containerd, kube-scheduler: anonymous 156/154/39 -> 142/135/32 MiB |
| Control plane (kube-apiserver, etcd, kube-controller-manager; static pods, no memory limit) and the node's kubelet and containerd | `GOGC=50`; kube-apiserver also `GOMEMLIMIT=1200MiB` | `provision.CONTROL_PLANE_ENV` per pod, applied by `provision.tune_control_plane`, run by `start_node` (provision-cluster and start-cluster): minikube start rewrites `/etc/kubernetes/manifests`; kubelet and containerd: `provision.NODE_SERVICE_ENV` in the systemd drop-in of `tune_node_services` | after a full tier: kube-apiserver 913 -> 400 MiB, etcd 159 -> 90 MiB (GOGC); kube-apiserver after deploys 1097-1554 -> at most 757 MiB RSS (GOMEMLIMIT; live heap 450-540 MiB). 700MiB was too low: at a 500 MiB live heap the GC ran 6 times a second, about 3 cores all day; the limit must exceed 1.5x the live heap. kubelet 160 -> 110, containerd 147 -> 89 MiB anonymous (GOGC, 30 minutes after restart) |
| APISIX (Frank!Gateway outway) | 2 nginx workers | `CAPACITY_ENV` `APISIX_WORKER_PROCESSES` (`auto` started 25) | -0.45 GiB |
| ZAC (WildFly bootable jar) | `-Xms64m -Xmx768m`, G1 with `-XX:G1PeriodicGCInterval=60000 -XX:MinHeapFreeRatio=10 -XX:MaxHeapFreeRatio=30`, `-XX:TieredStopAtLevel=1` | `values.yaml` `zac.javaOptions` (`_JAVA_OPTIONS`) | 1056 -> 882 MiB (heap 555 -> 390 committed). Not the serial GC: it kept the heap near -Xmx (+133 MiB). C1 only: 821 -> 737 MiB (code cache 31 -> 11), ZAC tests a few hundred ms slower per tier |
| ZaakBrug (Frank!Framework, Tomcat) | `frank.memory` 128M-640M, `-XX:TieredStopAtLevel=1`, 768Mi/1536Mi; serial GC by JVM ergonomics (limit below 1792 MiB), so G1 flags do nothing | `values.yaml` `podiumd.zaakbrug.frank` and `resources` | chart Xms=Xmx=4G (ontw idles at 3388 MiB) -> 676 MiB; old gen 332 of 512 MiB, metaspace 120 MiB after 15 minutes; at 640M (user's call): 733 MiB 5 minutes after start, old gen 244 of 407 MiB committed (the serial GC keeps its startup peak committed); heap free ratios 10/30 tried and removed: no full GC in a full tier, so nothing given back (old gen 416 MiB committed, 247 used; 909 MiB) |
| Keycloak (Quarkus) | `-Xms64m -Xmx512m -XX:-UseG1GC -XX:+UseSerialGC -XX:TieredStopAtLevel=1`, on its builder init container too; 512Mi/768Mi (chart 1700Mi/2Gi), `instances: 1` | `manifests.KEYCLOAK_JAVA_OPTS` (`tune_keycloak`, `JAVA_OPTS_APPEND` in the Keycloak resource's podTemplate); kc.sh sets `-XX:+UseG1GC`, so `-XX:-UseG1GC` is required | 592 -> 416 MiB; C1 only: 551 -> 409 MiB, discovery p95 4-5 -> 7 ms |
| Keycloak operator (Quarkus, `java -jar`) | `JDK_JAVA_OPTIONS` `-Xms32m -Xmx128m`, serial GC, C1 only, 1 CPU; operator SDK reconciliation and workflow threads 2 each (default 50 each); limit 384Mi (chart 768Mi) | `manifests.KEYCLOAK_OPERATOR_ENV` (`tune_keycloak`), `values.yaml` `keycloak-operator.operator.resources` | 127 -> 54 threads, PSS 207 -> 173 MiB (heap in use about 55 MiB, metaspace 54 MiB) |
| Solr (ZAC's SolrCloud) and its ZooKeeper | Solr `-Xms64m -Xmx512m`, `GC_TUNE=-XX:+UseSerialGC`, 1 replica (chart 3); ZooKeeper `ZK_SERVER_HEAP=128`, serial GC, C1 only | `values.yaml` `podiumd.zac.solr-operator`; the env through `manifests.tune_solr` into the SolrCloud's podOptions and zookeeperPodPolicy | plain Solr: 425 -> 214 MiB |
| monitoring-logging: components | kube-state-metrics, node-exporter, pushgateway, default rules off, as ExternalsPodiumD | `values.yaml` `monitoring-logging.kube-prometheus-stack`, `prometheus-pushgateway` | containers 1598 -> 1343 MiB; kube-apiserver unchanged (its +460 MiB comes with the CRDs) |
| monitoring-logging: Prometheus | 384Mi/768Mi, `retentionSize` 1GB, `GOMEMLIMIT` | `prometheusSpec.resources`; the operator builds its pod, so `limit_runtimes` patches the `Prometheus` resource's `containers` | 345 -> 204 MiB |
| monitoring-logging: otel-collector | limit 256Mi (chart 512Mi); `memory_limiter` is 80% of it | `opentelemetry-collector.resources` | 173 -> 43 MiB |
| monitoring-logging: Loki GC | `GOGC=50` (default 100) | `loki.singleBinary.extraEnv` | grew 202 -> 328 MiB over a day of tiers at GOGC 100; at GOGC 50, 2.3 h and a full tier after start: 260 MiB, heap in use 152 MiB, next GC at 160 MiB (the rest of the heap is in-memory chunks, not GC slack) |
| monitoring-logging: Alloy GC | `GOGC=50`; its `extraEnv` must keep the chart's `NODE_NAME` (Helm replaces lists) | `monitoring-logging.alloy.alloy.extraEnv` | 247 MiB = 74 anon + 160 active log-file cache; GOGC trims only the anon part (settled number pending) |
| monitoring-logging: Grafana GC | `GOGC=50` | `monitoring-logging.grafana.env` | after a full tier 239 -> 116 MiB (not scraped by Prometheus, so measured as the working set) |
| Postgres | `shared_buffers=64MB` (default 128MB): it is shmem, counted fully in the working set | `templates/postgres/deployment.yaml` args | 407 -> 367 and 387 MiB after full tiers (shmem 154 -> 88); full tier 1.015 -> 1.019 s per test, unchanged |
| monitoring-logging: Loki, sidecars | Loki 512Mi/1Gi as podiumd-infra; Grafana's and Loki's sidecars 80-96Mi/128-160Mi | `loki.singleBinary.resources`, `grafana.sidecar`, `loki.sidecar` | Loki 310 -> 210 MiB |
| kube-apiserver: CRDs | 12 monitoring-logging CRDs of kinds nothing uses are not applied; the Prometheus operator logs `not installed in the cluster` and skips their controllers | `crds.UNUSED_MONITORING_LOGGING_CRDS` | live heap 435-466 -> 401-406 MiB (the five monitoring.coreos.com ones); about 24 MiB of live heap per MiB of CRD JSON |
| Kibana | off | `podiumd.kiss-eck.eck-kibana.enabled: false` | -0.7 GiB |
| ClamAV | EICAR-only signature database | `podiumd.clamav` + `templates/clamav` | 19 MiB instead of 973 |
| KISS contact-web | 1 replica | `podiumd.kiss.replicaCount` (chart default 2) | -0.12 GiB |
| Cilium (agent, operator) | agent `GOGC=50` (`cni.values` extraEnv); fixed BPF map sizes (CT TCP 65536, CT any 32768, NAT and neigh 65536, policy, LB, auth and policy stats 16384; `extraConfig` `bpf-sock-rev-map-max` 16384, not in the chart); `rollOutCiliumPods` so a config change restarts the agent; limits 512Mi/192Mi for GOMEMLIMIT; 1 operator replica (AKS 2) | `cni.values()`, the render through `manifests.limit_runtimes` (`cni.render`) | the agent's cgroup 556 -> 162 MiB (BPF maps sized from the host's 93 GiB: 420 MiB kernel memory); with CAPACITY_ENV and GOMEMLIMIT agent 126, operator 47 MiB on the spike; on the full cluster the maps (bpftool memlock) 101.7 -> 42.7 MiB, agent 465 -> 157 MiB (policy stats and sock rev maps); GOGC=50: Go heap held 163 -> 117 MiB (live 127 -> 47 at the samples), working set 241 -> 129 MiB 25 minutes after the restart (part of that is the restart itself) |
| minikube node | 6 CPUs: quota and cpuset 0-5 | `provision.start_node` (`MINIKUBE_CPUS`): `docker update --cpus=6 --cpuset-cpus=0-5 minikube`; the quota alone still showed 24 CPUs to `nproc` (24 nginx workers) | caps CPU-scaled defaults: edge nginx 24 -> 6 workers (96 -> 54 MiB); full tier 7:31 -> 6:36 |

## Checks

- First `podiumd-minikube show-cluster-status`: node and containers against the
  budget and the baseline (`!` above the tolerance), and whether the cluster
  has settled.
- A GC change is measured per app after a warm-up (the suite) and 5 idle
  minutes: the serial GC suits small, low-allocation heaps (Keycloak, Solr);
  for ZAC it grew the heap.
- Every JVM: `kubectl exec <pod> -- sh -c 'tr "\0" " " < /proc/1/cmdline'` and
  its env; exactly one `-XX:+Use*GC` may be on, else the JVM does not start.
  `java -Xlog:os+container=debug -version` in the container must not say
  "disabled at kernel level": JDK 17.0.19 misses its limit on kernel 7.x.
  ZAC's heap and non-heap: WildFly management `http://<zac pod IP>:9990/metrics`
  (`base_memory_*`), e.g. from the openzaak pod with python's urllib.
- Celery: `ps`/`/proc/*/cmdline` in the worker shows `-c 1`; uWSGI: count the
  `uwsgi` processes in the web pod (master + http + 1 worker = 3; Open
  Archiefbeheer has no http router: 2).
- Control plane: `kubectl -n kube-system get pod kube-apiserver-minikube etcd-minikube -o jsonpath='{..env}'` shows `GOGC=50`, `GODEBUG=disablethp=1` (and `GOMEMLIMIT=1200MiB` on kube-apiserver); kube-apiserver's `/metrics` `go_gc_gogc_percent` is 50, and `go_gc_cycles_total_gc_cycles_total` rises by a few per minute, not per second. kubelet and containerd: `minikube ssh -- 'sudo systemctl show -p Environment kubelet containerd'` shows `GOGC=50 GODEBUG=disablethp=1`.
- Cilium: `kubectl -n kube-system get cm cilium-config` shows `bpf-ct-global-tcp-max` 65536 and `bpf-sock-rev-map-max` 16384; `kubectl -n kube-system exec ds/cilium -c cilium-agent -- bpftool map show`: memlock summed over all maps about 43 MiB, not 100+.
- Elasticsearch: `_nodes/os` shows `allocated_processors` 1; the java process' environ has `MALLOC_ARENA_MAX=2` (ECK builds the pods, so a fixup that misses its pod templates misses them).
- .NET: the env of the five apps has `DOTNET_gcServer=0`,
  `DOTNET_gcConcurrent=0`, `DOTNET_GCConserveMemory=7`.
- Go huge pages: `AnonHugePages` in `/proc/<pid>/smaps_rollup` of a Go process is a few MiB at most; a container's `memory.current` minus `anon`+`file`+`kernel` of its `memory.stat` is the charge nothing maps.
- Per-process cost: PSS from `/proc/<pid>/smaps_rollup` (a Celery child
  100-195 MiB, a uWSGI process 160-240 MiB); per container: the working set
  from `crictl stats` (what `test_memory.py` records).
- New component: state its request/limit and settled usage in plan.md, set
  an explicit heap for a JVM, and check what it sizes from the CPU count.

Related: [[laptop-resources]], [[reference-environments]].
