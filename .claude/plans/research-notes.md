# Research notes backing plan.md

Supporting detail gathered while designing `plan.md`, kept separate because
it's reference material rather than decisions. If a fact here ever conflicts
with the live chart repos, trust the live repos — this is a point-in-time
snapshot.

## PodiumD Chart.yaml repository aliases

Resolved via PodiumD's `scripts/add-helm-repos.sh`:

| alias | URL |
|---|---|
| `@maykinmedia` | `https://maykinmedia.github.io/charts/` |
| `@dimpact` | `https://Dimpact-Samenwerking.github.io/helm-charts/` |
| `@zac` | `https://infonl.github.io/dimpact-zaakafhandelcomponent/` |
| `@adfinis` | `https://charts.adfinis.com` |
| `@wiremind` | `https://wiremind.github.io/wiremind-helm-charts` |
| `@worth-nl` | `https://worth-nl.github.io/helm-charts` |
| `@opstree` | `https://ot-container-kit.github.io/helm-charts/` |
| `@zgw-office-addin` | `https://infonl.github.io/zgw-office-addin` |

`pabc`, `internetaakafhandeling` (ita), `kiss-chart` use raw OCI repos
(`oci://ghcr.io/...`) directly in PodiumD's Chart.yaml, no alias needed.

Only `@maykinmedia`, `@dimpact`, and the `pabc` OCI repo are relevant to this
project — the rest (adfinis/keycloak-operator, opstree/redis-operator,
apisix, zgw-office-addin, wiremind/clamav, worth-nl/omc) back PodiumD features
this project deliberately doesn't use.

## Why PodiumD's infra choices don't transfer to minikube

- **Keycloak**: PodiumD uses `keycloak-operator` (Adfinis chart wrapping the
  official Keycloak Operator) + its own templates for a `Keycloak` CR, realm
  import via `keycloak-config-cli` Jobs, and a hand-rolled admin-user
  bootstrap (Python computes a PBKDF2 hash, a psql container inserts it
  directly). This requires installing the operator's CRDs cluster-wide and
  running several bootstrap Jobs — considerably more moving parts than
  compose's single `keycloak` container with `--import-realm`, which is what
  this project mirrors instead.
- **Redis**: PodiumD uses `redis-operator` (OT Container Kit) rendering a
  `RedisReplication` CRD — a genuine 3-node HA cluster with per-replica PVCs
  on an Azure-specific storage class (`managed-csi-premiumv2`), plus a
  `labelMasterCronJob` running every 2 minutes to work around an operator bug.
  Total overkill for a single-node dev box; this project uses one plain
  `redis:8.6.4` container, matching compose exactly.
- **Solr**: `charts/zac` (already reused by this project for the ZAC app
  itself) supports both a `solr-operator`-managed SolrCloud cluster (heavy —
  needs the Solr Operator + Zookeeper Operator CRDs installed) and a plain
  external Solr via `solr.url` + `solr.createZacCore: true` (the chart's own
  initContainer creates the "zac" core against whatever Solr instance you
  point it at). The `solr-operator` block defaults to `enabled: false` in
  `charts/zac` itself — production PodiumD deployments must also point at an
  external/plain Solr rather than enabling the operator by default. This
  project uses the plain-Solr path.
- **No local/minikube/kind dev guide exists anywhere in PodiumD** (checked
  `README.md` — pure changelog — and all of `docs/`). This project's plan is
  original design work, not adapted from an existing PodiumD dev-cluster
  recipe.

## Image version comparison: PodiumD chart defaults vs. this project's compose-pinned targets

| Component | PodiumD chart default (4.8.1) | docker-compose.yaml pin (this project's target) |
|---|---|---|
| Open Zaak | `openzaak/open-zaak:1.27.3` | `1.29.1` |
| Open Notificaties | `openzaak/open-notificaties:1.16.0` | `1.15.0` |
| Objecten (objects-api) | `3.6.0` | `3.6.1` |
| Objecttypen (objecttypes-api) | `maykinmedia/objecttypes-api:3.4.2` | `3.4.2` (match) |
| Open Archiefbeheer | `2.0.0` | `2.0.0` (match) |
| Open Klant | `2.15.0` | `2.15.0` (match) |
| Open Formulieren | `3.4.9` | `3.5.4` |
| brp-personen-mock | `ghcr.io/brp-api/personen-mock:2.7.0-202606230850` (via images doc, not values.yaml) | `2.7.0` |
| pabc-api / pabc-migrations | `1.1.0` / `1.1.0` (PodiumD repoints to an internal ACR mirror; true upstream is `ghcr.io/platform-autorisatie-beheer-component/*`) | `1.1.0` / `1.1.0` (match, using the real upstream ghcr.io repo) |

Chart version and app image tag are independent — this project overrides
`image.tag` explicitly per dependency regardless of what the packaged chart
version defaults to, so none of the above deltas block reuse.

## `helm show values` findings (live, verified against `@maykinmedia`/`@dimpact`/OCI repos)

Checked directly: `maykinmedia/openzaak` (as the representative member of the
shared Maykin Django-app chart family — openklant/objecten/objecttypen/
opennotificaties/openarchiefbeheer/openforms all showed the identical
structural shape via `helm search repo -l` + targeted `helm show values`
greps), `oci://ghcr.io/platform-autorisatie-beheer-component/pabc` (1.1.0),
and `dimpact/brp-personen-mock` (1.2.9).

- `openzaak` Chart.yaml dependencies: only `redis` (bitnami, tag `22.0.1`,
  gated by `tags: [redis]`) — **no Postgres subchart at all**. Confirms every
  Maykin-family chart expects an always-external database via
  `settings.database.{host,port,username,password,name,sslmode,dbConnMaxAge,
  dbPool.*}`. `global.settings.databaseHost` exists as a shortcut that
  overrides just `settings.database.host` across every subchart it's set on
  — not used here since each app needs a distinct db name/user on the same
  shared host, not just a shared host.
- `pabc` values (lines ~120-215): `settings.database.{host,port,username,
  password}` (empty by default, host int entionally blank), `settings.
  apiKeys`, `settings.oidc.{authority,clientId,clientSecret,pkceEnabled,
  functioneelBeheerderRole}`, `settings.keycloakAdmin.{clientId,clientSecret}`,
  its own native `ingress.{enabled,className,hosts,tls}` +
  `extraIngress` block, and (unlike the Maykin family) a real
  `postgresql.enabled: true` toggle (bitnami `postgresql` subchart,
  `bitnamilegacy/postgresql` image) — needs explicit `postgresql.enabled:
  false` for the shared-Postgres design.
- `brp-personen-mock` (1.2.9) full values.yaml is short: `image.{repository:
  ghcr.io/brp-api/personen-mock, tag: 2.7.0-202606230850}`, `service.{type:
  ClusterIP, port: 5010}`, `resources.requests.{cpu: 10m, memory: 150Mi}`,
  and — critically — `nameOverride: "brp-personen-mock"` **already set by
  default**. Its `templates/service.yaml` names the Service via the
  `brppersonenmock.name` helper (`{{ default .Chart.Name .Values.nameOverride
  }}`), not the usual release-prefixed `.fullname` helper — so the Service is
  already named exactly `brp-personen-mock` on port `5010` out of the box,
  with zero overrides needed to match the vendored `brp-personen-wiremock`
  mapping's hardcoded `proxyBaseUrl: http://brp-personen-mock:5010`.
- Every Maykin-family chart + `charts/zac` itself ships the identical
  `helm create`-scaffold `ingress.{enabled,className,annotations,hosts,tls}`
  block (confirmed on openzaak, pabc, and by reading `charts/zac/values.yaml`
  lines 278-293 directly) — this is why the plan uses each subchart's own
  native ingress instead of writing raw Ingress templates for them.
- `flower.enabled` (Celery monitoring UI) true-by-default on openklant/
  objecten/openarchiefbeheer/openforms, already false on openzaak/
  opennotificaties.
- `replicaCount` defaults to `2` on openzaak/openklant/objecten/objecttypen/
  opennotificaties/openforms; already `1` on openarchiefbeheer/pabc.
- `tags.redis: true` + `redis.architecture: standalone` (bitnami) on every
  Maykin-family chart by default — each needs `tags.redis: false` plus its
  cache/celery DSN pointed at the shared Redis instance instead (exact DSN
  field name per app not yet individually confirmed — flagged in plan.md as a
  step-3/5 follow-up).

## Traefik ingress pattern (from `podiumd-infra`)

`podiumd-infra/docs/ingress.md` + `scripts/create-traefik-ingress.sh` describe
the production pattern this project's Traefik usage is adapted from: Traefik
as `ingressClassName: traefik`, `traefik.ingress.kubernetes.io/router.
entrypoints: websecure` annotation for TLS, one shared cert-manager
`Certificate` covering every app hostname as a SAN (avoids Let's Encrypt rate
limits). None of the TLS/cert-manager/Let's Encrypt machinery applies here —
this project uses plain HTTP (`web` entrypoint) since it's local-only, but
keeps the same `ingressClassName: traefik` convention and per-service
`<name>.local` hostnames. Traefik itself must be installed once via Helm
(`helm repo add traefik https://traefik.github.io/charts` + `helm upgrade
--install traefik traefik/traefik -n traefik --create-namespace`) — treated as
a cluster prerequisite, not something this chart manages, matching how
`podiumd-infra` treats it as pre-installed cluster infra rather than an
app-chart dependency.

## Postgres seed-script mechanics (verified directly against the vendored source files)

`docker-compose.yaml` mounts `scripts/docker-compose/imports/openzaak-database/`
(and the openklant-database/openarchiefbeheer-database equivalents) at
`/docker-entrypoint-initdb.d` on each service's own Postgres container. Each
directory's **top-level** `init.sh` (a real file directly in that directory,
so Postgres's own entrypoint auto-runs it once on first init) simply
backgrounds a nested `database/fill-data-on-startup.sh`:

```sh
sh /docker-entrypoint-initdb.d/database/fill-data-on-startup.sh &
```

Each `fill-data-on-startup.sh` polls *that service's own database* with a
different readiness marker, then applies its numbered SQL fixture files:

- **openzaak**: polls `select count(id) from accounts_user where username =
  'admin'` until it equals `1`, then runs `/docker-entrypoint-initdb.d/
  database/*.sql` via `psql -U openzaak openzaak -v BAG_API_CLIENT_MP_REST_URL=
  "$BAG_API_CLIENT_MP_REST_URL" -v BAG_API_KEY="$BAG_API_KEY" -f $file`
  (fixture SQL references these as psql variables).
- **openklant**: polls `select count(*) from django_migrations` until it
  equals a hardcoded expected count (`176`, with an explicit code comment that
  this must be updated if Open Klant's migration count changes in a future
  version), then runs its numbered SQL files the same way.
- **openarchiefbeheer**: identical pattern, expected count `154`.

Since Postgres only runs `docker-entrypoint-initdb.d` once for the whole
cluster (not once per logical database), the single-shared-Postgres design in
`plan.md` merges these three into one combined `01-seed-fixtures.sh` — same
per-service readiness queries and vendored fixture SQL, parameterized by
db/user instead of assuming the single default database per container.

`zac-database/init-zac-database.sql` is much simpler — a one-shot schema/grant
script (creates the `flowable` and `zaakafhandelcomponent` schemas, grants
`zac` the right privileges) with no polling/readiness logic, since it doesn't
depend on any app having already run migrations.

## Keycloak realm JSON specifics (verified directly)

`scripts/docker-compose/imports/keycloak/realms/zaakafhandelcomponent-realm.json`
(~100K):

- Contains **no** `${env.*}` placeholders anywhere — none of the
  `ZAC_*_TEST_*_EMAIL_ADDRESS` environment variables that
  `docker-compose.yaml` passes to the `keycloak` container are actually
  consumed by Keycloak's realm-import mechanism. Safe to pass through to this
  project's Keycloak Deployment for parity, but not required for correctness.
- `zaakafhandelcomponent` client: `redirectUris: ['http://localhost:8080/*',
  'http://localhost:4200/*', 'http://host.docker.internal:4200/*',
  'http://host.docker.internal:8080/*']`, matching `webOrigins`. No entry for
  `zac.local`.
- `pabc` client: `redirectUris: ['http://localhost:8000/*',
  'http://localhost:8006/*', 'http://host.docker.internal:8006/*',
  'http://host.docker.internal:8000/*']`, matching `webOrigins`. No entry for
  `pabc.local`.
- `zaakafhandelcomponent-admin-client` and `pabc-admin-client` both have empty
  `redirectUris`/`webOrigins` — these are service-account/client-credentials
  clients (server-to-server only), unaffected by any hostname change.

`plan.md`'s vendoring step patches (doesn't blindly copy) the
`zaakafhandelcomponent` and `pabc` clients' `redirectUris`/`webOrigins` to
additionally include `http://zac.local/*` / `http://zac.local` and
`http://pabc.local/*` / `http://pabc.local`, appended alongside the existing
entries.

## openzaak's fake-test-document.pdf startup step (verified directly)

`docker-compose.yaml`'s `openzaak.local` service overrides `command` to
`scripts/docker-compose/imports/openzaak/zac-scripts/
copy-test-pdf-and-start-openzaak.sh`, which does:

```sh
mkdir -p /app/private-media/uploads/2023/10 && cp /fake-test-document.pdf /app/private-media/uploads/2023/10/
mkdir -p /app/private-media/uploads/2023/11 && cp /fake-test-document.pdf /app/private-media/uploads/2023/11/
mkdir -p /app/private-media/uploads/2023/12 && cp /fake-test-document.pdf /app/private-media/uploads/2023/12/
/start.sh
```

(comment in the script: "multiple copies for multiple `product aanvragen`",
referencing `06-setup-zac-config-after.sql`). Since the reused `openzaak`
subchart has its own entrypoint, `plan.md` replaces this with an
initContainer that mounts the vendored PDF + the same persistence volume and
performs the same three copies, letting the main container start via the
subchart's normal entrypoint unmodified.

## Depending on `dimpact/podiumd` directly (architecture switch)

After the initial plan (direct dependencies on the ~10 individual ZGW app
charts), the user asked to switch to depending on the published `podiumd`
umbrella chart itself as this chart's single Helm dependency, so this
project's own Chart.yaml/templates contain only the minikube-specific deltas.
Verified directly: `dimpact/podiumd` is published (`helm search repo dimpact
-l` lists versions back to `1.0.0`, current `4.8.1`), so `helm repo add
dimpact https://Dimpact-Samenwerking.github.io/helm-charts/` — the same repo
already used for `brp-personen-mock` — is sufficient; no separate repo for
podiumd itself.

### `enabled` defaults across all of podiumd 4.8.1's dependencies

Pulled the chart (`helm pull dimpact/podiumd --version 4.8.1 --untar`) and
read `Values.<dep>.enabled` for every dependency in `Chart.yaml`:

| dependency | `enabled` default | action needed |
|---|---|---|
| `keycloak-operator` | `true` | disable — we run plain Keycloak |
| `clamav` | unset (falsy) | already off |
| `brppersonenmock` | unset (falsy) | **enable** — core |
| `openzaak` | unset (falsy) | **enable** — core |
| `opennotificaties` | unset (falsy) | enable only for its profile |
| `objecten` | unset (falsy) | enable only for its profile |
| `objecttypen` | unset (falsy) | enable only for openformulieren profile |
| `openarchiefbeheer` | `false` (explicit) | enable only for its profile |
| `openklant` | unset (falsy) | **enable** — core |
| `openformulieren` (alias of `openforms`) | unset (falsy) | enable only for its profile |
| `openinwoner` | unset (falsy) | already off |
| `referentielijsten` | `false` (explicit) | already off |
| `openbeheer` | `false` (explicit) | already off |
| `zac` (alias of `zaakafhandelcomponent`) | `true` | keep — set explicitly anyway |
| `zaakbrug` | `false` (explicit) | already off |
| `zgw-office-addin` | `true` | disable — unrelated to ZAC |
| `ita` | `true` | disable — unrelated to ZAC |
| `kiss` | `true` | disable — unrelated to ZAC |
| `pabc` | `true` | keep — set explicitly anyway |
| `omc` | `false` (explicit) | already off |
| `redis-operator` | `true` | disable — we run plain Redis |
| `apisix` | `false` (explicit) | already off |
| `eck-operator` | `true` | disable — unrelated (Elasticsearch operator for kiss) |
| `kiss-eck` | `true` | disable — tied to kiss |

In Go templates a missing/`nil` value is falsy, so "unset" and "false" behave
identically in `{{ if .Values.x.enabled }}` guards — the distinction only
matters for documentation clarity, not behavior.

### Template-level verification that disabling actually no-ops cleanly

Checked the templates that reference `keycloak-operator`/`redis-operator`
directly (16 files: `keycloak-cr.yaml`, `keycloak-ensure-operator-sa.yaml`,
`keycloak-import-master-realm-job.yaml`, `keycloak-import-podiumd-realm-job.yaml`,
`keycloak-master-realm-config.yaml`, `keycloak-podiumd-realm-config.yaml`,
`keycloak-podiumd-realm-secrets.yaml`, `keycloak-secrets.yaml`,
`keycloak-operator-client-secret.yaml`, `keycloak-operator-servicemonitor-rbac.yaml`,
`keycloak-ensure-podiumd-admin-user.yaml`, `redis-ha.yaml`,
`redis-ha-label-master.yaml`, `redis-ha-podmonitor.yaml`,
`redis-ha-pre-delete.yaml`, `validations.yaml`). Spot-checked
`keycloak-cr.yaml` (`{{- if (index .Values "keycloak-operator").enabled }}`
as its very first line) and `redis-ha.yaml` (`{{- if and $redisOperator.enabled
$redisHa.enabled }}`) — both guard correctly. `apisix-etcd.yaml` similarly
guards on `.Values.apisix.enabled`. `create-required-catalogi.yaml` and
`create-required-objecttypen.yaml` are guarded by their own app's `.enabled`
AND a job-specific sub-flag (`openzaak.create_required_catalogi_job.enabled`,
`objecttypen.create_required_objecttypen_job.enabled`) — these are optional
extras (declarative catalog/objecttype seeding via the OpenZaak/Objecttypen
API) that could in principle complement or replace some of our own vendored
seed SQL, but they almost certainly produce a minimal/generic "zac" catalog
for production bootstrapping rather than the exact zaaktype-test-1/2/3 +
BPMN test fixtures docker-compose seeds — not investigated further, left
disabled (default) for now, our own vendored SQL fixtures cover this instead.

`values.schema.json` does not exist for this chart — no separate schema-level
validation to worry about beyond the in-template `fail` calls in
`validations.yaml` (which only trigger for the monitoring OIDC secret and ITA
medewerker-objecttype cases, both irrelevant once `keycloak-operator` and
`ita` are disabled).

### The Azure-CSI storage template problem (the one real blocker)

`templates/openzaak-storage.yaml` (and, by the identical filename pattern,
`openklant-storage.yaml`, `opennotificaties-storage.yaml`,
`openarchiefbeheer-storage.yaml`, `openformulieren-storage.yaml`,
`openinwoner-storage.yaml`, `referentielijsten-storage.yaml`,
`openbeheer-storage.yaml` — `objecten`/`objecttypen`/`pabc`/
`brp-personen-mock` have no such template, unaffected) creates a raw
`PersistentVolume` with:

```yaml
annotations:
  pv.kubernetes.io/provisioned-by: file.csi.azure.com
spec:
  storageClassName: {{ .Values.openzaak.persistentVolume.storageClassName | default "podiumd-standard" }}
  csi:
    driver: file.csi.azure.com
    volumeHandle: ...
    volumeAttributes:
      shareName: ...
    nodeStageSecretRef:
      name: ...
      namespace: ...
```

— the CSI driver itself, not just the storage class name, is Azure Files
specific (`file.csi.azure.com`). No minikube cluster has this CSI driver
installed, so this PV can never bind there regardless of what
`storageClassName` is overridden to.

The template's top guard is:
```
{{- if or .Values.openzaak.enabled (not (hasKey .Values.openzaak "enabled")) -}}
```
— note the `(not (hasKey ...))` fallback: if the `enabled` key is *absent*
entirely, this defaults to *rendering the storage template anyway*, even
though the actual subchart `condition: openzaak.enabled` (a plain truthiness
check, no such fallback) would NOT install the openzaak app itself in that
same scenario. So leaving `enabled` unset does not avoid this template. Once
we explicitly set it `true` to get the app, the same condition obviously
still fires.

The fix relies on this template's other property: both the PV and its
matching PVC are wrapped in `{{- if not (lookup "v1" "PersistentVolume"/
"PersistentVolumeClaim" ...) }}` — genuinely idempotent, skips creation if an
object with that exact name already exists live in the cluster.
`lookup` queries the *live* cluster at render time, not the set of objects
being rendered in the same `helm install`/`helm template` invocation — so our
own same-name PV/PVC can't just be regular templates in this chart (on a
first install, both would render in the same pass with neither visible to
the other's `lookup` yet, risking either a duplicate-resource collision or a
race depending on apply ordering). Annotating our own PV/PVC as Helm
`pre-install,pre-upgrade` hooks solves this cleanly: Helm hooks are a
genuinely separate, ordered phase that completes *before* the release's
regular manifests (including podiumd's nested `openzaak-storage.yaml`, which
is not itself a hook) are applied. By the time podiumd's `lookup` runs, ours
already exists, and it no-ops. `helm.sh/resource-policy: keep` on our hook
objects prevents them from being deleted on hook cleanup or `helm uninstall`.
No extra Job/RBAC is required — PV/PVC objects can carry hook annotations
directly, they don't need a pod to run.

## Pre-provisioned PV/PVC: naming, access mode, and storage class (verified directly)

Diffed `openklant-storage.yaml` against `openzaak-storage.yaml` —
byte-for-byte identical except the app name substituted throughout — so the
fix generalizes cleanly across every `<app>-storage.yaml` template, not just
openzaak.

Checked `podiumd`'s own `values.yaml` defaults for `persistence.*` on every
affected app:

| app | `persistence.existingClaim` | `persistence.size` |
|---|---|---|
| openzaak | `openzaak` | `10Gi` |
| openklant | `openklant` | `10Gi` |
| objecten | `objecten` | `10Gi` |
| opennotificaties | `opennotificaties` | `10Gi` |
| openarchiefbeheer | `openarchiefbeheer` | `10Gi` |

All fixed, predictable, literally just the app's own name — no override
needed, our pre-provisioned PVC just needs to be named identically.

`openzaak-storage.yaml`'s PV/PVC both declare `accessModes: [ReadWriteMany]`
(appropriate for the real target, Azure Files — a network filesystem).
Minikube's default `standard` StorageClass is backed by
`k8s.io/minikube-hostpath` (the `storage-provisioner` addon), which only
really supports `ReadWriteOnce` — node-local storage, no real multi-node
concurrent-writer capability. Since we control both ends of our own
pre-provisioned pair, and every app here runs `replicaCount: 1` (no genuine
concurrent-writer scenario), using `ReadWriteOnce` is simpler and honest
about what's actually being provided, rather than attempting to declare
`ReadWriteMany` on what's really just a single hostPath directory.

Considered and rejected: referencing `storageClassName: standard` directly on
these specific PV/PVC pairs. Minikube marks `standard` as the cluster's
*default* StorageClass — a PVC that references it (or omits
`storageClassName` entirely) is dynamically provisioned against that class,
which creates a **new**, differently-named PV rather than binding to our
specific pre-created one. Since the whole point of this mechanism is for our
PV to exist *under a specific, predictable name* (`<namespace>-<app>`, so
podiumd's `lookup` check finds it), dynamic provisioning would silently
defeat that — we'd end up with an extra, correctly-bound pair (ours) *and* an
orphaned Azure PV from podiumd's own template (since its `lookup` for a PV
named `<namespace>-<app>` specifically would still return nothing, our
dynamically-provisioned one having some auto-generated name instead). Using
an explicit empty string for `storageClassName` on both our PV and PVC avoids
StorageClass-driven dynamic provisioning entirely and forces Kubernetes'
plain static 1:1 name/capacity/accessMode matching instead — which is what
guarantees our PVC binds specifically to our own PV. (Note: this distinction
only matters for these specific pre-provisioned pairs, which are racing
against podiumd's own same-named-object `lookup` checks. The shared
Postgres/Solr/Grafana PVCs elsewhere in this chart have no such collision to
avoid and can just reference `standard` directly via normal dynamic
provisioning.)

## PABC migrations ordering (verified directly — no gap)

`pabc`'s `templates/migration-job.yaml` is a plain `batch/v1` Job (no Helm
hook annotation), named `{{ include "pabc.fullname" . }}-migrations-{{
.Release.Revision }}` (so it re-runs — presumably idempotently — on every
`helm upgrade`, since the revision-suffixed name changes each time).
`templates/deployment.yaml` has its own initContainer
(`{{ .Chart.Name }}-wait-for-migrations`) that references that exact Job name
and blocks the main container from starting until it completes — the same
`groundnuty/k8s-wait-for`-style pattern referenced in `podiumd-infra`'s
scripts. This mirrors compose's `depends_on: pabc-migrations: condition:
service_completed_successfully` correctly, entirely within the `pabc` chart
itself. Nothing extra needed from this project for PABC's database-init
ordering.
