# Live-cluster test suite

Checks that this project's code did its job on a deployed cluster: the
fixups, templates, scripts and `values.yaml` wiring. How PodiumD's
applications behave is tested by podiumd-tests, on minikube and on the real
environments; after a deploy, `podiumd-tests run --env minikube --tier smoke`
(read-only, takes no lock, about 10 s) checks that the applications answer.

See [`../README.md`](../README.md) for provisioning and deploying the cluster.

## Prerequisites

- The chart is deployed to the `podiumd-minikube` namespace, e.g. via
  `../scripts/deploy --full`.
- `kubectl` points at the cluster.
- The edge has an external IP: run `../scripts/setup-tunnel` first if
  `kubectl get svc public-gateway-nginx -n ingress-basic` shows `<pending>`.

No `/etc/hosts` edits are needed: `*.local` resolves to the edge inside the
test process.

## Running

```bash
python3 -m venv ../.venv
source ../.venv/bin/activate      # ..\.venv\Scripts\activate on Windows
pip install -r ../requirements.txt
pytest
```

Tests for a profile that is not deployed skip; the profiles are read from
the running pods. `test_metrics.py` runs for `templates/metrics/`,
`test_monitoring_logging.py` only when `monitoringLogging.enabled=true`.

## What's covered

| File | What it checks (the code it guards) |
|---|---|
| `test_reachability.py` | Every Ingress host has an HTTPRoute at the edge, accepted with resolved backends (`lib.gateway`) |
| `test_edge.py` | The edge rejects a 2 MB request body, as ExternalsPodiumD's limit (`lib.gateway`) |
| `test_keycloak_realm.py` | The live realm matches the realm sync's target, PKCE included (`lib.keycloak`, `manifests.fix_realm`) |
| `test_database.py` | Databases, PostGIS, ZAC's Open Zaak credentials and Open Notificaties' kanaal/abonnement (`templates/postgres`, `values.yaml`) |
| `test_zgw_service_reachability.py` | The apps' zgw_consumers api_roots answer from inside their pods (`lib.dns`, `manifests.trust_ca`) |
| `test_metrics.py` | Grafana's datasources and Prometheus's scrape targets (`templates/metrics`) |
| `test_monitoring_logging.py` | The scrape jobs `values.yaml` adds to monitoring-logging's Prometheus |
| `test_kiss_ita.py` | KISS's Elasticsearch is green with its heap and limit; the edge forwards ITA's chunked bodies |
| `test_frankgateway.py` | OpenBao unsealed (`lib.openbao`), the outway's routes, ZAC pointed at the outway (`manifests.route_outbound`) |
| `test_clamav.py` | ClamAV runs with the EICAR-only signatures (`templates/clamav`) |
| `test_pabc_migrations_guard.py` | `scripts/apply-pabc-migrations` refuses to recreate the Job when PABC's database has data |
| `test_memory.py` | The node within the laptop budget, no container more than 20% + 64 MiB above `memory-baseline.json`; `--update-memory-baseline` rewrites it |

## Caveats

- `test_database.py` runs `psql` through `kubectl exec` in the postgres pod.
- `test_pabc_migrations_guard.py` changes the cluster: it deletes the
  `pabc-migrations-1` Job and restores it in a `finally` block, holding the
  cluster lock while it does.
