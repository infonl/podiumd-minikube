# Live-cluster test suite

Integration/smoke tests against a real, already-deployed minikube cluster —
not unit tests. They formalize the manual `curl`/`kubectl`-based
verification used throughout this project's build-order steps into a
repeatable pytest suite.

See [`../README.md`](../README.md) for how to provision and deploy the
cluster this suite runs against in the first place.

## Prerequisites

- The chart is deployed to the `podiumd-minikube` namespace, e.g. via
  `../scripts/deploy.sh --full` (all optional profiles need to be running
  for the full suite to pass, not just the core ones).
- `kubectl` is configured against the cluster (current context).
- Traefik has a real LoadBalancer external IP — run `../scripts/setup-tunnel.sh`
  first if `kubectl get svc traefik -n traefik` shows `<pending>`.

No `/etc/hosts` edits are needed to run the suite: every test reaches
services by Traefik's IP directly, with an explicit `Host` header per
request, resolved automatically at test time.

## Running

```bash
pip install -r requirements.txt
pytest
```

Tests for optional profile groups (objecten, objecttypen, opennotificaties,
openarchiefbeheer, openformulieren, metrics, itest) auto-skip if that
profile isn't currently deployed — profile detection is based on which pods
are actually running, not on reading `values.yaml`, so the suite always
reflects the real cluster state.

## What's covered

| File | What it checks |
|---|---|
| `test_pods.py` | Every pod is `Running`/`Succeeded`, every long-running container is `Ready`, the core stack is present |
| `test_reachability.py` | Every Ingress hostname (core + profile-gated) returns its expected status code |
| `test_login_flow.py` | The full OIDC login flow through `zac.local` — redirect to Keycloak, login form, credential submission, authorization code, callback, landing on the authenticated app shell |
| `test_database.py` | All expected Postgres databases exist, PostGIS is installed where needed, ZAC's own ZGW client credentials are seeded in Open Zaak |
| `test_metrics.py` | Grafana's provisioned datasources and Prometheus's scrape targets are actually healthy |
| `test_opa_policies.py` | The `opa-tests` Job succeeded |
| `test_pabc_migrations_guard.py` | `scripts/apply-pabc-migrations.sh` actually refuses to recreate the (non-idempotent) pabc-migrations Job when PABC's database already has data |

## Known caveats

- `test_login_flow.py` uses a dev-only Keycloak test user
  (`beheerder1newiam`) whose password was set directly via the Keycloak
  Admin API during live verification, not a compose default. If a fresh
  cluster doesn't have this password set, see that file's docstring for how
  to reset it.
- `test_database.py` shells out to `kubectl exec` into the postgres pod
  rather than connecting directly — no port-forward is assumed to be
  running.
- `test_pabc_migrations_guard.py`'s second test genuinely mutates cluster
  state (it deletes the real `pabc-migrations-1` Job to reach the scenario
  the guard protects against), unlike every other test in this suite,
  which is read-only. It's fully recoverable - the `finally` block restores
  the Job via `--force`, reloading the same seed dataset the database
  already had - but be aware if running this suite against a cluster
  someone else is actively using.
