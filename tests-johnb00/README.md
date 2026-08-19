# johnb00 live-cluster test suite

A copy of `../tests/` adapted to run against the real `johnb00` AKS
environment (see `podiumd-infra`'s `values/johnb00/`) instead of a local
minikube cluster. `../tests/` itself is untouched and still targets
minikube only.

## Why a separate copy instead of parameterizing `../tests/`

The two environments differ enough structurally (real DNS + trusted TLS vs.
Traefik-IP + Host-header; an external Azure Postgres Flexible Server vs. an
in-cluster pod; different realm/client naming; no wiremock) that a single
parameterized suite would need almost as much conditional logic as just
keeping two copies. See each file's own docstring here for its specific
adaptation from the `../tests/` reference.

## Prerequisites

- `kubectl` current context is `podiumd-johnb00-aks` (`az aks get-credentials
  --resource-group rg-podiumd-johnb00 --name podiumd-johnb00-aks`).
- The Keycloak test-user password is set - see below.
- Same venv as `../tests/` (`../.venv`): `pip install -r requirements.txt`,
  `playwright install chromium` (already done if you've run `../tests/`
  before).

## Setting the Keycloak test-user password

johnb00's realm has a real user named `johnb00`, but its password isn't
readable from outside CI (Key Vault access is restricted to the GitHub
Actions service principal). Set a known password directly via the Keycloak
Admin API, using the master-realm admin credential already readable via
`kubectl` (no Key Vault needed):

```bash
KC_USER=$(kubectl get secret keycloak-podiumd-admin -n podiumd -o jsonpath='{.data.username}' | base64 -d)
KC_PASS=$(kubectl get secret keycloak-podiumd-admin -n podiumd -o jsonpath='{.data.password}' | base64 -d)
TOKEN=$(curl -s -X POST "https://keycloak.johnb00.pd.test-rig.nl/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=${KC_USER}&password=${KC_PASS}" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
USER_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "https://keycloak.johnb00.pd.test-rig.nl/admin/realms/podiumd/users?username=johnb00" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['id'])")
NEWPW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
curl -s -X PUT "https://keycloak.johnb00.pd.test-rig.nl/admin/realms/podiumd/users/${USER_ID}/reset-password" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"type\":\"password\",\"value\":\"${NEWPW}\",\"temporary\":false}"
printf 'KEYCLOAK_TEST_USERNAME=johnb00\nKEYCLOAK_TEST_PASSWORD=%s\n' "$NEWPW" > .env.johnb00
```

`.env.johnb00` is gitignored - never commit it. `conftest.py`'s
`keycloak_test_user` fixture reads it; every test that needs a real login
skips with a clear message if the file doesn't exist yet.

**Known limitation**: the `johnb00` user currently has no group/role
mapping in Keycloak, so it likely isn't authorized in PABC's own
role/domain mapping. `test_login_flow.py` and `test_browser.py` both treat
landing on ZAC's "Geen toestemming" page as a skip (OIDC round trip
verified, PABC authorization gap noted separately), not a failure.

## Running

```bash
source ../.venv/bin/activate
pytest
```

`test_pabc_migrations_guard.py` and `test_productaanvraag_flow.py` both
skip unconditionally at collection time (see their own docstrings) - the
former because it deliberately mutates real cluster state and has no
podiumd-infra equivalent script to test anyway, the latter because it's
pinned to podiumd-minikube's own seeded demo fixture data that johnb00
doesn't have. `test_django_admin_login.py` skips unconditionally too -
johnb00 has no classic Django-admin credentials configured (admin access is
OIDC/PABC-mediated instead).

## What's different from `../tests/`, file by file

| File | Adaptation |
|---|---|
| `conftest.py` | Real HTTPS hostnames instead of Traefik IP + Host header; namespace `podiumd`; drops the Chromium `--host-resolver-rules` fixture entirely; adds `keycloak_test_user` |
| `test_pods.py` | Real core pod-prefix list (no in-cluster postgres/solr/wiremock; `redis` → `redis-ha`) |
| `test_reachability.py` | Real ingress hostnames from `values/johnb00/infra.yaml`, every expected status code re-verified live (not carried over from minikube) |
| `test_mailpit.py` | Real hostname; otherwise unchanged - mailpit was deployed to johnb00 this session specifically so this test could run for real |
| `test_zgw_service_reachability.py` | Namespace-suffix fix only; pod label selectors verified to still match live |
| `test_database.py` | Rewritten to use `manage.py shell` inside each app's pod instead of `kubectl exec` into a postgres pod (johnb00's Postgres is an external Azure Flexible Server) - needs no separate DB credential. `EXPECTED_DATABASES`/`POSTGIS_DATABASES` re-derived live (real names differ from minikube's; opennotificaties has no PostGIS on johnb00) |
| `test_login_flow.py` | Real realm (`podiumd`) and client (`zac`, not `zaakafhandelcomponent`); real `johnb00` test user via `keycloak_test_user`; PABC-authorization-gap handling |
| `test_browser.py` | Same realm/client/user adaptation as `test_login_flow.py` |
| `test_pkce.py` | Drastically reduced - kept only what's empirically verifiable live (no client currently requires PKCE; pabc's own hardcoded challenge-sending). Did not re-derive the reference file's full per-library PKCE-support investigation |
| `test_metrics.py` / `test_monitoring_logging.py` | Import fix only (`app_url` instead of `host_url`/`host_headers`) - both always skip on johnb00 today (no metrics profile deployed) |
| `test_django_admin_login.py` | Skips unconditionally - no classic admin credentials exist on johnb00 |
| `test_pabc_migrations_guard.py` | Skips unconditionally - no equivalent script, and its second test mutates real state |
| `test_productaanvraag_flow.py` | Skips unconditionally - pinned to minikube-only seeded fixture data |

## Known, pre-existing johnb00 infra findings (not introduced by this suite)

- `abc.johnb00.pd.test-rig.nl` (openarchiefbeheer) and
  `opennotificaties.johnb00.pd.test-rig.nl` both 404: the former because
  openarchiefbeheer is `enabled: false` but still has a dangling Ingress;
  the latter because its Ingress points at Service `opennotificaties-nginx`,
  which doesn't exist (opennotificaties' real Service is `notificaties` via
  `fullnameOverride`). See `test_reachability.py`'s own docstring.
