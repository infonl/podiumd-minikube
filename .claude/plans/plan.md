# Convert docker-compose stack to a Helm chart for minikube

## Context

`docker-compose.yaml` + `docker-compose.override.yml` (in
`dimpact-zaakafhandelcomponent`) define the full local dev stack for ZAC:
~40 services gated behind compose profiles (`zac`, `itest`, `objecten`,
`opennotificaties`, `openarchiefbeheer`, `openformulieren`, `metrics`). We want
a Helm chart that reproduces this stack on minikube, for local
Kubernetes-based development/testing instead of Docker Compose.

Rather than hand-write Kubernetes manifests for every ZGW component (Open
Zaak, Open Klant, Objecten, Open Formulieren, ...), **this chart depends on
the published `podiumd` umbrella chart** (`dimpact/podiumd`, from
`https://Dimpact-Samenwerking.github.io/helm-charts/`) as its single Helm
dependency, and disables the parts of it that are Azure-production-specific
(Keycloak Operator, Redis Operator HA, APISIX, ClamAV, and the unrelated
non-ZAC apps it also bundles: ITA, KISS, OpenInwoner, Referentielijsten,
OpenBeheer, Zaakbrug, zgw-office-addin, OMC, ECK). What's left standing —
openzaak, openklant, objecten, objecttypen, opennotificaties,
openarchiefbeheer, openforms, pabc, brp-personen-mock, and zac itself — is
exactly the set of components `docker-compose.yaml` also runs, all wired
through PodiumD's own values conventions.

This was a deliberate choice after comparing it against directly depending on
each of those ~10 subcharts ourselves (see `research-notes.md` for the
comparison): depending on the whole `podiumd` chart means `helm dependency
update` also fetches ~15 subcharts we'll never render (still real cost — see
below), but it means our own chart genuinely only contains the changes and
extras needed for minikube — everything else is "disable this, override
that" against a single upstream dependency, one line in `Chart.yaml`.

`dimpact-zaakafhandelcomponent`'s own production chart, `charts/zac`, comes
along automatically as `podiumd`'s own `zac` dependency (aliased `zac`,
enabled by default) — it already provides the ZAC app, OPA (as a pod
sidecar), the office-converter (Gotenberg) container, and a `solr.url` +
`createZacCore` mechanism that lets ZAC create its own Solr core against any
external Solr instance — exactly matching compose's plain
`solr:9.10.1-slim` + `solr-precreate` container. **Caveat**: because it's a
nested dependency of `podiumd`, we don't independently choose the `zac` chart
version — we get whatever version the chosen `podiumd` release itself pins
(see "PodiumD dependency version" below). Testing a newer `charts/zac`
release means testing a newer `podiumd` release, not bumping `zac`
independently.

## Standalone project: this repo (`podiumd-minikube`)

This chart does **not** live inside `dimpact-zaakafhandelcomponent`. It is
this independent git repository, so that all output — the chart itself, its
plan/design notes, and any future memory this project accumulates — lives in
one self-contained place instead of being scattered across
`dimpact-zaakafhandelcomponent`'s `charts/` directory.

- **Every file asset this chart needs from `dimpact-zaakafhandelcomponent` is
  physically copied into this repo** at build time, so this project never
  reaches across repos at runtime or at `helm template`/`helm install` time:
  - `scripts/docker-compose/imports/keycloak/realms/zaakafhandelcomponent-realm.json`
  - `scripts/docker-compose/imports/{brp-personen-wiremock,smartdocuments-wiremock,kvk-wiremock,bag-wiremock}/{mappings,__files}`
  - `scripts/docker-compose/imports/{zac-database,openzaak-database,openklant-database,opennotificaties-database,openarchiefbeheer-database}/...` (init/seed SQL)
  - `scripts/docker-compose/imports/{otel-collector,tempo,prometheus,grafana}/*.yaml` (metrics profile configs)
  - `src/test/resources/policies` + `src/main/resources/policies` (opa-tests profile)
  These land under `vendor/dimpact-zaakafhandelcomponent/...` with a short
  `NOTES.md` recording the exact source commit/path they were copied from, so
  future re-syncs are traceable. **Not yet done** — see Status below.

### Target chart layout

This repo *is* the chart (no nested `charts/<name>` wrapper needed, since
there's nothing else in the repo): `Chart.yaml`, `values.yaml`, `templates/`,
`vendor/` all live at the repo root.

## Dependency: `podiumd`

```yaml
# Chart.yaml
dependencies:
  - name: podiumd
    repository: "@dimpact"    # https://Dimpact-Samenwerking.github.io/helm-charts/
    version: "4.8.1"           # see "PodiumD dependency version" below for how to change this
    condition: podiumd.enabled  # always true — this is our only real dependency
```

All configuration for every ZGW component now nests one level deeper, under
`podiumd.<component>.*` in our own `values.yaml` (e.g.
`podiumd.openzaak.settings.database.host`, `podiumd.zac.contextUrl`), since
they're podiumd's nested dependencies, not ours directly.

### PodiumD dependency version — easily configurable

Helm dependency versions are a `Chart.yaml`-level concept, not a
`values.yaml`-level one — they can't be templated or overridden at
`helm install`/`--set` time. To still make "test a different podiumd version"
a one-line operation instead of a manual edit-and-remember-to-update-deps
dance, this repo ships `scripts/set-podiumd-version.sh`:

```sh
#!/usr/bin/env bash
set -euo pipefail
NEW_VERSION="${1:?Usage: set-podiumd-version.sh <version>}"
sed -i.bak "s/version: \"[^\"]*\"  *# podiumd version/version: \"${NEW_VERSION}\"  # podiumd version/" Chart.yaml
rm -f Chart.yaml.bak
helm dependency update
echo "podiumd dependency set to ${NEW_VERSION}; helm dependency update re-run."
```

Chart.yaml's `version:` line for the `podiumd` dependency carries the
`# podiumd version` trailing comment specifically so this sed is unambiguous
even though today it's the only dependency in the file. Usage:
`./scripts/set-podiumd-version.sh 4.7.8 && helm template . | ...`.
`helm search repo dimpact/podiumd -l` lists every available version.

### What has to be explicitly disabled (defaults to `enabled: true` in podiumd)

Verified directly (pulled `dimpact/podiumd` 4.8.1 and inspected
`values.yaml` + `Chart.yaml` + templates):

```yaml
podiumd:
  keycloak-operator:
    enabled: false   # we run plain Keycloak instead — see Raw templates
  redis-operator:
    enabled: false   # we run plain Redis instead — see Raw templates
  apisix:
    enabled: false    # already false by default, set explicitly for clarity
  zgw-office-addin:
    enabled: false
  ita:
    enabled: false
  kiss:
    enabled: false
  kiss-eck:
    enabled: false
  eck-operator:
    enabled: false
  omc:
    enabled: false    # already false by default
  clamav:
    enabled: false    # already unset/false by default
  openinwoner:
    enabled: false    # already unset/false by default
  referentielijsten:
    enabled: false    # already false by default
  openbeheer:
    enabled: false    # already false by default
  zaakbrug:
    enabled: false    # already false by default
```

Each of these is properly guarded internally (e.g. `keycloak-cr.yaml` starts
with `{{- if (index .Values "keycloak-operator").enabled }}`,
`redis-ha.yaml` with an equivalent check) — disabling cleanly removes the
templates. The real cost isn't correctness, it's that **`helm dependency
update` still downloads all of these subchart archives into `charts/`
regardless of `condition:`** (conditions only gate rendering, not fetching) —
accepted as the trade-off for a single upstream dependency.

### What has to be explicitly enabled (defaults to unset/falsy in podiumd)

```yaml
podiumd:
  openzaak:
    enabled: true      # always on (core)
  openklant:
    enabled: true      # always on (core)
  brppersonenmock:
    enabled: true      # always on (core)
  # zac.enabled and pabc.enabled already default to true — set explicitly anyway
  # for robustness against a future podiumd version changing that default.
  zac:
    enabled: true
  pabc:
    enabled: true
  # profile-gated — only flipped true when the matching top-level profile flag is set:
  objecten:
    enabled: false        # "objecten"/"openformulieren" profile
  objecttypen:
    enabled: false         # needed by "openformulieren" profile
  opennotificaties:
    enabled: false    # "opennotificaties"/"openformulieren" profile
  openarchiefbeheer:
    enabled: false    # "openarchiefbeheer" profile
  openformulieren:
    enabled: false      # "openformulieren" profile
```

### Verified `helm show values`/`helm pull` findings (unchanged facts, now nested under `podiumd.*`)

- **Postgres**: the Maykin family (openzaak/openklant/objecten/objecttypen/
  opennotificaties/openarchiefbeheer/openforms) has **no bundled Postgres at
  all** — always expects an external database via
  `podiumd.<app>.settings.database.{host,port,username,password,name}`. No
  `postgresql.enabled` toggle for these. **pabc** is the exception — it *does*
  bundle `postgresql` (bitnami) and needs `podiumd.pabc.postgresql.enabled:
  false` + its own `settings.database.{host,port,username,password}` pointed
  at the shared instance instead.
- **Redis**: every Maykin-family chart bundles its own Redis by default
  (`tags.redis: true`, `redis.architecture: standalone`). Set
  `podiumd.<app>.tags.redis: false` on all of them and point their
  cache/celery Redis DSN settings at the one shared Redis instance instead
  (exact per-app DSN field name confirmed per chart at implementation time).
- **`flower`** defaults to `enabled: true` on
  openklant/objecten/openarchiefbeheer/openforms — set
  `podiumd.<app>.flower.enabled: false` explicitly across the board.
- **Ingress**: every one of these charts (and `zac`) ships its own native
  `ingress.{enabled,className,hosts,tls}` block — expose via
  `podiumd.<app>.ingress.{enabled: true, className: traefik, hosts: [...]}`,
  no raw Ingress template needed for any of them.
- **`replicaCount`** defaults to `2` on most of the family — set
  `podiumd.<app>.replicaCount: 1` + `autoscaling.enabled: false` everywhere.
- **OIDC client secrets — only zac and pabc, corrected.** PodiumD's own
  `keycloak-podiumd-realm-secrets.yaml` auto-generates an OIDC client secret
  for every app whenever neither `configuration.secrets.keycloak_client_secret`
  nor `configuration.oidcSecret` is explicitly set, regardless of whether
  `keycloak-operator` is enabled. An earlier version of this plan said "every
  relevant app" needs this set explicitly — **checked the vendored realm.json
  directly and that's wrong**: it only defines OIDC clients for
  `zaakafhandelcomponent`(+`-admin-client`) and `pabc`(+`-admin-client`) —
  nothing for openzaak/openklant/objecten/objecttypen/opennotificaties/
  openarchiefbeheer/openformulieren, because compose never wires Keycloak
  OIDC for any of them (they use Django's own local admin login, or in
  openzaak's case a separate ZGW JWT client-credentials mechanism that has
  nothing to do with Keycloak despite the similar-sounding secret name —
  `ZGW_API_SECRET=openzaakZaakafhandelcomponentClientSecret` is a ZGW API
  client secret registered in OpenZaak's own database via the vendored seed
  SQL, not a Keycloak client secret). So only two apps need real,
  compose-matching values: `podiumd.zac.auth.secret` =
  `keycloakZaakafhandelcomponentClientSecret` and `podiumd.pabc.oidc.
  clientSecret` = `pabcClientSecret` (plus each app's separate Keycloak
  *admin*-client secret: `podiumd.zac.keycloak.adminClient.secret` =
  `zaakafhandelcomponentAdminClientSecret`, `podiumd.pabc.keycloakAdmin.
  clientSecret` = `pabcAdminClientSecret`). Every other app is left with no
  OIDC config at all, matching compose — podiumd's auto-generated secret for
  them sits unused (nothing references it, since we don't add an OIDC
  `configuration.data` block for apps that don't have one in compose).

### PodiumD's Azure-CSI storage templates — the one real blocker, and its fix

**Confirmed directly** (pulled and read `templates/openzaak-storage.yaml`):
podiumd has a `<app>-storage.yaml` template for openzaak, openklant,
opennotificaties, openarchiefbeheer, openformulieren, openinwoner,
referentielijsten, and openbeheer (objecten/objecttypen/pabc/brp-personen-mock
don't have one — unaffected). Each one **unconditionally creates a raw
`PersistentVolume` hardcoding the Azure Files CSI driver**
(`csi: {driver: file.csi.azure.com, ...}`) — not just a storage-class
reference, the whole PV spec is Azure-only. Setting `storageClassName` does
**not** fix this; there is no values-only way to make the PV itself portable.

Two details make this fixable rather than a dead end:
1. The template's guard is `{{- if or .Values.openzaak.enabled (not (hasKey
   .Values.openzaak "enabled")) }}` — once we explicitly set
   `podiumd.openzaak.enabled: true` (which we must, to get the app itself),
   this always fires. There's no separate toggle to keep the app but suppress
   just its storage template.
2. Both the PV and its matching PVC creation are individually wrapped in
   `{{- if not (lookup "v1" "PersistentVolume"/"PersistentVolumeClaim" ...) }}`
   — i.e. **idempotent**: if an object with that exact name already exists in
   the cluster, podiumd's own template skips creating it.

The fix: **this chart pre-provisions its own minikube-compatible
`PersistentVolume` + `PersistentVolumeClaim`**, named exactly what podiumd
expects. Confirmed directly (diffed `openklant-storage.yaml` against
`openzaak-storage.yaml` — byte-for-byte identical except the app name, so
this generalizes cleanly, not just an openzaak-specific fix) that
`persistence.existingClaim` and `persistence.size` already default to fixed,
predictable values in podiumd's own `values.yaml` — literally just the app
name (`openzaak`, `openklant`, `objecten`, `opennotificaties`,
`openarchiefbeheer`) and `10Gi` — so there's nothing to invent: our PV is
named `<namespace>-<app>` and our PVC is named `<app>`, mirroring those
existing defaults exactly.

Two details matter for how these are actually backed, both different from
the shared Postgres/Solr/Grafana PVCs elsewhere in this chart (which have no
competing template racing to create a same-named object, so they can just use
the `standard` StorageClass normally via dynamic provisioning):
- **`accessModes: [ReadWriteOnce]`, not `ReadWriteMany`.** PodiumD's own
  (unwanted) PV declares `ReadWriteMany` — appropriate for Azure Files, not
  for minikube's hostPath-backed `standard` class, which only really supports
  `ReadWriteOnce`. Since every app here runs at `replicaCount: 1`, there's no
  genuine multi-writer need — we control both ends of this pair, so we use
  `ReadWriteOnce` and don't try to fake RWX.
- **`storageClassName: ""` (explicit empty string), not `standard`.** Minikube
  marks `standard` as the cluster's *default* StorageClass — a PVC that
  references it (or omits `storageClassName` entirely) triggers **dynamic**
  provisioning against that class, which would create a fresh,
  differently-named PV rather than binding to the specific one we
  pre-created. An explicit empty string on both our PV and PVC forces
  Kubernetes' static 1:1 matching instead, guaranteeing our PVC binds to
  *our* PV specifically. The PV's own volume source is a plain
  `hostPath: {path: ..., type: DirectoryOrCreate}`.

`lookup` only sees objects that already exist in the *live cluster* at
template-render time — it can't see objects from the same `helm install`
pass — so these can't just be regular templates in this chart (on a first
install, ours and podiumd's would render in the same pass and neither would
see the other yet, risking a name collision). Instead, **this chart's PV/PVC
templates are annotated as Helm `pre-install,pre-upgrade` hooks**
(`helm.sh/hook: pre-install,pre-upgrade`, `helm.sh/resource-policy: keep` so
they survive uninstalls). Helm hooks complete as a distinct phase *before*
any of the release's regular manifests — including podiumd's nested
`openzaak-storage.yaml` — are applied, so by the time podiumd's `lookup`
check runs, our PV/PVC already exist and it cleanly no-ops. No extra Job or
RBAC needed — PV/PVC objects can carry hook annotations directly.

This applies to every persistent app we actually enable: openzaak and
openklant from day one (both are "core"), and opennotificaties/
openarchiefbeheer/openformulieren later when their profiles are turned on —
each gets its own hook-annotated PV/PVC pair, following the same pattern
confirmed on openzaak/openklant.

**Also confirmed, no gap**: PABC's migrations (compose:
`depends_on: pabc-migrations: condition: service_completed_successfully`)
are already handled robustly by the `pabc` chart itself — its
`deployment.yaml` has a `wait-for-migrations` initContainer that polls the
migrations Job (`{{ include "pabc.fullname" . }}-migrations-{{
.Release.Revision }}`) by name before the main container starts. Nothing
extra needed from this chart for PABC's database initialization ordering.

## Raw templates (new, in `templates/`)

All file assets referenced below (`scripts/docker-compose/imports/...`,
policy directories) are the **vendored copies** under
`vendor/dimpact-zaakafhandelcomponent/` described above, not live references
into `dimpact-zaakafhandelcomponent`. These are unaffected by the switch to
depending on the whole `podiumd` chart — they're still raw templates we write
ourselves for pieces podiumd doesn't provide (or provides only via
Azure-specific operators we've disabled above).

- **Postgres — single shared instance.** One `postgis/postgis:17-3.4`
  Deployment + PVC + Service (PostGIS is a superset of plain Postgres, so it
  serves the non-spatial databases too), `storageClassName` left unset /set to
  minikube's default `standard` class.

  Verified detail (checked `init.sh`/`fill-data-on-startup.sh` for openzaak,
  openklant, and openarchiefbeheer directly): these are **not** passive seed
  files — `init.sh` is a genuine top-level `docker-entrypoint-initdb.d` script
  that Postgres auto-runs once on first init, and it backgrounds
  `fill-data-on-startup.sh`, which polls *that service's own database* (a
  different readiness marker per app — openzaak waits for
  `accounts_user` to contain `admin`; openklant/openarchiefbeheer instead wait
  for `django_migrations` to reach an exact row count, 176 and 154
  respectively) until the app's own migrations have finished, then applies
  that service's numbered SQL fixture files against its own db/user. Postgres
  only runs `docker-entrypoint-initdb.d` **once**, for the whole cluster — not
  once per logical database — so with one shared instance this can't stay
  three separate per-service scripts. The three are merged into:
  - `00-create-databases.sql` — creates all 9 databases + roles/passwords
    (zac, keycloak, openzaak, openklant, objecten, objecttypes,
    opennotificaties, openarchiefbeheer, pabc), matching
    `docker-compose.yaml`'s existing names/users/passwords, plus
    `zac-database/init-zac-database.sql`'s schema/grant statements for the
    `zac` database. **Extension-ordering risk found and fixed here**: the
    `postgis/postgis` image's own bundled `docker-entrypoint-initdb.d`
    scripts (which modify `template1` so that new databases inherit PostGIS
    automatically) are conventionally numbered to run early (e.g. `10_*.sh`),
    and Postgres runs these scripts in **alphabetical** order — so our own
    `00-create-databases.sql` would otherwise run *before* them, meaning the
    four databases that need PostGIS (openzaak, objecten, opennotificaties,
    openarchiefbeheer) would be created before `template1` has it, and would
    **not** inherit the extension. Rather than depend on script ordering,
    `00-create-databases.sql` explicitly runs `\c <dbname>` +
    `CREATE EXTENSION IF NOT EXISTS postgis;` for each of those four
    databases itself, right after creating them.
  - `01-seed-fixtures.sh` — one merged, backgrounded script containing the
    three existing per-service blocks unchanged (same readiness queries, same
    vendored numbered `*.sql` fixture files, now parameterized by db/user
    instead of assuming the single default database).
- **Keycloak** — plain `quay.io/keycloak/keycloak:26.6.4` Deployment + Service,
  run with `start-dev --import-realm`, importing a **patched copy** of
  `scripts/docker-compose/imports/keycloak/realms/zaakafhandelcomponent-realm.json`
  (100K, mounted via ConfigMap) — no Keycloak Operator/CRDs, no realm-import
  Jobs (podiumd's own keycloak-operator-based realm wiring is entirely
  disabled per above — this is a clean substitute, not a second competing
  Keycloak). `KC_DB` points at the shared Postgres.

  Verified detail: the realm JSON has no `${env.*}` placeholders (so none of
  the `ZAC_*_TEST_*_EMAIL_ADDRESS` env vars compose passes to the Keycloak
  container are actually consumed by realm import — safe to pass through
  for parity but not required for correctness). It **does**, however,
  hardcode `redirectUris`/`webOrigins` for the `zaakafhandelcomponent` and
  `pabc` clients to only `localhost:8080`/`localhost:4200`/
  `host.docker.internal:*` — nothing for the new `zac.local`/`pabc.local`
  Ingress hostnames. Left as-is, Keycloak would reject the OIDC redirect and
  login would fail outright. The vendoring step therefore **patches** (not
  copies verbatim) both clients' `redirectUris` and `webOrigins` arrays to
  append `http://zac.local/*` / `http://zac.local` and `http://pabc.local/*` /
  `http://pabc.local` alongside the existing entries (kept, not replaced, so a
  port-forward-based fallback still works too).
- **Redis** — plain single-container `redis:8.6.4` Deployment + Service, no
  persistence, no HA operator — every app's cache/celery DSN points at this
  one instance using the same DB-index convention as compose.
- **RabbitMQ** — plain single-container `rabbitmq:4.2.7-alpine` Deployment +
  Service (`opennotificaties`/`openformulieren` profile only).
- **Solr** — plain single-container `solr:9.10.1-slim` Deployment + Service +
  **PVC** (`storageClassName: standard`), mounted at `/var/solr`. Compose
  persists Solr's index via a bind-mounted `solr-data` volume — the original
  plan draft for this chart omitted the PVC entirely, which would have lost
  the "zac" core's index on every pod restart, forcing a full re-index each
  time. Fixed here: single-instance PVC, same as the shared Postgres.
  Wired into `podiumd`'s `zac` dependency via
  `podiumd.zac.solr.url: http://<solr-service>:8983` +
  `podiumd.zac.solr.createZacCore: true`, reusing that chart's existing
  initContainer instead of writing our own core-creation logic.
- **Wiremocks — one merged pod, not four.** Only `brp-personen-wiremock` is
  always-on in compose; `smartdocuments-wiremock`, `kvk-wiremock`, and
  `bag-wiremock` are all gated behind the `itest` profile. Rather than four
  separate `wiremock/wiremock:3.13.2` Deployments, this chart runs **one**
  WireMock pod that always mounts `brp-personen-wiremock`'s mappings/`__files`,
  and — only when `itest.enabled=true` — also mounts the other three sets as
  extra ConfigMap-backed directories (their URL patterns target distinct
  upstream APIs, so mapping sets don't collide). ZAC/tests reach the
  itest-only mappings through the same in-cluster Service, on the same host,
  differentiated by path. Content is small (20–108K per set) so ConfigMaps
  work directly, one key per mapping/file.
- **brp-personen-mock** wiring — `podiumd`'s `brp-personen-mock` dependency
  (aliased `brppersonenmock`) provides the personen-mock API itself;
  `brp-personen-wiremock` (raw template above) is the proxy/translation layer
  in front of it, exactly as in compose. The wiremock mapping
  `proxy-requests-with-headers.json` hardcodes `"proxyBaseUrl":
  "http://brp-personen-mock:5010"` — verified this resolves with **zero
  overrides needed**: the `brp-personen-mock` chart already ships
  `nameOverride: "brp-personen-mock"` by default, and its Service template
  keys off that name (not the usual release-prefixed fullname), so the
  in-cluster Service is already named exactly `brp-personen-mock` on port
  `5010`, matching the vendored mapping unchanged.
- **greenmail** (itest profile) — plain single-container Deployment + Service.
- **Metrics stack** (`metrics` profile) — plain single-container Deployments +
  Services for otel-collector, tempo, prometheus, grafana, each with a
  ConfigMap of its existing config file from
  `scripts/docker-compose/imports/{otel-collector,tempo,prometheus,grafana}/`.
  Grafana additionally gets a **PVC** (`storageClassName: standard`) mounted
  at `/var/lib/grafana` — compose persists `grafana-data` (dashboards, users,
  its own SQLite DB); omitting this (as the original plan draft did) would
  reset Grafana's state on every restart. Tempo/Prometheus/otel-collector
  have no persistent volumes in compose either — none added here, matching.
- **opa-tests** (itest profile) — a Helm `Job` running `opa test` against
  `src/test/resources/policies` + `src/main/resources/policies` (mounted via
  ConfigMap), mirroring the compose one-shot container.
- **Traefik Ingress — only for components with no chart of their own.** Every
  app that comes via the `podiumd` dependency already ships a native
  `ingress.{enabled,className,hosts,tls}` block — those are exposed by setting
  values (`podiumd.<app>.ingress.*`), not by writing templates. A **raw
  Ingress template** is only needed for the components this chart writes
  itself: Keycloak, the merged WireMock pod, Grafana (`metrics` profile), and
  greenmail (`itest` profile). Each gets hostname `<service>.local`,
  `ingressClassName: traefik`, plain HTTP (`web` entrypoint, no
  TLS/cert-manager — this is local dev only, unlike PodiumD's production
  Let's Encrypt setup documented in `podiumd-infra/docs/ingress.md`).

## Keycloak/ZAC issuer-URL consistency (no hostAliases needed)

Compose solves browser vs. container hostname mismatch via
`KC_HOSTNAME=http://host.docker.internal:8081` while ZAC's own
`AUTH_SERVER=http://keycloak:8080` stays internal. This works because
`AUTH_SERVER`/`auth.server` is only used to *fetch* the OIDC discovery
document; every actual endpoint the browser or backend calls thereafter
(`authorization_endpoint`, `token_endpoint`, `issuer`, ...) comes from *inside*
that discovery document, which is built from `KC_HOSTNAME`. Backend and
browser therefore never need to agree on which hostname to use — they only
need to agree with whatever Keycloak itself advertises.

Same pattern on minikube, without any `hostAliases` workaround:
- ZAC's `podiumd.zac.auth.server` → in-cluster Keycloak Service DNS
  (`http://keycloak:8080`), used only for backend token/introspection calls.
- Keycloak's own `KC_HOSTNAME` → the Traefik Ingress hostname
  (`http://keycloak.local`), which is what ends up in the discovery document
  and is what the browser is redirected to.
- User adds `keycloak.local` (and the other `*.local` Ingress hosts) to
  `/etc/hosts`, pointing at the Traefik ingress controller's address
  (`minikube tunnel` or the Traefik LoadBalancer/NodePort address).

Traefik itself is a **cluster prerequisite**, installed once via Helm
(`helm repo add traefik https://traefik.github.io/charts` +
`helm upgrade --install traefik traefik/traefik -n traefik --create-namespace`)
— not managed by this chart, matching how `podiumd-infra/docs/ingress.md`
treats Traefik as pre-installed cluster infra rather than an app-chart
dependency. This will be documented in a short README for the new chart.

## External reachability: every service compose exposed on a host port

The earlier drafts of this plan only explicitly assigned `.local` Ingress
hostnames to the services that also needed a self-referential URL fix
(zac, pabc, openarchiefbeheer, openformulieren) — openzaak, openklant,
objecten, objecttypen, and Solr's admin UI were left to "use the native
ingress block" without ever actually being assigned a hostname. Every compose
service with a host `ports:` mapping needs an explicit entry so nothing is
silently unreachable from the browser:

| Compose service (host port) | Ingress hostname | Mechanism |
|---|---|---|
| `zac` (8080) | `zac.local` | `podiumd.zac.ingress.*` (native) |
| `keycloak` (8081) | `keycloak.local` | raw Ingress template |
| `openzaak-nginx` (8001) | `openzaak.local` | `podiumd.openzaak.ingress.*` (native) |
| `objecten-api.local` (8010, `objecten` profile) | `objecten.local` | `podiumd.objecten.ingress.*` (native) |
| `openklant.local` (8002) | `openklant.local` | `podiumd.openklant.ingress.*` (native) |
| `solr` (8983) | `solr.local` | raw Ingress template |
| `pabc-api` (8006) | `pabc.local` | `podiumd.pabc.ingress.*` (native) — already covered above |
| `opennotificaties` (8003, profile) | `opennotificaties.local` | `podiumd.opennotificaties.ingress.*` (native) |
| `openarchiefbeheer-web`/`-ui` (8004/8005, profile) | `openarchiefbeheer-web.local`/`openarchiefbeheer-ui.local` | native — already covered above |
| `objecttypes-api` (8011, openformulieren profile) | `objecttypen.local` | `podiumd.objecttypen.ingress.*` (native) |
| `openformulieren-nginx`/`-web` (8007/8009, profile) | `openformulieren-nginx.local`/`openformulieren-web.local` | native — already covered above |
| `grafana` (3000, `metrics` profile) | `grafana.local` | raw Ingress template |
| `greenmail` (18083 web UI, `itest` profile) | `greenmail.local` | raw Ingress template |

Deliberately **not** exposed (matches compose's own intent — these are
internal-only even there, or genuinely not meant for interactive browser use):
ZAC's WildFly management port (9990, JMX/admin console only), OPA (8181,
internal policy engine), office-converter/Gotenberg (8083, internal
conversion API), the wiremocks (18080-18084, internal test doubles reached
only by ZAC/tests), otel-collector/tempo/prometheus (scrape/ingest endpoints,
not interactive UIs — only Grafana is).

## values.yaml profile flags

Mirrors compose's own opt-in profile behavior (`start-docker-compose.sh` with
no flags only starts the core stack). These are **our own** top-level flags,
separate from the `podiumd.<app>.enabled` flags they control:

```yaml
zac.enabled: true                 # "zac" profile — on by default so the app is visible
itest.enabled: false               # wiremocks (smartdocuments/kvk/bag), greenmail, opa-tests
objecten.enabled: false
opennotificaties.enabled: false
openarchiefbeheer.enabled: false
openformulieren.enabled: false      # also pulls in objecten + objecttypen + opennotificaties
metrics.enabled: false
```
Core (no profile in compose, so always deployed): postgres, redis, solr,
keycloak, openzaak, openklant, pabc, brp-personen-mock/wiremock.

## Self-referential URLs: every exposed service gets its hostname updated

Same root cause as the Keycloak redirect-URI issue above, but broader: several
apps bake their *own* public-facing URL into their own config, hardcoded in
`docker-compose.yaml` to `localhost:<port>`. Moving each to a Traefik
`<service>.local` hostname means that config must move with it — silently
leaving it as `localhost:<port>` would produce CORS/CSRF rejections or wrong
links, not an obvious crash, so this is enumerated explicitly rather than left
implicit:

| Service | Compose value (to replace) | New value |
|---|---|---|
| zac (`podiumd.zac.contextUrl`) | `http://localhost:8080` | `http://zac.local` |
| pabc (`podiumd.pabc.oidc.authority`) | n/a (already `http://keycloak:8080/realms/zaakafhandelcomponent/`, internal — unaffected) | unchanged |
| pabc (its own `podiumd.pabc.ingress.hosts`) | `localhost:8006`/`8000` | `pabc.local` |
| openarchiefbeheer-web (`CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`) | `http://localhost:8005,http://localhost:8004` | `http://openarchiefbeheer-ui.local,http://openarchiefbeheer-web.local` |
| openarchiefbeheer-web (`OAB_API_URL`) | `http://localhost:8004` | `http://openarchiefbeheer-web.local` |
| openarchiefbeheer-web (`FRONTEND_URL`) | `http://localhost:8005` | `http://openarchiefbeheer-ui.local` |
| openformulieren (`CSRF_TRUSTED_ORIGINS`) | `http://localhost:8007,http://localhost:8009` | `http://openformulieren-nginx.local,http://openformulieren-web.local` |

The exact `values.yaml` field path for each (`podiumd.<app>.settings.*`
nesting) is confirmed for openzaak/pabc directly; the CORS/CSRF/frontend-url
field names for openarchiefbeheer/openformulieren specifically follow the
same `settings.*` shape but get a final confirmation pass per-chart during
step 5 (when those profile groups are actually wired).

## openzaak: the "copy fake-test-document.pdf" startup step

Compose's `openzaak.local` service overrides its `command` to
`copy-test-pdf-and-start-openzaak.sh`, which copies a vendored test PDF into
`/app/private-media/uploads/2023/{10,11,12}/` (referenced by the zaaktype
fixture SQL in `06-setup-zac-config-after.sql`) before exec'ing the image's
normal `/start.sh`. The `openzaak` dependency (nested inside `podiumd`) has
its own entrypoint we don't want to fight with a `command` override, so this
becomes an **initContainer** on its pod (via its `extraInitContainers` value,
or equivalent — confirmed at implementation time) that mounts the vendored
`fake-test-document.pdf` (as a ConfigMap or small init image) and the same
persistence volume the main container uses (the one we pre-provision — see
"PodiumD's Azure-CSI storage templates" above), and copies the file into the
same three subpaths, before the main container starts normally.

## Resource footprint optimizations

Minikube is a single-node cluster with limited memory, so beyond the
already-shared Postgres/Redis/OPA-sidecar decisions above, the chart
deliberately overrides several subchart defaults that are sized for
production HA rather than a single dev node:

- **`replicaCount: 1` + `autoscaling.enabled: false` on every app nested
  inside `podiumd`** (openzaak, openklant, objecten, objecttypen,
  opennotificaties, openarchiefbeheer, openformulieren, pabc). Several of
  these default to 2+ replicas / HPA for production HA — pure waste on one
  node. This is the single broadest win since it's a uniform override
  applied identically across every dependency.
- **Wiremocks merged into one pod** — see "Wiremocks" above; cuts up to 3
  standing WireMock JVMs (~150–200MB each) down to zero marginal processes
  when `itest.enabled=true`.
- **Explicit, deliberately low JVM heap / container resource requests**,
  rather than inheriting each chart's production-sized defaults:
  - ZAC: `-Xms512m -Xmx1024m` (vs. compose's `1024m/1024m`), and no hard
    container memory limit (unlike compose's `deploy.resources.limits.memory:
    4G`) so it can burst instead of getting OOMKilled on a memory-constrained
    minikube VM.
  - Solr: `SOLR_JAVA_MEM=-Xms256m -Xmx512m` — dev only ever populates one
    trivial "zac" core.
  - Keycloak: bounded heap via `JAVA_OPTS_APPEND`; `start-dev` mode already
    avoids clustering/Infinispan overhead.
  - CPU (lower priority): `-XX:ActiveProcessorCount=2` on every JVM-based
    component (ZAC, Keycloak, Solr) so they don't auto-detect the full
    minikube VM core count and over-spawn GC/JIT threads.
- **`flower.enabled: false` everywhere** (see Dependencies section) — not
  needed for local dev and we already have a separate `metrics` profile for
  observability.
- **Per-app worker/nginx/beat pod count must match compose's actual container
  list — and the mechanism is `replicaCount: 0`, not an `.enabled` toggle.**
  Pulled and read `openzaak`'s and `openklant`'s actual `templates/
  deployment.yaml` directly: each renders **four separate, unconditional**
  `Deployment` objects from one file (main app, nginx, worker, beat) — none
  of nginx/worker/beat has an `{{- if }}` guard at all, only `flower` does
  (`{{ if .Values.flower.enabled -}}`). So there is no `worker.enabled`/
  `nginx.enabled` key to set — the only lever is each sub-component's own
  `replicaCount`, scaling it to zero pods (the Deployment/ReplicaSet objects
  still exist in the cluster, just with nothing running — functionally
  equivalent to "off" for pod-count purposes). Compose's own service list is
  the ground truth for which should be `0` vs. left at their chart default:
  - **openklant**: compose runs it as a **single bare container** — no
    `openklant-celery` service exists anywhere in `docker-compose.yaml`.
    `podiumd.openklant.worker.replicaCount: 0` and
    `podiumd.openklant.nginx.replicaCount: 0` (chart defaults are `2` and `1`
    respectively — leaving either at its default would be pure excess over
    compose, not parity).
  - **openzaak**: compose runs `openzaak-nginx` **unconditionally** (no
    `profiles:` entry — required for chunked transfer-encoding on large file
    uploads) but gates `openzaak-celery` behind `profiles: ["opennotificaties",
    "openformulieren"]`. So: `podiumd.openzaak.nginx.replicaCount: 1` (leave
    at chart default) but `podiumd.openzaak.worker.replicaCount: 0` for the
    core profile, raised back to `1` only when `opennotificaties.enabled` or
    `openformulieren.enabled` is set.
  - **openarchiefbeheer**/**openformulieren** (both profile-gated, checked at
    step 5): compose runs *both* a `-celery` and a separate `-celery-beat`
    service for each — so both `worker.replicaCount` and `beat.replicaCount`
    stay at their chart defaults when that profile is active, matching
    compose 1:1 there.
  - **opennotificaties**: compose runs one `opennotificaties-celery` (no
    separate beat service) — `worker.replicaCount` at its default,
    `beat.replicaCount: 0`, when its profile is active.
  - **objecten**: compose runs `objecten-api-celery` whenever the `objecten`
    profile itself is active (no separate beat) — `worker.replicaCount` at
    its default, `beat.replicaCount: 0` in that case.
  `flower.enabled: false` (a real toggle, unlike the others) stays as
  documented above regardless.
- **Minimum minikube VM sizing** — a rough tally of the *core* profile alone
  (ZAC's 1Gi JVM heap + WildFly overhead, Solr's ~512Mi heap + overhead,
  Keycloak, Postgres, openzaak (app+nginx), openklant, pabc-api,
  brp-personen-mock, the merged WireMock pod, office-converter) lands
  somewhere around 6–8Gi of actual memory once every container's baseline
  overhead (not just its JVM heap) is counted — comfortably more than
  minikube's own default allocation (2 CPU / 2–4Gi depending on driver/
  version). None of the optimizations above change that; they reduce
  overhead, not the fact that this is still ~11 containers. This isn't a
  chart defect — it needs documenting as a **prerequisite**, not something
  the chart can enforce: the README should say to start minikube with
  something like `minikube start --cpus=4 --memory=8192` (adjust from real
  measurements once step 4's actual `helm install` is up and pods are
  observed with `kubectl top pods`), so an undersized default minikube VM
  isn't mistaken for a broken chart when pods are actually just Pending on
  insufficient allocatable memory.

## Storage: minikube's default StorageClass

Everything that would otherwise need an Azure-specific storage class is now
either replaced with a plain single-container Deployment with its own
ordinary PVC (Redis has none needed; Postgres, Solr, Grafana each get one),
or handled by the pre-provisioned-PV/PVC hook mechanism (openzaak, openklant,
and later opennotificaties/openarchiefbeheer/openformulieren) — and these two
cases are deliberately backed differently:

- **Postgres/Solr/Grafana's own PVCs**: ordinary dynamic provisioning against
  minikube's default StorageClass, `standard` (backed by the
  `storage-provisioner` addon, hostPath-based) — `storageClassName` left
  unset (falls back to the cluster's marked-default class) or explicitly set
  to `standard`. Nothing else is racing to create a same-named object here,
  so there's no need for anything more precise.
- **The pre-provisioned PV/PVC pairs for podiumd-nested apps**: `storageClassName:
  ""` (explicit empty string) on *both* the PV and PVC, `accessModes:
  [ReadWriteOnce]`, and a plain `hostPath` volume source on the PV — see
  "PodiumD's Azure-CSI storage templates" above for why: referencing
  `standard` here would trigger *dynamic* provisioning (since it's the
  cluster default) instead of statically binding to the specific,
  fixed-name PV podiumd's own `lookup` check needs to find already existing.

Either way, nothing here ever inherits podiumd's Azure-specific
`podiumd-standard`/`managed-csi-premiumv2` defaults.

## Build order (staged, to keep review manageable)

0. Vendor every file asset listed above into
   `vendor/dimpact-zaakafhandelcomponent/...` with a `NOTES.md` recording
   where each came from — **including** patching the vendored realm JSON's
   redirect URIs/web origins (not a byte-for-byte copy) and writing the merged
   `00-create-databases.sql`/`01-seed-fixtures.sh` Postgres init scripts out of
   the three existing per-service ones. **Not yet done — next concrete step.**
1. Chart skeleton: `Chart.yaml` with the single `podiumd` dependency +
   `scripts/set-podiumd-version.sh`, `values.yaml` with the
   enable/disable/replicaCount/flower/redis/ingress overrides from the
   Dependencies section above (facts already confirmed live — this step is
   wiring known values, not discovery).
2. Core raw templates: shared Postgres (with the merged init scripts), the
   merged WireMock pod, Keycloak (with the patched realm ConfigMap), Redis,
   Solr, and the hook-annotated pre-provisioned PV/PVC pair for openzaak and
   openklant (both "core", both need the storage workaround from day one).
3. Wire the `podiumd`-nested openzaak (including its test-PDF initContainer),
   openklant, pabc, brppersonenmock, and zac: point `settings.database.*`/
   `tags.redis: false` at the shared Postgres/Redis, set
   `configuration.secrets.keycloak_client_secret` to match the vendored
   realm's client secrets, set each one's own native
   `ingress.{enabled,className,hosts,tls}` block to `traefik` + its
   `<service>.local` hostname, and apply the self-referential URL updates
   from the table above where relevant (pabc, zac).
4. Verify the stack boots on minikube and login/OIDC works end-to-end through
   `http://zac.local`.
5. Layer in optional profile groups (objecten, opennotificaties,
   openarchiefbeheer, openformulieren, metrics, itest) behind their `enabled`
   flags, each following the same wiring pattern established in step 3 —
   including their own pre-provisioned PV/PVC hooks (opennotificaties,
   openarchiefbeheer, openformulieren all have the same Azure-CSI storage
   template problem as openzaak/openklant) and the
   openarchiefbeheer/openformulieren self-referential URL updates from the
   table above, confirmed against those charts' actual `settings.*` field
   names at that point (not yet individually verified, unlike openzaak/pabc).

## Verification

Structured around the four properties a working equivalent of
`docker-compose.yaml` actually needs — not just "does it render":

- **Renders cleanly**: `helm dependency update && helm lint .`;
  `helm template podiumd-minikube .` with default values (core profile only)
  and with every profile flag turned on.
- **External reachability**: after `helm install` + adding the External
  reachability table's hostnames to `/etc/hosts` (pointed at the Traefik
  address), every URL in that table actually loads — not just `zac.local`.
  Specifically confirm `kubectl get ingress -A` lists all of them with an
  address, and that Keycloak's login redirect round-trips correctly (the
  actual end-to-end proof that the issuer-URL-consistency design works, not
  just that both services independently start).
- **Storage is persistent where needed**: `kubectl get pv,pvc -A` shows Bound
  volumes for Postgres, Solr, and (once enabled) Grafana and every
  podiumd-nested app with an `-storage.yaml` template (openzaak, openklant,
  ...) — confirm those are backed by `standard`, not left `Pending` (which
  would indicate the Azure-CSI template fired instead of ours). Then
  concretely: `kubectl delete pod` on the Postgres/Solr/an-openzaak pod and
  confirm data survives the restart (a Solr search still returns previously
  indexed results, an openzaak-uploaded test document is still present) —
  actually exercising persistence, not just checking the PVC exists.
- **Minimum pods**: `kubectl get pods` in the default (core) profile should
  show roughly Postgres + Redis + Solr + WireMock + Keycloak + zac (+OPA
  sidecar) + office-converter + openzaak (+its always-on nginx, no worker) +
  openklant (no worker, no nginx) + pabc-api + brp-personen-mock — compare
  this list directly against the per-app worker/nginx/beat audit above and
  flag any pod that audit says shouldn't be there.
- **Database/storage initialization**: `kubectl exec` into the Postgres pod
  and confirm all 9 databases exist with the right owners, the 4 PostGIS-
  dependent ones actually have the extension installed
  (`\dx` inside each), and the openzaak/openklant/openarchiefbeheer fixture
  data landed (e.g. the ZAC-test zaaktypes exist in OpenZaak's catalog) —
  the full sequence (create → wait for app migrations → seed) actually
  completing, not just the scripts existing.
- `./scripts/set-podiumd-version.sh <other-version> && helm template .`
  renders cleanly too, confirming the version-swap mechanism actually works
  end-to-end (a real test of the "easily configurable" requirement, not just
  a paper mechanism).
- Matches the compose walkthrough in
  `docs/development/installDockerCompose.md` (behavioral reference only —
  nothing in this repo reads that file at runtime).

## Status

Plan fully designed and cross-checked against live chart repos: `helm show
values`/`helm pull` against `@maykinmedia`/`@dimpact`/OCI repos for the
individual app charts, and a direct pull + template inspection of
`dimpact/podiumd` 4.8.1 itself (confirming the enable/disable defaults, the
OIDC secret auto-generation behavior, and — critically — the Azure-CSI
storage template problem and its pre-provisioned-PV/PVC-hook fix).

A subsequent desk-check against the four criteria "external reachability,
persistent storage, minimum pods, database/storage initialization" (compared
directly against `docker-compose.yaml`'s actual service list, since nothing
is deployed yet to test live) found and fixed five concrete gaps: Solr and
Grafana were missing PVCs entirely; openzaak/openklant/objecten/objecttypen
had no explicit Ingress hostname assigned (only the mechanism was described);
per-app worker/nginx/beat pod counts hadn't been audited against compose's
actual per-app container list (openklant needs both disabled entirely;
openzaak needs nginx on but worker off in the core profile); and the merged
Postgres init script could have silently failed to give 4 of the 9 databases
the PostGIS extension due to alphabetical script-ordering. All five are now
resolved in the relevant sections above, not left as open gaps.

**Step 0 (vendoring) is now done** — see `vendor/dimpact-zaakafhandelcomponent/NOTES.md`
for exact provenance of every file. Notable can  during vendoring itself:
`00-create-databases.sql` needed a 10th database (`openformulieren`, missing
from this plan's earlier "9 databases" phrasing), and the merged
`01-seed-fixtures.sh` intentionally drops the original scripts' `useradd`
calls (vestigial — Postgres's official image runs
`docker-entrypoint-initdb.d` scripts under temporarily-trusted local auth
regardless). Both are corrected in the vendored files and in `NOTES.md`.

**Step 1 (Chart skeleton) is now done** — `Chart.yaml`, `values.yaml`,
`scripts/set-podiumd-version.sh` all exist and `helm lint`/`helm template`
both pass cleanly. The version-swap script was tested end-to-end (actually
ran `helm dependency update` against a different version and back, not just
read for syntax). `charts/podiumd-4.8.1.tgz` and `Chart.lock` were originally
committed deliberately (not gitignored), matching `podiumd-infra`'s own
convention, so `helm template`/`helm install` would work offline without a
`helm dependency update` step first.

**Reversed after step 5** (`charts/*.tgz` now gitignored, `Chart.lock` still
committed): the offline-reproducibility argument no longer holds once
`scripts/provision-cluster.sh` exists and already runs `helm dependency
update` itself as part of provisioning any cluster - there's no scenario
left where having the tarball pre-committed actually saves that step. Worse,
it turned out to be the direct root cause of a real bug hit live in step 4:
Helm's own release record embeds the entire resolved chart, including this
3.87MB dependency, which exceeds Kubernetes' hardcoded 3MB API request-size
limit and is exactly why `helm install` had to be abandoned for
`helm template | kubectl apply` for the rest of this project. `Chart.lock`
stays committed - it's a small text file (name/version/repository/digest
only) with none of the tarball's downsides, genuinely useful for pinning.

Corrected two things this plan had wrong, found only by actually rendering
the chart against real values rather than reading source: there is **no**
`worker.enabled`/`nginx.enabled` toggle on the Maykin-family charts at all —
`openzaak`/`openklant`'s `templates/deployment.yaml` renders four
Deployments unconditionally (main/nginx/worker/beat; only `flower` has a real
`{{- if }}` guard) — the actual lever is scaling `replicaCount` to `0`. And
the earlier "every relevant app needs
`configuration.secrets.keycloak_client_secret`" claim was wrong — checked the
vendored realm.json directly and only `zaakafhandelcomponent`/`pabc` have
Keycloak clients at all; the others use Django's own local admin login or (for
openzaak specifically) a separate ZGW JWT mechanism unrelated to Keycloak
despite the similar-sounding secret name. Both corrected in the relevant plan
sections and in `values.yaml`.

Three more issues found only by actually rendering (not visible from reading
values.yaml alone), all fixed:
- `podiumd.zac.solr-operator.enabled` defaults to `true` in podiumd's own
  override (unlike `charts/zac`'s own quiet `false` default) — rendered a
  full `SolrCloud` + operator RBAC before being disabled explicitly.
- `podiumd.zac.opentelemetry-collector.enabled` similarly rendered a
  standing Deployment despite `charts/zac`'s own default being `false` —
  confirmed the override path via `--set` before adding it explicitly.
- `openklant`'s own `templates/ingress.yaml` hardwires its backend to the
  `<fullname>-nginx` Service specifically — scaling `nginx.replicaCount` to
  `0` for openklant (to match compose's "single bare container, no nginx")
  would have made `openklant.local` completely unreachable through this
  chart's own Ingress. Left `nginx.replicaCount` at its chart default for
  openklant; only `worker.replicaCount: 0` applies there.
- `openzaak.create_required_catalogi_job` defaults to `enabled: true` with
  literal placeholder credentials (`<openzaak_client_id>`/`<openzaak_secret>`)
  that would just fail repeatedly, and would also attempt to create its own
  test zaaktype/catalog that could conflict with the vendored SQL fixtures
  which already seed this exact data — disabled explicitly.

Current rendered output (core profile, default values): 12 Deployment objects
(3 scaled to 0 replicas: `openklant-worker`, `openzaak-worker`,
`openzaak-beat`), 4 Ingress (zac/openzaak/openklant/pabc `.local` hosts all
present), 4 Job + 2 CronJob, 2 PersistentVolume + 2 PersistentVolumeClaim
(still podiumd's own Azure-CSI ones for openzaak/openklant — expected until
step 2's pre-provisioned-hook fix lands).

**Step 2 (core raw templates) is now done**: `templates/postgres/` (Deployment
+ Service + PVC + two ConfigMap groups — one for the merged init scripts via
`.Files.Glob`, one per app for the vendored fixture SQL), `templates/redis/`,
`templates/keycloak/` (Deployment + Service + Ingress + realm ConfigMap),
`templates/solr/` (Deployment with a `chown`-fixing initContainer, matching
compose's own root/chown dance, + PVC + Service + Ingress), `templates/wiremock/`
(the merged pod, brp-personen-wiremock's 3 mappings mounted via `subPath` —
see correction below), and `templates/storage-hooks.yaml` (the
pre-provisioned PV/PVC mechanism from the Dependencies section, written as a
single `range` over all five possible apps gated on each one's own
`podiumd.<app>.enabled`, so turning on a profile flag later needs no template
changes).

Also fixed: `01-seed-fixtures.sh` (vendored in step 0) unconditionally
backgrounded all three seed loops, but `openarchiefbeheer` isn't deployed in
the core profile — its loop would have polled forever for migrations that
would never happen. Added an env-var guard
(`SEED_OPENARCHIEFBEHEER`, driven by this chart's own
`openarchiefbeheer.enabled` flag) so it only backgrounds when that profile is
actually active.

One correction found only by reasoning through WireMock's actual file-loading
behavior (not caught by rendering, since `helm template` can't detect a
runtime-only bug like this): WireMock's file-based mapping loader only scans
**flat files** directly in `mappings/`, it does not recurse into
subdirectories. The initial draft mounted the whole ConfigMap as one
subdirectory (`mappings/brp-personen/`), which would have silently loaded
zero mappings. Fixed by mounting each mapping file individually via `subPath`
straight into `mappings/` — the same approach step 5 will need for the three
itest-only mapping sets sharing this one pod.

Verified via `helm template` that the pre-provisioned PV/PVC names match
podiumd's own exactly (`default-openzaak`/`default-openklant` for PVs,
`openzaak`/`openklant` for PVCs) — the necessary precondition for the
lookup-skip mechanism to work. `helm template` can't exercise the actual live
`lookup()` skip behavior itself (no live cluster during a dry-run render) —
that's confirmed for real in step 4, on an actual minikube cluster.

Next step: build order step 3 (wire the podiumd-nested core apps' remaining
settings — this was largely already done in step 1's `values.yaml`, so step 3
is mostly the openzaak test-PDF initContainer plus a final pass confirming
everything boots together).

**Step 3 is now done.**

The test-PDF mechanism turned out simpler than planned: checked the actual
bundled `openzaak` chart's `deployment.yaml` directly and it has **no**
`initContainers`/`extraInitContainers` support at all — but it does have
`extraVolumes`/`extraVolumeMounts` (real, `tpl`-rendered extension points).
Vendored `fake-test-document.pdf` (missed in step 0 — added now, as a
`binaryData` ConfigMap via `.Files.Get | b64enc`) and mounted it three times
via `subPath` directly at the three target paths inside the existing
`/app/private-media` volume — no initContainer needed at all.

Then ran a systematic check: extracted every env var name `docker-compose.yaml`
sets for `zac`/`openklant`/`pabc` and diffed against what actually renders,
rather than trusting step 1's values.yaml was complete. Found and fixed four
real gaps this way:
- `BRON_ORGANISATIE_RSIN`/`VERANTWOORDELIJKE_ORGANISATIE_RSIN` were entirely
  missing from step 1 (`organizations.bron.rsin`/`organizations.
  verantwoordelijke.rsin`) — silently defaulted to `"000000000"`.
- `openklant.settings.secretKey` was left at its empty-string chart default —
  now set to `openZaakSecretKey`, matching compose exactly (yes, compose
  really does reuse openzaak's own secret key literally there — a
  copy-paste artifact in compose itself).
- `openklant.settings.cache.default`/`.axes` were missing entirely (only
  `celery.brokerUrl`/`.resultBackend` had been set in step 1) — checked the
  chart's own `configmap.yaml` directly and confirmed `tags.redis: false`
  routes `CACHE_DEFAULT`/`CACHE_AXES` through these fields specifically, not
  through `celery.*` — would have pointed Django's cache backend at nothing.
- `pabc.settings.oidc.requireHttps` defaults to `true` — would have rejected
  every token from our HTTP-only Keycloak outright, breaking PABC's login
  entirely. Set to `false`, matching compose's own `Oidc__RequireHttps:
  "false"`.

Also confirmed two things that look like gaps but aren't fixable and are
being accepted as-is, both tied to the exact zac chart version (1.0.251)
podiumd 4.8.1 bundles — verified directly against that bundled chart's own
templates, not the current (newer) `charts/zac` in
`dimpact-zaakafhandelcomponent`:
- `auth.enablePkce` is a silent no-op — the bundled version's `config.yaml`
  has no `AUTH_ENABLE_PKCE` line at all (a newer feature not present in this
  older, version-locked chart). Left set anyway — harmless, and takes effect
  automatically if a future podiumd version bundles a newer zac chart.
- `OTEL_SDK_DISABLED` is hardcoded `"false"` with no override, and
  `OTEL_EXPORTER_OTLP_ENDPOINT` defaults to a nonexistent collector service
  (disabled above). ZAC's JVM will harmlessly retry failed trace exports in
  the background — noisy log warnings, no functional impact, no values-level
  fix exists for this specific chart version.

Next step: build order step 4 (verify the stack actually boots on a real
minikube cluster and the login/OIDC flow works end-to-end through
`http://zac.local` — the first point in this project where anything gets
deployed to a live cluster rather than just rendered).

**Step 4 is now done**: deployed live to a real minikube cluster (via
`helm template | kubectl apply`, not `helm install` — see below for why),
all 14 core-profile pods reached `Running`/`Ready`, and the full OIDC login
flow was verified end-to-end through `http://zac.local` by replaying the
browser's redirect chain with `curl` (cookie jar across hops): unauthenticated
GET → 302 to Keycloak → login form (200) → credential POST → 302 with an
authorization code → ZAC's own callback → final `200` on `zac-root`, the real
Angular app shell, not the "Geen toestemming" (403) page. Confirmed with two
different vendored test users (`raadpleger1newiam`, then
`beheerder1newiam` after the PABC mapping-data fix below).

Getting there took far more than a clean `helm install` - every fix below was
found by actually watching pods fail live, not by re-reading values.yaml:

- **Cluster infra, not the chart**: the local `helm` binary (v3.9.0) couldn't
  parse the latest Traefik chart (needs Go 1.18+ template `break`) - pinned
  Traefik to `--version 34.4.0` instead. Minikube's inner Docker has zero
  network access even for `gcr.io/google-samples/hello-app` - every image
  needed a host-side `docker pull` + `minikube image load` instead of
  in-cluster pulls. Attempting to raise the Kubernetes API server's request
  size limit (`--extra-config=apiserver.max-request-bytes=...`, to work
  around Helm's release record embedding the whole 3.87MB vendored
  `podiumd-4.8.1.tgz`) crash-looped the whole control plane, since that flag
  was **removed** in Kubernetes v1.35.1 - recovered via a direct
  `kube-apiserver.yaml` manifest edit + `kubeadm init phase addon all` to
  restore CoreDNS/kube-proxy, then abandoned `helm install` for
  `helm template | kubectl apply` for the rest of this step (the 3MB limit is
  a hardcoded constant in this Kubernetes version, no flag exists at all).
  Consequence: Helm hook/`lookup()` semantics needing a live release
  (`storage-hooks.yaml`'s whole PV/PVC pre-provisioning mechanism) don't fire
  under `kubectl apply` - worked around by applying those specific objects
  first and relying on Kubernetes' own immutable-spec protection to reject
  podiumd's competing Azure-CSI objects on every subsequent apply (the
  resulting "Forbidden: spec is immutable" errors are expected, not
  failures).
- **`SOLR_PORT` service-link collision**: Kubernetes auto-injects
  `<SERVICE_NAME>_PORT` env vars for every Service in the namespace: Solr
  crashed parsing the injected `tcp://10.x.x.x:8983` as its own `SOLR_PORT`
  config value. Fixed with `enableServiceLinks: false` on every raw pod
  template (postgres, redis, keycloak, solr, wiremock) as a class fix, not
  just for Solr.
  `command: ["solr-precreate", "zac"]` (matching compose exactly) with
  `podiumd.zac.solr.createZacCore: false`.
- **Solr readiness probe flakiness**: cold TCP connections to Solr measured
  ~2s live (kubelet's HTTP prober opens a fresh connection per probe, no
  keep-alive reuse), but the probe never set an explicit `timeoutSeconds`,
  defaulting to Kubernetes' built-in 1s - every probe attempt was destined to
  time out even though the app itself always responded successfully. Fixed
  with `timeoutSeconds: 5` on Solr's readinessProbe. Under genuine node
  resource contention (multiple JVMs booting concurrently, load average 6+,
  active swapping) this still occasionally flaps, but resolves itself once
  concurrent boot activity settles - not a probe misconfiguration at that
  point, just a genuinely loaded single-node VM.
- **Five digest-qualified image references failed to pull**
  (`nginxinc/nginx-unprivileged`, `ghcr.io/infonl/zaakafhandelcomponent`,
  `gotenberg/gotenberg`, `openpolicyagent/opa`, `curlimages/curl`): kubelet's
  exact-reference matching for `IfNotPresent` doesn't treat a tag-only
  loaded image as satisfying a `repo:tag@sha256:digest` reference, so it
  always attempts a live pull, which then fails (no network in-cluster).
  Fixed with explicit tag-only overrides for each field
  (`podiumd.zac.image.tag`, `.global.curlImage.tag`, `.opa.image.tag`,
  `.office_converter.image.tag`, `podiumd.openzaak.nginx.image.tag`,
  `podiumd.openklant.nginx.image.tag`). Also found `podiumd.zac.nginx.enabled:
  true` is podiumd's own production-specific override (the underlying zac
  chart's own default is `false`, matching compose's single-container
  architecture) - disabled explicitly, which also sidesteps that image's
  digest problem entirely.
- **`openzaak-config`/`openklant-config` Jobs failing "No steps enabled,
  aborting"**: with no `configuration.data` set for either app (matching
  compose, which doesn't use django-setup-configuration for them at all), the
  underlying management command treats "nothing to do" as an error. Fixed
  with `configuration.job.enabled: false` for both.
- **openzaak `SECRET_KEY` missing**: `podiumd.openzaak.settings.secretKey`
  was left unset in step 3's pass (openklant's had already been fixed) -
  `ImproperlyConfigured: SECRET_KEY must not be empty` on every request.
  Fixed with `secretKey: openZaakSecretKey`, matching compose's own
  (copy-pasted) literal value for both apps.
- **openzaak's fixture-seeding script polling forever**: the vendored
  `01-seed-fixtures.sh` waits for `accounts_user` with `username='admin'`
  before applying openzaak's SQL fixtures (the ZGW API client credentials ZAC
  itself needs), but nothing in this chart ever creates that admin user -
  compose sets `OPENZAAK_SUPERUSER_USERNAME`/`_PASSWORD`/`_EMAIL` for exactly
  this, which step 3 missed entirely. Fixed with
  `podiumd.openzaak.configuration.superuser.{username,password,email}`
  (verified against the chart's own `secret.yaml`/`configmap.yaml` templates
  - this is a *different* `configuration.*` block than the
  `configuration.job` one already disabled above). Since Postgres only runs
  `docker-entrypoint-initdb.d` once per PVC lifetime and the earlier cluster
  incident had killed the backgrounded seed script mid-flight on an already-
  initialized data directory, the fixture SQL had to be re-run manually
  against the live Postgres pod once the admin user finally existed, rather
  than relying on a fresh pod restart to retry it.
- **PABC readiness check permanently `DOWN`**: `PabcClientHeadersFactory`'s
  companion object reads `pabc.api.key` via MicroProfile Config at
  class-init time - a missing value throws once and Java caches the failure
  forever as `Could not initialize class ...` on every later use. Fixed with
  `podiumd.zac.pabcApi.apiKey: zac-test-api-key`, matching
  `podiumd.pabc.settings.apiKeys` and compose's own `.env.example` default
  (both sides of this API key were previously only half-configured - PABC's
  side had the right value already, ZAC's side was empty).
- **`openzaak.local`/`openklant.local` returning Django's own 400
  DisallowedHost page**: the chart's default `ALLOWED_HOSTS` only covers the
  in-cluster fullname/namespace-qualified name (sufficient for ZAC's own
  backend calls via Service DNS), never the Traefik Ingress hostname. Fixed
  with `settings.allowedHosts: <app>.local` on both.
- **Every login rejected with `invalid_request: Missing parameter:
  code_challenge_method`**: the vendored realm's `zaakafhandelcomponent`
  client requires PKCE, but PKCE support in ZAC itself is unreleased (see the
  `auth.enablePkce` comment in `values.yaml` and
  `vendor/dimpact-zaakafhandelcomponent/NOTES.md`'s PKCE note) - our pinned
  `podiumd.zac.image.tag: "5.0.1"` predates it. A first attempt to inject
  `AUTH_ENABLE_PKCE=true` via a Helm post-renderer
  (`scripts/post-render.py`, since the chart exposes no values field for it)
  confirmed the image's bundled `oidc.json` has no matching field at all -
  reverted that script since it was a genuine no-op. Fixed instead on the
  Keycloak side: patched the vendored realm's
  `pkce.code.challenge.method` from `"S256"` to `""` for this one client
  (`account-console`/`security-admin-console` untouched). Realm re-import on
  pod restart does **not** overwrite an already-existing realm, so the
  live Keycloak client also needed a direct Admin REST API `PUT` to take
  effect immediately without wiping all other realm data.
- **Every authenticated user rejected with "Geen toestemming" (403), even a
  `beheerders-elk-domein` group member**: PABC, not Keycloak group/role
  membership directly, is ZAC's actual source of truth for authorization
  decisions - and nothing in this chart ever seeded PABC's own
  application/role/domain/mapping data. Compose's `pabc-migrations` container
  mounts exactly this via `JSON_DATASET_PATH`
  (`scripts/docker-compose/imports/pabc-database/json-mapping/
  pabc-mapping-data.json`), which step 0's vendoring pass missed entirely
  (it's data for a *migration job*, not an app config file, so it didn't fit
  the categories vendored back then). Fixed by vendoring the file
  (`vendor/dimpact-zaakafhandelcomponent/pabc/pabc-mapping-data.json`), adding
  a ConfigMap for it (`templates/pabc/configmap-mapping-data.yaml`), and
  wiring it into the migrations Job via the chart's own
  `migrations.dataSetPath`/`extraVolumes`/`extraVolumeMounts` fields (exactly
  the mechanism the chart's own values.yaml documents for this). Since the
  migrations Job's name is pinned to `{{ .Release.Revision }}` (which
  `helm template` always renders as `1`, having never run a real
  `helm upgrade`), re-running it after the values change required manually
  deleting the old completed Job first.

  **Found live afterward (post step-5), confirmed by direct test**: this
  Job is not idempotent - its container clears PABC's database before
  reloading the vendored seed dataset, every time it runs. Re-deleting and
  reapplying it (the exact manual step used above and again for the
  image-tag fix in step 5) silently wipes and reseeds PABC's tables even if
  they already hold the correct data (or, in a real environment, anything
  added since). `scripts/apply-pabc-migrations.sh` is now the one place
  this Job should ever be (re)created - it checks PABC's `mapping` table
  first and refuses to proceed unless it's empty or `--force` is passed,
  replacing the ad-hoc `kubectl delete job` + `kubectl apply` pattern used
  twice above.

All fixes are committed in `values.yaml`/`templates/`/`vendor/` (nothing left
as a live-cluster-only patch except the Keycloak Admin API PKCE call above,
which only affects this specific already-running cluster's live data, not the
chart - a fresh `helm install` picks up the realm-JSON fix directly instead).

Next step: build order step 5 (layer in the optional profile groups -
objecten, opennotificaties, openarchiefbeheer, openformulieren, metrics,
itest - each following step 3's wiring pattern, not yet individually
verified the way the core profile now has been).

**Step 5 is now done** (values.yaml/templates wiring + `helm lint`/`helm
template` verification with every profile flag on at once - not yet a live
minikube deploy the way step 4 was for the core profile; that's the natural
next verification pass, not yet done).

Researched each remaining subchart directly (objecten/objecttypen,
opennotificaties/openforms, openarchiefbeheer, plus the vendored metrics/itest
assets) before wiring anything, the same way step 1 researched openzaak/pabc -
this caught several real chart quirks that reading values.yaml alone would
have missed:

- **objecttypen** is fully stateless (no worker/beat/flower/persistence at
  all) - only objecten itself has a real celery worker and its own PVC.
  `create_required_objecttypen_job` (podiumd's own override, enabled by
  default with placeholder credentials) has the same problem as openzaak's
  `create_required_catalogi_job` - disabled for the same reason.
- **openarchiefbeheer is genuinely 5 Deployments** (main/nginx/worker/beat/
  flower), not the 2 ("web"/"ui") the plan's compose-derived naming implied -
  nginx serves the SPA static files AND proxies `/api/` to the backend from
  the *same* container, and there is only **one** native Ingress, routing
  exclusively to nginx. Reproducing the two compose hostnames
  (`-web.local`/`-ui.local`) means two `host` entries in that single
  `ingress.hosts` list, both hitting the same nginx, plus both hostnames in
  `settings.allowedHosts` (feeds both Django's ALLOWED_HOSTS and nginx's own
  `server_name`).
- **openarchiefbeheer needs a two-file custom-settings workaround**, mirroring
  openzaak's test-PDF initContainer trick from step 3: compose overrides
  `DJANGO_SETTINGS_MODULE` to a vendored `docker_no2fa.py` file purely to
  disable enforced two-factor auth on the admin login form (the only
  DISABLE_2FA-reading dev settings module pulls in django-debug-toolbar,
  which isn't installed in the production image) - vendored the file,
  mounted via the same `extraVolumes`/`extraVolumeMounts` + small ConfigMap
  pattern, and set `settings.djangoSettingsModule` to point at it.
  Also vendored (and lightly patched - `openzaak.local:8000` → `openzaak`)
  compose's `data.yaml` into `configuration.data`, giving openarchiefbeheer
  the same ZAC-client ZGW-consumers config compose provides via its
  `openarchiefbeheer-web-init` container.
- **opennotificaties needs RabbitMQ** - the only app here whose
  `settings.celery.brokerUrl` points at anything other than the shared Redis
  (`amqp://guest:guest@rabbitmq:5672//`, matching compose exactly). No
  rabbitmq raw template existed yet - added one
  (`templates/rabbitmq/deployment.yaml`), gated on
  `podiumd.opennotificaties.enabled` (openformulieren's own profile never
  actually uses rabbitmq itself - it only pulls in opennotificaties
  transitively per compose's profile list, so gating on that one flag alone
  is correct and sufficient).
- **A real, reproducible typo bug in podiumd's own bundled charts**: found on
  openarchiefbeheer first (`configmap.yaml` reads
  `.Values.settings.celery.resultBackendl`, note the trailing "l" - but
  `values.yaml` itself declares the correctly-spelled `resultBackend`),
  then confirmed the *same* typo also affects **openklant** (already
  deployed as a core app since step 3/4!), **objecten**, and **openforms**.
  Fixed on all four by setting both the correctly-spelled and typo'd key to
  the same value, so it works regardless of which the template actually
  reads. openklant's case is currently a no-op in practice (its celery
  worker is disabled, so nothing reads the result backend), but fixed
  anyway for correctness/future-proofing.
- **opennotificaties has an even less obvious variant of the same class of
  bug**: `CELERY_RESULT_BACKEND` is defined *twice* on this one chart - once
  in `configmap.yaml` (reading `settings.celery.resultBackend`, correctly
  spelled) and once in `secret.yaml` (reading a completely different,
  undocumented field, `settings.messageBroker.celeryResultBackend`). Both
  render the same env var name via `envFrom` on the same container: whichever
  Kubernetes resolves last wins, so both fields now carry the same value
  rather than depending on that ordering. Also found live: `PUBLISH_BROKER_URL`
  *does* have a real dedicated field (`settings.celery.publishBrokerUrl`,
  in `secret.yaml`) - an early draft of this wiring assumed it didn't exist
  and reached for the generic `extraEnvVars` passthrough instead; corrected
  to use the real field, and confirmed `RABBITMQ_HOST` genuinely has no
  field anywhere in this chart (and isn't needed - the app never reads it,
  the full connection strings already carry everything required).
- **`CACHE_OIDC` unconditionally rendered on two charts** (objecten,
  opennotificaties) with no `{{- if }}` guard at all - compose never sets an
  equivalent env var for either app, and leaving `settings.cache.oidc` unset
  let podiumd's own untouched production default
  (`redis-ha-master.podiumd.svc.cluster.local:...`) leak through into an
  otherwise fully-parameterized manifest. Caught by grepping the fully
  rendered manifest for `redis-ha-master`/`.podiumd.svc.cluster.local`/
  `azurecr.io` across every profile at once after finishing the "known"
  wiring - worth repeating as a check any time a new app is wired in, since
  it catches exactly this class of "forgot one field" leak that reading
  values.yaml alone won't reveal.
- **Tempo's OTLP listener needs `0.0.0.0:4317`, not `tempo:4317`** - compose's
  vendored `tempo.yaml` binds to the hostname `tempo`, which resolves to the
  container's own IP there (bindable). In Kubernetes, `tempo` resolves to the
  Service's ClusterIP instead, which isn't a local interface inside the pod -
  binding would fail. Patched in the vendored copy.
- **Prometheus's scrape target needed repointing** from compose's
  `host.docker.internal:9990` (a host-level port mapping that doesn't exist
  in-cluster) to a small extra Service, `zac-admin`, added specifically
  because zac's own Service (rendered by the podiumd dependency) only
  forwards its app port (80) - Kubernetes lets multiple Services select the
  same pods, so this reaches the WildFly management port (9990) the
  container already exposes without needing to touch the zac chart's own
  Service definition at all (which has no `extraPorts`-style field to do
  that anyway).
- **ZAC's OTEL_EXPORTER_OTLP_ENDPOINT gap from step 3 is now actually fixed**,
  not just documented as accepted: step 3 found the bundled zac chart
  (1.0.251) hardcodes tracing on with no way to disable it, defaulting to a
  nonexistent `<release>-opentelemetry-collector:4317`. Now that a real
  otel-collector exists, `opentelemetry_zaakafhandelcomponent.endpoint` (a
  values field that was already there, just unused) points traces at it
  directly - metrics.enabled=false still leaves this pointed at nothing
  (same harmless-retry behavior as before), but metrics.enabled=true now
  actually works.
- **WireMock's `__files` directories were never mounted at all before this
  step** - the existing `mappings/`-only subPath pattern from step 2 didn't
  need it since brp-personen-wiremock genuinely has zero `__files` content,
  but smartdocuments/kvk/bag all do (including one binary `.docx` in
  smartdocuments's set, needing `binaryData` rather than `.AsConfig`'s
  plain-text `data`). Extended the same per-file subPath pattern to cover
  both directories for all three itest-only sets.

**Explicitly out of scope, not silently dropped** - each of these is a
custom one-shot demo/fixture-seeding container in compose
(`objecten-api-import`, `objecttypes-api-import`, `opennotificaties-init`,
`openformulieren-init`) running the app image's own `init.sh` or a bespoke
multi-step shell script against custom-vendored fixture data, not the
generic `configuration.data`/django-setup-configuration mechanism the other
apps use - replicating them would mean vendoring several more
fixtures/init-script directories and writing custom Helm Jobs per app,
materially larger than "wire this profile's settings" (step 3's scope for
this step). Each of these apps still boots, migrates, and is reachable via
its own Ingress hostname without them - only the specific demo/test data
those containers seed is missing (openforms's own admin superuser *is*
still created, via `configuration.superuser`, just without the BRP service
link / klantinteracties API group / demo form import).

Next step: a live minikube verification pass for these six profile groups,
following step 4's pattern (deploy, watch pods, check reachability) - not
yet done for anything beyond the core profile.

**Live verification of step 5 is now done**, on the same running cluster
from step 4 (all core pods still healthy throughout, no regression). All 168
new/changed objects applied cleanly via `kubectl apply` (plus the same 12
expected "immutable spec" errors for the 6 apps now covered by the
storage-hook workaround), every pod reached `Running`/`Completed`, and all 9
new Ingress hostnames plus the 5 core ones responded correctly. Found and
fixed five more real, live-only issues:

- **Five apps were running the wrong image version** entirely -
  `values.yaml` never set `image.tag` for objecten/objecttypen/
  opennotificaties/openarchiefbeheer/openformulieren at all, so each
  silently used whatever the bundled chart's own default happened to be
  (objecten 3.6.0 vs compose's 3.6.1; opennotificaties 1.16.0 vs 1.15.0;
  openforms 3.4.9 vs 3.5.4 - three of these were also still
  digest-qualified, failing to pull in-cluster the same way zac's image did
  in step 4). Fixed by setting every one explicitly, pulling + loading all
  12 net-new images (all missing from minikube's cache, same as every
  image in step 4) - one `minikube image load` failed with "no space left
  on device" from running 12 in parallel and needed a sequential retry
  after clearing stale audit logs from `/tmp`.
- **A permission problem the openzaak/openklant/openarchiefbeheer apps
  never hit**: openformulieren's own entrypoint script `cp -r`s built-in
  static assets into `/srv/static/` (a mount backed by our hostPath PVC) on
  every boot, and this chart has no initContainer field at all to chown it
  first. Kubernetes' own `fsGroup: 1000` podSecurityContext (already the
  chart's default) does **not** reliably apply to bare hostPath-sourced
  PVs - confirmed live, the directory stayed `root:root` after mount. Fixed
  generally, not just for this one app: added a `storage-permissions-fix`
  Job to `storage-hooks.yaml` that `chmod -R 0777`s every enabled app's
  hostPath volume once, before the main manifest applies (applied as part
  of the same pre-step that already creates the PV/PVC pairs) - covers any
  future app hitting the same class of problem, not just openformulieren.
- **`opa-tests` failed with spurious "multiple default rules" errors**:
  mounting the whole policies ConfigMap as one directory let `opa test`'s
  own recursive directory walk see each `.rego` file three times
  (Kubernetes ConfigMap volumes expose every file through the visible
  name, a `..data` symlink target, and a timestamped directory - `opa
  test` doesn't know to skip the hidden ones). Fixed with the same
  per-file `subPath` mounting pattern already used for wiremock's
  mappings - one real file per mount point, no symlink structure left for
  the directory walk to trip over. Confirmed passing after the fix:
  **261/261**.
- **`rabbitmq`'s readiness probe** hit the same default-`timeoutSeconds`
  problem as Solr's did in step 4 (`rabbitmq-diagnostics -q ping` timing
  out after the default 1s) - fixed the same way, `timeoutSeconds: 5`.
- **A stale `openarchiefbeheer-config` Job** (created before the image-tag
  fix landed, and Jobs are immutable like `pabc-migrations-1` was in step
  4) needed manual deletion to pick up the corrected image on the next
  apply.

Confirmed working end-to-end, not just "pods are Running": Grafana's own
`/api/datasources` lists both Prometheus and Tempo, and Prometheus's own
`/api/v1/targets` shows both scrape targets (`zac-admin:9990/metrics`,
`tempo:3200/metrics`) reporting `health: up` - the whole zac→otel-collector
wiring and the new `zac-admin` Service both actually work, not just render.
openformulieren's own root path (`/`) returns a real, app-rendered 403 (not
an infra-level block) - expected given no demo form was ever imported
(documented scope decision above); its actual admin login page
(`/admin/classic-login/`) returns `200` normally.

Next step: none required for the six profile groups themselves - all now
verified live. Remaining open items are the explicitly-scoped-out
demo/fixture-seeding gaps noted above, should a fuller compose-parity pass
ever be wanted.

**Found afterward, a real design flaw**: nearly every `image.tag` override
added across steps 3-5 to work around digest-qualified images failing to
pull in-cluster (no network access - see step 4's own note on this) was
hardcoded to a *specific version string*, not just "whatever the chart's
bundled default already is, minus the digest." That's fine as long as
`podiumd`'s own bundled subchart versions never change - but
`scripts/set-podiumd-version.sh` exists specifically so they can. Audited
every `tag:` line in `values.yaml` against its own original comment and
found three genuinely different categories tangled together:

- **Pure digest-strips** (10 of them: zac, curlImage, opa, office_converter,
  openzaak's nginx, openklant's nginx, objecttypen, openarchiefbeheer +
  its nginx, openformulieren's nginx) - the hardcoded tag was always just
  whatever the chart's bundled default happened to be. Removed the
  hardcoded tags entirely; added `scripts/lib/strip-image-digests.py`, a Helm
  post-renderer that strips any `@sha256:...` suffix from every image
  reference in the fully-rendered manifest, regardless of what tag it's
  attached to. This now needs piping into every `helm template | ... |
  kubectl apply` invocation project-wide (not just an optional extra step).
- **Redundant with podiumd's own pin** (pabc's image/migrations/waitFor tags,
  all `"1.1.0"`/`"v2.0"`) - these were coupled with a *repository* override
  (escaping podiumd's private, IP-restricted ACR redirect) and the tag
  values happened to exactly match podiumd's own already-pinned defaults
  for those same fields. Removed the redundant `tag:` lines, keeping only
  `repository:` - Helm's own values-merge now inherits whatever tag
  podiumd's own value currently specifies for each field.
- **Genuine, intentional version pins** (4 of them: openzaak 1.29.1,
  objecten 3.6.1, opennotificaties 1.15.0, openformulieren 3.5.4) - these
  really do need to stay hardcoded regardless of which podiumd version is
  selected, for real functional reasons (schema compatibility with vendored
  fixture SQL, or matching `docker-compose.yaml`'s own pinned version
  exactly) having nothing to do with digest-pull avoidance. Left as-is, with
  `scripts/set-podiumd-version.sh`'s own usage comment now pointing at all
  four so a future version swap prompts re-checking whether each is still
  correct (still diverges from the new version's bundled default in the
  same way, now redundant because the new default already matches, or
  genuinely needs updating).

Verified the fix actually solves the problem it claims to, not just that it
renders: swapped to `podiumd` 4.7.8 via `set-podiumd-version.sh` (with `zac`
temporarily disabled - that version's bundled `zac` subchart needs a
different values schema entirely, an unrelated pre-existing limitation of
version-swapping this chart, not something this fix causes or could fix)
and confirmed live that `nginx-unprivileged` really does differ between
podiumd releases (`1.30.2` at 4.7.8 vs `1.31.1` at 4.8.1) - with the
hardcoded override removed, the chart now correctly picks up whichever
version the selected podiumd release actually bundles, where before it
would have stayed silently pinned to `1.31.1` regardless. The four genuine
version pins correctly stayed fixed at both versions, as intended. Switched
back to 4.8.1 (the version verified live throughout this project),
confirmed the post-render-stripped manifest resolves to the exact same tags
as before this fix (zero pod restarts on re-apply), and re-ran the full
pytest suite (41/41 still passing).

Also fixed `scripts/provision-cluster.sh`'s own image pre-load list, which
had the identical staleness problem in a different guise: a hardcoded
`IMAGES` array that would pre-load the *previous* podiumd version's set of
images after a version swap, potentially missing whatever the newly
selected version actually needs. Replaced it with the same
render-with-every-profile-on-then-strip-digests derivation used to prove
the values.yaml fix above, run fresh every time the script executes -
confirmed it derives the same 29 images either way, and that
`helm dependency update` now has to run *before* that derivation (moved
up from step 4 to step 3 in the script), since deriving the image list
needs the podiumd chart tarball to already be present.

**Added `scripts/deploy.sh`** - the "render + apply the chart" step
`provision-cluster.sh`'s own final message pointed at, previously a bare
`helm template | strip-image-digests.py | kubectl apply` command typed by
hand throughout steps 4-5 (plus a separate, easy-to-forget earlier apply of
just `templates/storage-hooks.yaml` first, for the reasons explained in
that file's own comments). Defaults to the core profile only (matching
`values.yaml`'s own default); `--full` enables every optional profile.
Classifies the expected "spec is immutable" PV/PVC errors (computed from
the same render, not a hardcoded count, so it's correct regardless of
which profiles `--full` or plain `--set` flags select) as success rather
than a failure needing investigation.

Found live while testing it against both modes on the already-`--full`-deployed
cluster: `storage-hooks.yaml`'s own `storage-permissions-fix` Job (added in
step 5's live-verification pass, alongside the PV/PVC pairs) has a volume
mount list that depends on which profiles are enabled - switching between
`--full` and the default mode on a cluster already deployed the other way
hits Job immutability head-on ("field is immutable"), aborting the whole
script under `set -e`. Fixed by having `deploy.sh` unconditionally delete
and recreate this one Job every run, before applying anything else -
unlike `pabc-migrations` (see `scripts/apply-pabc-migrations.sh`'s own
guard for why *that* one specifically must never be recreated blindly),
this Job only ever runs an idempotent `chmod`, so there's nothing it could
lose by being recreated freely.

**Found live: `opennotificaties-worker` restarting every ~8 minutes**,
visible only as a recurring liveness-probe kill/restart cycle, not an
obvious crash - traced to two independent, stacked bugs:

1. Kubernetes' legacy "service links" feature auto-injects a
   `<SERVICE_NAME>_PORT`-style env var for every Service in the namespace
   into every pod. The worker's image bundles its own
   `/wait_for_rabbitmq.sh` entrypoint script (run before celery ever
   starts), which expects `RABBITMQ_PORT` to be a bare port number - it
   got the injected `tcp://10.96.128.255:5672` instead, and
   `nc -vz $rabbit_host $rabbit_port` rejected it outright
   ("port number invalid"), so the worker process never started at all.
   Same root cause already fixed once for Solr's `SOLR_PORT` in step 4 -
   generalized this time into a universal Helm post-renderer,
   `scripts/lib/disable-service-links.py`, chained into `deploy.sh`'s
   `render()` after `strip-image-digests.py`, setting
   `enableServiceLinks: false` on every Deployment/StatefulSet/
   DaemonSet/CronJob pod spec regardless of which chart the object came
   from (none of the podiumd-nested charts expose this as a values.yaml
   field, so there's no override point to fix chart-by-chart). Deliberately
   excludes bare `Job`: a Job's `spec.template` is immutable once created,
   and patching it here broke `kubectl apply` outright on
   `pabc-migrations-1` (protected from casual recreation on purpose - see
   `apply-pabc-migrations.sh`) the first time this was tried with `Job`
   included.
2. With the injected variable no longer in the way, the *same* wait
   script's own hardcoded fallback defaults
   (`rabbit_host=${RABBITMQ_HOST:-localhost}`,
   `rabbit_port=${RABBITMQ_PORT:-5672}`) turned out to still be in play -
   `values.yaml`'s `podiumd.opennotificaties` block had a comment
   incorrectly claiming `RABBITMQ_HOST` wasn't needed, on the reasoning
   that `CELERY_BROKER_URL`/`PUBLISH_BROKER_URL` already carry the full
   connection string. True for the Django app container, wrong for the
   worker: its wait script reads `RABBITMQ_HOST`/`RABBITMQ_PORT`
   completely separately, before celery (and therefore before either of
   those settings) is ever consulted. Fixed by adding both as
   `extraEnvVars` (`RABBITMQ_HOST=rabbitmq`, `RABBITMQ_PORT="5672"` -
   no dedicated values.yaml field exists for either on this chart).

Verified live: after both fixes, the worker's log shows
`Connection to rabbitmq (10.96.128.255) 5672 port [tcp/amqp] succeeded!` →
`RabbitMQ is up.` → `celery@... ready.`, restart count staying at 0.

**Found live while verifying the above, unrelated second bug: RabbitMQ
itself was being OOMKilled** (`exitCode: 137`, `reason: OOMKilled`) under
completely normal steady-state load - just `opennotificaties` and its one
worker connected, no traffic spike needed. Its container memory limit was
256Mi; RabbitMQ's own memory high watermark defaults to 40% of what it
reads as the container's available memory, so 256Mi only gave it roughly
100MB of actual working room once the Erlang VM's own baseline overhead is
subtracted. Raised the limit to 512Mi in `templates/rabbitmq/deployment.yaml`.
Every worker that depends on RabbitMQ reconnects simultaneously each time
it dies, which plausibly contributed to the wider resource pressure seen
this session (see below) rather than being purely a symptom of it.

**Found live, separately and more severely: the whole minikube Docker
container hit its own memory limit** while iterating on the two fixes
above (several `deploy.sh --full` re-applies in quick succession, each
restarting many Deployments at once) - load average climbed past 900,
`kubectl`/`minikube ssh` both became unresponsive with TLS handshake
timeouts, and `docker stats` showed the `minikube` container itself at
728% CPU / 7.799GiB of a 7.812GiB limit. That limit (`docker inspect
--format '{{.HostConfig.Memory}}'` → exactly 8388608000 bytes = 8GiB) is
half of the 16Gi this project's own `provision-cluster.sh` requests for a
*newly created* profile - this cluster's profile predated that script (or
was never resized), so it had silently been running the entire session on
half the intended memory. Fixed live via `docker update --memory=12g
--memory-swap=-1 minikube` - no restart, no pod kills, raises the cgroup
ceiling on the already-running container. Confirmed this alone (before
either RabbitMQ-related fix was even applied) dropped the container to
561% CPU / 8.816GiB, and `kubectl get nodes`/`get pods` responded normally
again within seconds. Not yet fixed at the source (`minikube start
--memory=...` would need an actual stop/start cycle to persist this past a
future full cluster recreation) - left as a live patch for now since a
disruptive restart wasn't warranted mid-investigation on a cluster with
real deployed state.

Verified afterwards, once the cluster settled: full pytest suite still
41/41. Also checked `openformulieren-worker`, which had picked up 1
restart of its own during the same window - ruled out as unrelated crisis
collateral rather than a third bug. `kubectl describe`'s event log showed
`failed liveness probe, will be restarted`, timing out on the worker's own
`celery ... inspect --destination celery@${HOSTNAME} active` liveness
probe after its already-generous 15s `timeoutSeconds` (the `openforms`
chart's own default for this specific probe, higher than the 5s used
everywhere else in that chart - confirmed via `helm show values`/
inspecting the pulled chart tarball, not a value this project overrides).
Exit code 137 here is kubelet's own post-probe-failure `SIGKILL`, not a
cgroup OOM event (`reason: Error`, not `OOMKilled`, unlike RabbitMQ's
case) - a heavyweight Python/Celery/Django bootstrap command plausibly
just couldn't complete in 15s while the node was at 728% CPU. No restarts
since; no values.yaml override added, since 15s is already the chart
maintainers' own considered default and the underlying contention is
already fixed above.

**Reorganized `scripts/` into `scripts/lib/` for internal-only helpers.**
`strip-image-digests.py` and `disable-service-links.py` are never run by a
person directly - they only exist as pipe stages inside `deploy.sh`'s/
`provision-cluster.sh`'s own `render()` step - so they moved to
`scripts/lib/`, with every reference to their old path updated
(`deploy.sh`, `provision-cluster.sh`, this file). Deliberately did **not**
move `apply-pabc-migrations.sh` there even though it's rarely needed on the
happy path: unlike the two above, a human genuinely has to invoke it
directly and decide on `--force` in the one case it's needed - there's no
automated caller for *that* decision, which is a different thing from being
rarely needed.

**Found live, investigating "how would anyone know to run
`apply-pabc-migrations.sh`": a real, unrelated safety gap, not just a
documentation one.** Nothing surfaces the need to run it - it's not in the
Quick start flow, and `test_pabc_migrations_guard.py`'s own steady-state
test (`test_guard_leaves_succeeded_job_alone`) *skips* rather than fails
when the Job isn't in a `succeeded` state, so the test suite wouldn't
flag a missing/failed Job either. Worse: `deploy.sh`'s main "apply
everything" step renders and `kubectl apply`s the pabc-migrations Job
completely unfiltered, exactly like every other resource - which is safe
for everything else (Jobs are immutable, so re-applying an *existing* one
is already a no-op) but not for this one, since its container clears
PABC's database before reseeding every time it *runs*. If this Job were
ever deleted while the database still had real data, the very next plain
`deploy.sh` run - not `apply-pabc-migrations.sh` - would silently recreate
it via unguarded `kubectl apply` and wipe that data, completely bypassing
the guard script's whole reason for existing.

Fixed by excluding this Job from the general apply and instead having
`deploy.sh` call `apply-pabc-migrations.sh` (without `--force`) as its own
explicit, later step - safe to call unconditionally every run, since the
script itself already no-ops if the Job succeeded, safely creates it if
genuinely missing/empty, and safely refuses (without mutating anything) if
data exists and the Job's missing, requiring a human to decide `--force`
only in that specific case. New post-renderer,
`scripts/lib/exclude-pabc-migration-job.py`, drops the Job by kind+name
from `deploy.sh`'s `render()` output; chained in after
`disable-service-links.py`. Verified live: confirmed the Job no longer
appears in the general render, ran `deploy.sh --full` against the
already-deployed cluster and confirmed it printed "already exists and
succeeded - leaving it alone" via the new explicit step, the Job's own
`creationTimestamp` was unchanged (not recreated), and the full pytest
suite still passed 41/41.

**Investigated readiness for podiumd's in-progress `objecten`+`objecttypen`
→ `openobject` merge** (upstream: Maykin merged the Objects API and
Objecttypes API into one Open Object 4.0 app; podiumd's own umbrella chart
has an uncommitted feature branch, `feature/objecten-merge-podiumd-4.9.0`,
in the sibling `dimpact-samenwerking/alt_helm-charts` checkout, replacing
the two subcharts with one `openobject` dependency aliased back to
`objecten`). Not yet published to the `dimpact/podiumd` Helm repo, so
tested it live via `set-podiumd-version.sh --path` pointed at that
checkout - confirmed real, not just theoretical.

**Found live, via actual `helm template` render (not just reading the
migration doc), two concrete deploy-breaking issues, not merely stale
docs:**
1. `values.yaml`'s `podiumd.objecten.image.tag: "3.6.1"` override only
   overrides the tag; the merged chart's own default `image.repository`
   changed from `maykinmedia/objects-api` to `maykinmedia/open-object`.
   The two combine into `maykinmedia/open-object:3.6.1` - a tag that
   doesn't exist for that repo (open-object starts at 4.0.0). Every
   objecten/objecten-worker pod would sit in `ImagePullBackOff`.
2. The rendered `create-required-objecttypen-secret`'s
   `authorization-token` came out empty - the upstream template moved its
   admin token from a plain `objecttypen.configuration.token` field (gone)
   to a `tokenauth` item with `is_superuser: true`, sourced from
   `objecten.configuration.secrets.create_required_objecttypen_token`. This
   project's values.yaml never populates that key, so the Job would run
   and fail auth against the API.

One thing that turned out to be harmless: confirmed via the same render
that no `objecttypen.local` ingress exists at all once merged, so the
leftover `objecttypen.local` reference in `setup-tunnel.sh`'s `/etc/hosts`
hint is genuinely just stale, not a functional break.

**Also found, mid-fix**: this project's own top-level `objecten:` key
(values.yaml's own compose-profile-equivalent flag, read by
`storage-hooks.yaml` etc.) and the actual passthrough to the podiumd
subchart, `podiumd.objecten:`, are two *different* keys with the same
leaf name - `--set objecten.image.tag=...` silently does nothing (no
error, since the podiumd chart has no `values.schema.json`); the fix has
to target `podiumd.objecten.*`. Cost real debugging time before the
`helm.sh/chart:` label in a same render confirmed which key path actually
reaches the subchart.

**Fixed to support both shapes simultaneously** (so a deploy of the
currently-pinned classic 4.8.1 *and* a deploy pointed at the merged
checkout both succeed, without knowing in advance which one is active):
new `scripts/lib/detect-objecten-shape.sh`, sourced by `deploy.sh`/
`provision-cluster.sh` after `helm dependency update`. Detects the active
shape by checking which subchart directory actually exists inside the
vendored `charts/podiumd-*.tgz` (`podiumd/charts/openobject/` vs. not) -
not by guessing from a version number, since the merge may only exist in
an unpublished/locally-pathed checkout with no meaningful version to key
off. Classic shape keeps today's `--set
podiumd.objecttypen.enabled=true`; merged shape instead sets
`podiumd.objecten.image.tag=null` (confirmed live: nulling a parent
chart's override on the command line correctly falls through to the
subchart's *own* default - here, whatever tag/digest the merged chart
itself pins, so nothing new to go stale here either) and
`podiumd.objecten.create_required_objecttypen_job.enabled=false` (moved
from its classic location, `podiumd.objecttypen.create_required_objecttypen_job.enabled`,
confirmed against the merged chart's own `create-required-objecttypen.yaml`
template).

Verified live: rendered `deploy.sh --full`'s exact `helm template`
invocation against both shapes. Merged shape now resolves to
`maykinmedia/open-object:4.1.0` (post digest-strip) with no
`create-required-objecttypen` Job rendered at all. Classic 4.8.1 shape's
render is byte-for-byte identical to before this change - confirmed via
`diff`, zero regression on the shape this project currently ships by
default.

**Added `scripts/seed-fixtures.sh`: docker-compose-equivalent demo/fixture
data for objecten/objecttypen/openobject.** Prompted by a prior finding
that podiumd-minikube deliberately never seeds objecten/objecttypen data
(`configuration.job.enabled: false`, scoped out at build order step 5).
Investigated whether that gap is even fixable, and how broadly - grepped
every `scripts/docker-compose/imports/*/init.sh` in
`dimpact-zaakafhandelcomponent` for the pattern objecten/objecttypen uses
(`manage.py loaddata` inside a one-shot import container, run after the
app itself is healthy) and confirmed it's the *only* seeded component
using this specific mechanism - everything else there is either a
Postgres-side init script (already replicated in
`postgres/00-create-databases.sql`/`01-seed-fixtures.sh`) or
django-setup-configuration YAML data (already a declarative,
values.yaml-driven mechanism). So this script's scope is deliberately
just the one component for now.

**Also found, independently of the podiumd-chart-level openobject merge
investigated above: `dimpact-zaakafhandelcomponent`'s own docker-compose
stack has *already* migrated to `open-object` (image
`maykinmedia/open-object:4.0.2`, commit `a98d5ae2b`, "chore: upgrade to
Open Object 4.0.2 in Docker Compose")** - independently of whatever
podiumd's own chart-level timeline does. This project's `values.yaml`
is thus already out of sync with its own stated source-of-truth for this
one component, not just anticipating a future podiumd release.

Found the exact pre-merge fixture mechanism by diffing that commit
against its parent (`338edab1b`): `objects-api` and `objecttypes-api` each
had their own `init.sh` (`manage.py loaddata demodata`) and their own
`fixtures/demodata.json`, one per app/database - `objects-api`'s fixture
already carried a local `core.objecttype` cache (6 records, matching the
`import_objecttypes` mechanism documented in the
`openobject-migration.md` doc read earlier), separate from
`objecttypes-api`'s own `core.objecttype`+`core.objectversion` records in
its own database. Post-merge, `open-object`'s single `demodata.json`
combines both into one `core.objecttype`+`core.objecttypeversion` set (86
records total) in one database.

Vendored all three fixtures verbatim (see `NOTES.md` for exact commit
provenance per file - these predate/postdate this vendor dir's own pinned
commit, called out as an explicit exception): `objecten/demodata.json`
(from `objects-api`, commit `16e90ce2a`), `objecttypen/demodata.json`
(from `objecttypes-api`, commit `52976809f`), `openobject/demodata.json`
(from the merged app, commit `a98d5ae2b`).

`scripts/seed-fixtures.sh` reuses `scripts/lib/detect-objecten-shape.sh`
(extended to also export a plain `OBJECTEN_MERGED` boolean, not just the
`--set` array, for scripts that need to branch rather than pass flags
through) to pick the right fixture(s): classic shape seeds both `objecten`
and `objecttypen` Deployments (each from its own app's own fixture, two
separate databases); merged shape seeds the single `objecten` Deployment
(openobject, serving both APIs) from the combined fixture. Mechanism:
`kubectl cp` the fixture into the target pod, then `kubectl exec ...
manage.py loaddata`, then an idempotent (existence-checked) superuser
creation - matching compose's own `init.sh` exactly, confirmed via the
pre-/post-merge `init.sh` diff above (deliberately used the idempotent
superuser-creation form throughout, including for `objects-api`'s target,
even though *that one specific* pre-merge script lacked the existence
check compose's other two already had - no reason to reintroduce a
re-run failure mode compose itself had already fixed elsewhere).

Runs as a separate, manually-invoked script rather than folding into
`deploy.sh`'s rendered manifest, for the same reason
`apply-pabc-migrations.sh` is separate: this has to run *after* the
target pod exists and is ready, which a `kubectl apply` can't express, and
Helm hooks never fire in this project's `helm template | kubectl apply`
deploy flow anyway (see `deploy.sh`'s own header comment).

Verified live (no cluster running at investigation time, so this covers
what's independently confirmable without one): deployment names/labels
(`objecten`, `objecttypen`, both `app.kubernetes.io/name`-matching) and
image paths (`/app/src/manage.py`) confirmed directly against actual
rendered manifests already produced above, for both shapes; shape
detection branch selection confirmed for both classic and merged Chart.yaml
states; all three vendored fixture files confirmed as valid JSON.
**Not yet verified**: an actual `kubectl exec`/`kubectl cp` run against a
live pod (no running cluster at the time) - worth a real end-to-end run
before relying on this beyond what's confirmed here.

**Ran that end-to-end verification live against both shapes - found and
fixed two real bugs neither `helm template` nor a standalone shell check
would have caught.**

1. **`scripts/lib/detect-objecten-shape.sh` misdetected the merged shape
   every time it actually ran inside `deploy.sh`/`provision-cluster.sh`**,
   despite the exact same `tar -tzf ... | grep -q ...` check giving the
   right answer when run standalone in an ad-hoc shell. Root cause: both
   callers set `set -o pipefail`, and `grep -q` exits on its first match -
   SIGPIPE-ing `tar` before it finishes writing - which pipefail then
   reports as the whole pipeline failing, even though grep found exactly
   what it was looking for. An ad-hoc interactive shell doesn't have
   `pipefail` on by default, so this only ever showed up once the fix was
   actually exercised through its real callers - confirmed by reproducing
   the exact same wrong answer with `bash -c 'set -o pipefail; ...'`.
   Fixed by capturing `tar`'s output into a variable first, then grepping
   that (no live pipe, no early-exit SIGPIPE possible). Consequence before
   the fix: `provision-cluster.sh` tried to pre-pull
   `maykinmedia/open-object:3.6.1` (the classic tag combined with the new
   image repo) twice in a row, both times failing with "manifest unknown"
   - a second, independent confirmation of the exact image bug documented
   above, this time surfacing through the detection layer instead of
   values.yaml directly.
2. **`deploy.sh`'s `render()` was always called as `render` or `render -s
   ...` - never `render "$@"`** - so the script's own documented usage
   (`./scripts/deploy.sh --set some.other=value # any extra --set flags
   are passed through`) silently did nothing outside the `--full` path.
   Pre-existing, unrelated to this session's other changes - only
   surfaced because testing the merged shape's `objecten` Deployment
   specifically (without turning on every other profile via `--full`)
   needed exactly this. Fixed by capturing the script's remaining
   positional args into `EXTRA_ARGS` and appending them in `render()`
   after `EXTRA_SETS` and the call-site's own args.

Verified live end-to-end on the real minikube cluster, both shapes,
including a full round-trip through `seed-fixtures.sh`:
- **Classic** (podiumd 4.8.1): deployed already-present `objecten`/
  `objecttypen`; `seed-fixtures.sh` installed 82+14 fixture objects into
  the two separate databases; confirmed via direct ORM queries (28
  `Object`, 6 `ObjectType` in objecten; 6 `ObjectType` in objecttypen;
  `admin` superuser created); re-ran the script a second time to confirm
  idempotency (clean re-run, no errors, no duplicates).
- **Merged** (openobject, via `set-podiumd-version.sh --path` against the
  sibling `alt_helm-charts` checkout): after the two fixes above,
  `deploy.sh` correctly rolled `objecten` to `maykinmedia/open-object:4.1.0`
  (confirmed via `kubectl get deploy -o jsonpath`) and the old pod
  terminated cleanly once the new one passed its readiness probe. Hit
  upstream's own documented startup gate here for real: the container
  refused to start (`SystemCheckError: Upgrading from 3.6.1 to 4.1.0 is
  not possible`) because the objecten database still had the classic
  shape's just-seeded fixture objecttypes, not marked `is_imported=True` -
  exactly the precondition failure `openobject-migration.md`'s section A.6
  describes, confirmed live rather than just read about. Not a bug -
  reset the (test, disposable) `objects` Postgres database fresh
  (`DROP DATABASE`/`CREATE DATABASE ... OWNER objects` +
  `CREATE EXTENSION postgis`, matching `postgres/00-create-databases.sql`)
  to simulate a real fresh openobject install rather than an in-place
  upgrade, which isn't what a fresh podiumd-minikube deploy would ever
  actually do. After that, `seed-fixtures.sh` installed 86 fixture
  objects cleanly (28 `Object`, 6 `ObjectType`, 7 `ObjectTypeVersion`,
  `admin` superuser); re-ran a second time to confirm idempotency there
  too.

**Near-incident, worth recording prominently**: partway through this
verification, `kubectl`'s current-context had silently drifted away from
`minikube` to an unrelated real cluster that is entirely out of scope for
this project. A `deploy.sh` run and a `pabc-migrations` Job delete/recreate
landed there before this was caught, causing real damage on that
out-of-scope cluster. Never determined exactly how/when the context
changed - the user fixed it by switching back to `minikube` manually.
**Every mutating command for the rest of this session explicitly checked
`kubectl config current-context` = `minikube` first and refused
otherwise** - worth considering a permanent guard along these lines in
`deploy.sh`/`seed-fixtures.sh` themselves, not just ad-hoc per-command
checks, so this can't silently recur.

**Followed up on both**, live:

1. **Added `scripts/lib/require-minikube-context.sh`**, sourced by every
   script that runs `kubectl` against this project's cluster
   (`deploy.sh`, `seed-fixtures.sh`, `apply-pabc-migrations.sh`,
   `provision-cluster.sh`, `setup-tunnel.sh`) - refuses with a clear
   message unless `kubectl config current-context` is exactly `minikube`.
   `teardown-cluster.sh` doesn't need it: it only ever calls `minikube
   delete -p minikube` directly (not kubectl), so it can't accidentally
   target a different cluster regardless of kubectl context.
   `set-podiumd-version.sh` doesn't need it either (no kubectl at all -
   pure local Chart.yaml/`helm dependency update`). Verified live: passes
   silently on the real `minikube` context, refuses with a clear message
   under a simulated wrong one.

2. **Reconciled the cluster back to one consistent shape (classic, per
   explicit choice)** - Chart.yaml already reverted to the checked-in
   4.8.1 default earlier; redeployed `objecten` back onto
   `maykinmedia/objects-api:3.6.1` (confirmed via
   `kubectl get deploy -o jsonpath`), rollout completed cleanly, old
   openobject-shape pod terminated on its own once the new one passed
   readiness. `objecttypen` was never touched during the merged-shape
   excursion, so it needed no reconciliation - still healthy, still
   holds its original seeded data. Nothing to clean up on the
   "orphaned `objecttypen`" front either, precisely because classic was
   chosen as the final shape (objecttypen is the active, needed
   component again, not orphaned) - that concern only would have applied
   had merged been chosen instead.

**Found live, while re-seeding classic `objecten` against a freshly-reset
database as part of that reconciliation: a real, confirmed upstream bug
in `maykinmedia/objects-api`, unrelated to anything in this project.**
Re-running `seed-fixtures.sh` failed with `psycopg.errors.UndefinedColumn:
column "service_id" of relation "core_objecttype" does not exist`. Traced
this all the way to the actual root cause rather than assuming it was a
stale/incompatible vendored fixture (the first hypothesis, since the
fixture's `core.objecttype` entries do use an older `service`/`_name`
shape) - confirmed via a completely fixture-free reproduction in the
Django shell:
```python
from objects.core.models import ObjectType
ObjectType(uuid="...", name="test").save()
# -> the exact same UndefinedColumn error
```
This proves the bug is in the app itself, not the fixture: `models.py`
still declares `service` (a required `ForeignKey`, no `null=True`, no
default) and `_name` as real fields on `ObjectType`, but the migrations
actually shipped in the image never create those columns - so *any*
write to `ObjectType`, from any source, fails. Ruled out a stale local
image cache (pulled a fresh copy directly from Docker Hub - byte-identical
to what was already cached, 2 months old either way) and ruled out "just
a 3.6.1 problem" (temporarily bumped the live Deployment to
`objects-api:3.6.2`, the version podiumd's own chart 2.12.1 actually
bundles by default per the `openobject-migration.md` doc read earlier -
reproduced the identical failure there too, then rolled back to 3.6.1 to
match the checked-in pin). Given this, the two options considered
(maintain two versions of the classic fixture; hotpatch the fixture at
apply-time to match the current schema) were both moot - neither touches
the actual problem, since the ORM itself can't write to this table
regardless of what data is provided.

**Fixed `seed-fixtures.sh` to warn and stop cleanly instead of crashing
with a raw traceback on this specific, known failure** - matched by the
exact `UndefinedColumn` message text (`KNOWN_OBJECTEN_BUG_SIGNATURE`),
not skipped unconditionally, so this stops warning on its own the moment
a future `objects-api` version actually fixes it upstream; any other,
genuinely new failure still surfaces its full raw error for real
debugging (verified both paths live: the actual known-bug case shows the
clean warning, a synthetic unrelated failure still falls through to the
raw message). Verified against the live cluster: classic `objecten`
seeding now exits 1 with the clean warning instead of a wall of
traceback; `objecttypen` (a different app, no such bug) still seeds and
holds its data fine independently.

**Replaced Greenmail with Mailpit, made it unconditional, and actually
wired every component's email settings to it** (previously none of them
did - see below). Evaluated the `jouve/mailpit` Helm chart first
(`helm pull` + inspected its `templates/`) and rejected it: it drags in
the full Bitnami `common` library chart (~20 helper templates for
MongoDB/MariaDB/Cassandra/MySQL/PostgreSQL validation this project would
never use) just to template one container - inconsistent with this
project's own established pattern of plain raw templates for every other
single-container piece it manages itself (Postgres, Redis, Keycloak,
Solr, WireMock, and Greenmail itself). Wrote `templates/mailpit/
mailpit.yaml` instead, modeled directly on the greenmail.yaml it
replaces (same Deployment+Service+Ingress-in-one-file shape), using
`axllent/mailpit:v1.30.6`'s own default ports (1025 SMTP, 8025 web UI,
confirmed via `docker inspect`) - no env vars needed at all, unlike
greenmail's explicit auth-disable flag, since mailpit doesn't require
auth by default. Sized well below greenmail's 50m/128Mi/256Mi footprint
(10m/32Mi/64Mi) - a static Go binary, not a JVM.

Deleting `templates/itest/greenmail.yaml` (the whole directory, since it
only had two files) also deleted `opa-tests-job.yaml` by accident -
caught immediately via `git status`, restored with `git checkout --`.
`opa-tests-job.yaml` correctly stays itest-gated in place; only mailpit
moved out to be unconditional.

**Found live, before touching anything: none of the 8 ZGW components
actually pointed at greenmail** - a real, pre-existing gap, not
something this change broke. ZAC pointed at a fake placeholder Mailjet
hostname (`in-v3.mailjet.com`) nothing ever used; the other 7
Maykin-family apps (openzaak, openklant, objecten, objecttypen,
opennotificaties, openarchiefbeheer, openformulieren) all silently
defaulted to their own chart's `localhost:25` - a no-op inside their own
pod. Added `settings.email.host: mailpit` / `port: 1025` to all 7 (new
blocks, none existed before) and repointed zac's `mail.smtp.server`/
`port` from the Mailjet placeholder to mailpit.

**Found live, only after actually sending a test email through the
newly-wired config**: podiumd's own umbrella `values.yaml` sets
`settings.email.useTLS: true` for every one of those 7 apps (a
production-relay assumption) - since this project's own values.yaml
never touched that field, it kept leaking through. Confirmed via
`kubectl exec ... env`: `EMAIL_USE_TLS=True`, `EMAIL_PORT=587` on a pod
that should've had `mailpit`/`1025`/`false` - initially mis-diagnosed as
a rollout issue (`kubectl exec deploy/openzaak` had raced onto the *old*
terminating pod mid-rollout, not the new one) before finding the real
cause. Mailpit doesn't speak STARTTLS, so this had to be explicitly
overridden to `false` on all 7 apps, not left at the chart default.

**Found live, a second and completely different gotcha, while verifying
zac's own SMTP env**: clearing zac's `mail.smtp.username`/`password` to
`""` (mailpit needs no auth) didn't actually clear the live Secret's
stale `fakeMailjetApiKey`/`fakeMailjetApiSecretKey` values on this
already-existing cluster, even though `kubectl apply` reported
`secret/zac configured`. Root cause: the chart's own `secret.yaml` reads
`{{- if .Values.mail.smtp.username }}` - an empty string is falsy, so
the key is omitted from the rendered manifest entirely rather than
rendered as an empty string, and `kubectl apply` never sees it as a
value to actively clear from the *existing* object. Fixed live with a
one-time `kubectl patch --type=json` removing both stale keys directly
(not a values.yaml bug - a fresh deploy that never had a non-empty value
would never hit this; only already-existing clusters with old values
baked into a Secret would). Verified afterward: `kubectl exec ...`
inside zac's container can open a raw TCP connection to
`mailpit:1025` and gets mailpit's real ESMTP banner back.

**Incidental fix, found via a new warning during this same
`helm dependency update` run**: `.venv/` (added earlier this session) has
no `.helmignore` entry, so Helm's chart-directory walk followed its
`python3 -> /usr/bin/python3.11` symlinks and warned about it on every
run. Added `.helmignore` (excluding `.git/`, `.idea/`, `.claude/`,
`.venv/`, `tests/`) - confirmed the warning is gone.

Verified end-to-end, live: a Django `send_mail()` from openzaak's own
shell landed in mailpit, confirmed via mailpit's own `/api/v1/messages`
API (not just "no error raised"). `itest`'s WireMock mappings
(SmartDocuments/KVK/BAG), lost earlier this session to an unrelated
targeted `deploy.sh` invocation that didn't set `itest.enabled=true`,
came back correctly as a side effect of this change's own `--full`
redeploy - confirmed via the same in-pod `ls` check that found them
missing originally. Full test suite: 43/43 passing (`tests/conftest.py`'s
itest-detection fixture switched from `any_pod_named("greenmail")` to
`any_pod_named("opa-tests")`, since mailpit no longer signals itest;
`test_reachability.py`/`test_pods.py` updated to `mailpit`/`mailpit.local`
accordingly).

**Follow-ups, same session.** Added `tests/test_mailpit.py`: sends a
fresh UUID-marked email per test (not a fixed string, so it can't
false-positive on a leftover message from a previous run) and confirms
it shows up both via mailpit's own `/api/v1/messages` API and via a real
headless-Chromium browser check (`page.goto("http://mailpit.local/")` +
asserting the marker text is visible) - same "does the SPA actually
render, not just return 200" reasoning as `test_browser.py`'s ZAC
dashboard check. `conftest.py`'s `browser_type_launch_args` override
extended to also resolve `mailpit.local`.

**Found live, unprompted, while pointing a browser at mailpit.local
directly**: the host's own `/etc/hosts` had a stale, incomplete line
(only 5 of the 15 hostnames this chart's Ingresses can produce - it
predated several profile additions). `setup-tunnel.sh` only ever
*printed* the `sudo tee -a` command rather than running it, so nothing
had kept it in sync. Extracted the hostname list out of
`setup-tunnel.sh`'s own `hosts_line()` into a new shared
`scripts/lib/hosts-line.sh` (avoids the exact kind of duplication that
let this go stale in two places instead of one), and added
`scripts/update-hosts.sh`: idempotent, removes any existing line
matching `zac.local` (catches both a hand-edited line and a previous
run's own line, not just this script's own marker comment) before
appending a fresh one. Couldn't fully verify end-to-end - `sudo` needs
an interactive terminal this session doesn't have; syntax-checked all
three scripts and confirmed `setup-tunnel.sh` still runs correctly
end-to-end with the extracted helper, but `update-hosts.sh`'s actual
`/etc/hosts` write has only been reviewed, not run - left for the user
to run themselves in a real terminal.

**Removed `opa-tests` entirely**, at the user's request. Confirmed the
full scope first: `templates/itest/opa-tests-job.yaml` (the whole
`templates/itest/` directory, since it was the only file left there
after mailpit's own removal), `tests/test_opa_policies.py`, the vendored
`vendor/dimpact-zaakafhandelcomponent/policies/` directory (main + test
`.rego` files, only ever consumed by this one Job's ConfigMaps - nothing
else referenced that path), plus every mention in `values.yaml`'s itest
comment, `README.md`/`tests/README.md`'s coverage tables,
`tests/test_pods.py`'s `ONE_SHOT_JOB_PREFIXES`, `scripts/lib/
disable-service-links.py`'s comment, `CLAUDE.md`'s vendor-directory
description, and `NOTES.md`'s provenance entry. Deliberately did *not*
touch the unrelated `"opa"` container in zac's own bundled pod spec
(`openpolicyagent/opa:1.17.1-static`, `OPA_API_CLIENT_MP_REST_URL`) - a
real runtime authorization sidecar bundled by the zac chart itself,
completely unrelated to this project's own vendored-policy test Job;
confirmed by reading the actual rendered manifest before removing
anything, not just grepping for the string "opa".

Also removed the now-dead `"itest": any_pod_named("opa-tests")` entry
from `conftest.py`'s `enabled_profiles` fixture - nothing else consumed
it once `test_opa_policies.py` (its only reader) was deleted, and
`itest.enabled` still gates something real (the extra WireMock
mappings), so the flag itself stays, just with no dedicated
pod-based detection signal left.

Verified live: rendering with `itest.enabled=true` no longer references
`opa` anywhere except zac's own unrelated sidecar; the old `opa-tests`
Job and its two ConfigMaps were still sitting on the cluster from a
`--full` deploy three weeks ago (`kubectl apply` never prunes resources
that disappear from a later render - the same recurring gotcha as
`objecttypen` and the itest WireMock mappings earlier this session) -
deleted them manually. Full suite: 44/44 passing (45 minus the deleted
opa test).

**Renamed `itest` to `wiremock`**, at the user's request - now that
mailpit and opa-tests have both moved out, this flag's only remaining
effect is wiremock's own extra SmartDocuments/KVK/BAG mappings, so
"itest" no longer described what it does. Renamed the top-level
`values.yaml` flag, `templates/wiremock/configmap-itest-mappings.yaml` →
`configmap-extra-mappings.yaml` (both its filename and its
`.Values.itest.enabled` check), the same check in
`templates/wiremock/deployment.yaml` (two occurrences), and every other
reference: `scripts/deploy.sh`/`provision-cluster.sh`'s `--set` flags,
`README.md`'s profile table, and the profile-list mentions in
`CLAUDE.md`/`tests/README.md`. The per-set ConfigMap names themselves
(`wiremock-kvk-wiremock-mappings` etc.) were never tied to "itest" and
needed no change.

Verified live: rendering with `wiremock.enabled=true` includes the extra
mapping sets, with it unset (default) includes none; deployed `--full`
and confirmed the actual mappings are mounted and served from the real
wiremock pod; full suite 44/44 passing.

**Investigated replacing WireMock (JVM) with something lighter, on the
condition that `vendor/dimpact-zaakafhandelcomponent/wiremocks/` stays
unchanged. Conclusion: not worth doing - decided to leave WireMock as-is,
no changes made.**

Evaluated three candidates:

1. **MockServer** - rejected outright. Its own repo
   (mock-server/mockserver-monorepo) has zero WireMock compatibility
   code, and its expectation schema (`httpRequest`/`httpResponse`,
   distinct matcher types) is structurally different from WireMock's
   (`request`/`response`). The vendored files would need a full rewrite,
   violating the "unchanged" constraint outright.

2. **stubr** (beltram/stubr, Rust) - the strongest candidate by far.
   Explicitly built to consume WireMock JSON stubs directly ("we want to
   be compatible with it" per its own docs), published benchmarks show
   ~3-8MB memory vs WireMock's ~300-410MB (its own `bench/README.md`,
   comparing directly against wiremock 2.31.0) and near-instant cold
   start (332µs vs 5134µs). Actively maintained (releases through mid-
   2026). Cross-checked our *actual* 22 vendored mapping files' feature
   usage against stubr's docs and confirmed support for everything used
   except one thing: read stubr's actual `ResponseStub` struct
   (`lib/src/model/response/mod.rs`) and confirmed it has no
   `proxy_base_url`/`remove_proxy_request_headers` field at all - no
   mention anywhere in its docs either. Since serde silently ignores
   unknown JSON fields, feeding stubr a WireMock proxy stub wouldn't
   error, it would silently serve an empty response instead of actually
   proxying - worse than a hard failure. This directly breaks 2 of
   `brp-personen-wiremock`'s 3 files (`proxy-requests-with-headers.json`,
   `proxy-requests-without-headers.json`), whose entire purpose is
   header-gated live-proxying to the real `brp-personen-mock:5010`
   service - and critically, `brp-personen-wiremock` is core/always-on,
   not itest-gated like the other three mapping sets (kvk/bag/
   smartdocuments, 19 files, all plain static stubs that stubr handles
   fine).

3. **httpmockie** (Tantalor93/httpmockie, Go) - checked at the user's
   request after finding stubr's proxy gap, to see if it fared better.
   It didn't - worse on every axis. Its actual schema (checked
   `docs/specification.md`) is flat and primitive (`path`/`status`/
   `body`/`headers`/`delay`, no `request`/`response` nesting at all)
   despite the README's "similar to Wiremock JSON API" phrasing - no
   method/header/body matchers, no proxy support, one fixed path per
   file with no conditional branching. Every one of our 22 files would
   need a full rewrite, and several (anything needing header-based
   branching or body matching) couldn't be expressed in this model at
   all. Also abandoned: last commit August 2022, 0 stars, 0 forks.

**Considered a hybrid (stubr for the 3 itest-only sets, keep WireMock
just for brp-personen-wiremock's proxying) - looked like the obvious
compromise, but worked out the actual resource math and it doesn't pay
off.** The current design already runs a *single* WireMock pod; the
itest-only mappings are just extra volume mounts on that same pod, not a
separate process. A JVM's memory footprint is dominated by its baseline
heap/metaspace, not by how many small stub JSON files are loaded, so
`wiremock.enabled` toggling barely moves that pod's memory use either
way. The hybrid keeps that same JVM pod unchanged *and* adds a second
pod (stubr) for the itest-only sets - even at ~3-8MB, that's a whole
additional Pod/container/Service, i.e. strictly more total resource
usage than letting the existing JVM pod keep serving those same files
for near-zero marginal cost. Do-nothing wins on resource usage precisely
because the hybrid's savings target was already nearly free.

**Decision: left WireMock completely unchanged.** The only way to
actually reduce footprint below where it is today would be replacing
WireMock's proxy role too (e.g. a plain nginx/Envoy config for
brp-personen-wiremock's 2 header-gated proxy routes instead of a JVM) -
a bigger, separate change, not attempted here. No files changed in this
investigation.

## monitoringLogging: optional alternative metrics/logging implementation

Added the `dimpact-samenwerking/helm-charts` monorepo's `monitoring-logging`
chart (Loki, Alloy, Grafana, kube-prometheus-stack, Prometheus Pushgateway,
Tempo, OpenTelemetry Collector - the same one PodiumD itself uses in
production) as a new, **optional** `Chart.yaml` dependency, gated behind a
new `monitoringLogging.enabled` flag. Off by default: `templates/metrics/`'s
existing raw templates stay exactly as they were, unchanged, and are the
default whenever `metrics.enabled=true`. When `monitoringLogging.enabled=true`
too, the new dependency's own grafana/tempo/otel-collector/kube-prometheus-
stack/loki/alloy/prometheus-pushgateway subcharts supersede those raw
templates instead - never both at once (see `templates/metrics/*.yaml`'s own
`{{- if and .Values.metrics.enabled (not .Values.monitoringLogging.enabled) }}`
guard, added to each). `scripts/deploy.sh --monitoring-logging` turns on both
the flag and its coordinated `--set` overrides (metrics.enabled,
monitoringLogging.enabled, ZAC's OTLP endpoint) together - deliberately kept
out of `--full`'s own set, since this changes *which* implementation backs
the metrics profile rather than adding a new one.

This is the heavier of the two options discussed with the user up front
(full stack, all 7 components, re-tuned for minikube resource efficiency -
`SingleBinary` Loki instead of `Distributed`+MinIO, no AKS node-selector/
storage-class assumptions, anonymous Grafana admin instead of Keycloak
OAuth) rather than a trimmed one that drops Loki/Alloy/MinIO - the user
picked "option 1" explicitly after seeing the resource estimate for both.

`scripts/set-podiumd-version.sh` was extended to move `monitoring-logging`'s
dependency alongside `podiumd`'s: `--path <dir>` now also points
`monitoring-logging` at the sibling `monitoring-logging/` directory next to
whatever podiumd path was given (both charts live side by side in every
`dimpact-samenwerking/helm-charts` checkout seen so far); plain `<version>`
mode takes an optional second argument for `monitoring-logging`'s own
version, since the two charts are independently versioned in the same
monorepo with no formula relating them - the only way to find the exact
co-released version for a given podiumd release is `git show
podiumd-<version>:charts/monitoring-logging/Chart.yaml` in that monorepo
checkout, done once by hand to pick 1.0.13 as this repo's default alongside
podiumd 4.8.1, documented (not automated - stays a one-time lookup, not a
live cross-repo reference) in that script's own header.

### Deploying and verifying this live surfaced a long chain of real bugs

Rendering (`helm template`) alone looked clean early on, but actually
deploying live to the shared minikube cluster (`./scripts/deploy.sh --full
--monitoring-logging`) surfaced problem after problem that no render-only
check would have caught - each fixed in turn, in the order found:

1. **Loki's own validate.yaml rejected the render outright** the first time:
   `compactor.replicas: 1` (copied from a wrong assumption) alongside
   `singleBinary.replicas: 1` trips Loki's own "single binary and
   distributed targets both active" guard - `compactor` is one of several
   Distributed-mode-only components (along with `backend`/`read`/`write`
   and, it turned out, `indexGateway`/`queryScheduler`/`queryFrontend`/
   `distributor`/`querier`/`ingester` too, all defaulted to 2-3 replicas by
   monitoring-logging's own umbrella values.yaml since *it* defaults to
   `deploymentMode: Distributed`) - every one of those needed forcing to 0.

2. **`kubectl apply`, unlike `helm upgrade`, never prunes** - switching
   `monitoringLogging.enabled` in either direction leaves the *other*
   implementation's Deployments/Services/ConfigMaps/Ingress running
   alongside the new ones (confirmed live: the 21-day-old raw-template
   grafana/tempo/otel-collector/prometheus were still running fine
   alongside the new `podiumd-minikube-*`-prefixed ones after the first
   switch-over, exactly the "two Grafanas at once" scenario the flag exists
   to prevent) until manually deleted. Documented as a caveat in
   `values.yaml`'s own `monitoringLogging` comment - no automatic fix
   attempted (would need real pruning logic, out of scope here).

3. **Missing images.** `provision-cluster.sh` wasn't re-run after adding the
   dependency, so none of monitoring-logging's own images were pre-pulled -
   15 images needed pulling/loading by hand the first time (kube-state-
   metrics, node-exporter, alloy, grafana, loki, tempo, k8s-sidecar,
   busybox, nginx-unprivileged, kube-webhook-certgen, otel-collector-contrib,
   prometheus-operator/prometheus/pushgateway). `provision-cluster.sh`'s own
   image-derivation render now includes `monitoringLogging.enabled=true` so
   this is pre-pulled automatically going forward, even for users who never
   enable the flag themselves (a separate, explicit opt-in per
   `deploy.sh --monitoring-logging`'s own comment).

4. **Grafana's `initChownData` initContainer permanently breaks itself
   after its own first successful boot.** It runs with `capabilities: {add:
   [CHOWN], drop: [ALL]}` - enough to `chown` a *fresh* volume (root:root,
   world-readable) but not enough to even traverse the `csv`/`pdf`/`png`
   export directories Grafana itself creates `0700` at runtime afterward
   (lacks `CAP_DAC_OVERRIDE`/`CAP_DAC_READ_SEARCH`, so "root" can't bypass
   permission bits) - every restart *after* the first hits "Permission
   denied" and sits in `Init:Error` forever, not something that self-heals.
   Fixed by disabling `initChownData` entirely - confirmed live that the
   pod-level `fsGroup: 472` (already set by the chart's own default) is
   enough on its own, restarting cleanly with the initContainer off.

5. **`helm template` never renders a chart's `crds/` directory** - only
   `helm install`/`helm upgrade` install CRDs automatically, and this
   project never runs either (the whole reason for `kubectl apply` instead,
   see `deploy.sh`'s own header: Helm's release record would exceed
   Kubernetes' 3MB API request-size limit). Every
   Prometheus/PrometheusRule/ServiceMonitor/PodMonitor object failed with
   "no matches for kind ... ensure CRDs are installed first" - invisible at
   first because of finding #9 below. Fixed with a new
   `scripts/lib/apply-monitoring-logging-crds.sh`: extracts
   `charts/monitoring-logging-*.tgz` (already fetched by `helm dependency
   update`, no live cross-repo reference) to a temp dir, finds every file
   whose content declares `kind: CustomResourceDefinition` (filtering by
   content, not by guessing which nested `crds/` directory convention
   applies - they're scattered across kube-prometheus-stack's own nested
   "crds" subchart, alloy's, and loki's bundled rollout-operator/grafana-
   agent-operator subcharts, at several different nesting depths), and
   `kubectl apply --server-side`s all of them (some of kube-prometheus-
   stack's own CRDs are large enough to hit the same client-side apply
   annotation limit as finding #7 below). `deploy.sh --monitoring-logging`
   now runs this before the main manifest apply.

6. **A stale operator process never notices CRDs installed later.**
   controller-runtime-based operators (kube-prometheus-stack's Prometheus
   Operator here) check CRD availability once at their own startup and
   don't retry live - since the operator pod in this session had already
   started *before* finding #5 was fixed, it needed a one-time manual
   restart (`kubectl delete pod -l app=kube-prometheus-stack-operator`) to
   actually start reconciling the `Prometheus` CR into a real StatefulSet.
   Not a template/values fix - only relevant because CRDs were added
   mid-session after the operator was already running; a fresh deploy from
   scratch (CRDs applied before the operator Deployment is ever created)
   wouldn't hit this.

7. **`kubectl apply`'s client-side `last-applied-configuration` annotation
   caps out at 262144 bytes** - monitoring-logging's own bundled Grafana
   dashboards ConfigMap (`templates/metrics-dashboards.yaml`, packing 9
   full dashboard JSON exports into one object via `.Files.Get`) blows past
   that on its own, well under Kubernetes' own much higher per-object size
   limit (the object itself is perfectly valid - only kubectl's own
   annotation-based 3-way-merge bookkeeping rejects it: "metadata.annotations:
   Too long"). First (wrong) diagnosis assumed the ConfigMap was missing
   entirely and needed a hand-rolled stand-in - it wasn't; the chart
   already creates it correctly, just too big for plain `kubectl apply`.
   Fixed with a new `scripts/lib/split-large-configmaps.py` post-renderer:
   pulls any ConfigMap over 200000 bytes out of the main stream into a side
   file, applied separately via `kubectl apply --server-side` (which
   doesn't use that annotation at all) right after the main apply.

8. **Helm test hooks left a permanently-failed Pod behind.**
   `<release>-grafana-test` (`helm.sh/hook: test`) has no real Helm release
   to ever run it properly under this project's `kubectl apply`-only model
   (same root cause as pabc-migrations' own exclusion) - applied as a plain
   resource, it's a Pod that runs once, fails (no readiness/DNS guarantees
   at the moment `kubectl apply` happens to create it), and then sits in
   `Error` forever, which would fail `tests/test_pods.py`'s "every pod is
   Running/Succeeded" check. Fixed with a new
   `scripts/lib/exclude-helm-test-hooks.py` post-renderer, dropping any
   resource annotated `helm.sh/hook: test` (deliberately *not* excluding
   pre-install/post-install hooks too - some of those, like the admission-
   webhook cert-generation Jobs below, need to actually run).

9. **`deploy.sh`'s own error-detection only ever recognized ONE specific
   error shape** (`grep -c "error when applying patch"`, matching just the
   expected "spec is immutable" storage-hooks case) - every genuinely new
   failure in this whole investigation (findings #5, #10, #11 below) used
   different wording and slipped through *uncounted*, since the count still
   matched `expected_errors` by coincidence. A first attempt to fix this by
   computing failures structurally (total rendered resources minus
   successful `created`/`configured`/`unchanged` result lines) itself
   undercounted for a reason not fully run down. Landed instead on matching
   every known error-line *shape* directly: server-side rejections all
   start `Error from server (`; client-side ones (kubectl refuses before
   reaching the API server - missing CRDs, cross-namespace conflicts) don't
   share that prefix, so a second pattern
   (`no matches for kind|does not match the namespace|ensure CRDs are
   installed|cannot be handled as`) catches those. Not exhaustive against
   every conceivable future error shape, but no longer silently trusts a
   coincidental count match either.

10. **A version-skew bug inside monitoring-logging's own dependency tree**
    (not introduced here): its `kubelet` ServiceMonitor sets
    `spec.endpoints[1].trackTimestampsStaleness`, a field the CRD schema
    actually bundled in that same dependency's own "crds" subchart doesn't
    recognize - rejected outright by the API server's strict decoding.
    Fixed by disabling `kube-prometheus-stack.kubelet.enabled` - minikube's
    own control plane runs as static pods with no scrapeable kubelet
    Service of its own anyway, so nothing real was lost.

11. **Five more kube-prometheus-stack Services hardcoded to `kube-system`**
    (`coreDns`, `kubeControllerManager`, `kubeEtcd`, `kubeProxy`,
    `kubeScheduler` - scraping the control-plane components that, on a
    "real" cluster, run there) - conflicts outright with `deploy.sh`'s own
    single `kubectl apply -n podiumd-minikube -f -` covering the whole
    manifest at once ("the namespace from the provided object 'kube-system'
    does not match the namespace 'podiumd-minikube'"). Disabled all five -
    wouldn't have scraped anything real on minikube regardless.

12. **Loki's query path failed on every single query** ("too many unhealthy
    instances in the ring"), while the write path worked fine throughout -
    `loki.commonConfig.replication_factor` defaults to 3, assuming
    Distributed mode's normal multi-replica setup; with
    `singleBinary.replicas: 1`, every ring it feeds (ingester, compactor,
    scheduler, pattern-ingester) only ever has one real member, so the
    query path's own quorum math ("need N healthy out of replication_factor
    total") always came up short even though that one member was genuinely
    `ACTIVE` the whole time. Fixed by setting
    `loki.commonConfig.replication_factor: 1`.

13. **Alloy shipped zero logs the entire session** - not a crash, just
    silently zero targets discovered. Root cause: an *invented* values key.
    An earlier pass (before this file's own findings-in-order list starts)
    added a `monitoringLogging.alloy.logCollectionNamespaces` override,
    assuming such a structured field existed - it never did, anywhere in
    this chart; Alloy's entire log-collection pipeline is one literal
    River-config *string* (`alloy.alloy.configMap.content`) hardcoding
    `namespaces { names = ["podiumd", "monitoring"] }` as a server-side API
    watch filter, and Helm silently drops values keys nothing references,
    so the invented override was a no-op the whole time with no error ever
    raised. Fixed by actually overriding `configMap.content` with the same
    River config, copied verbatim except that one line
    (`names = ["podiumd-minikube"]`) - confirmed only fixable this way since
    it's a plain string value, not a structured list Helm could merge.

Two more of these (findings #4/alloy's own AKS-only nodeSelector,
`kubernetes.azure.com/agentpool: userpool`) turned out to share the exact
same underlying limitation as finding #9's investigation revealed in
passing: clearing a map key this deep in a subchart-of-a-subchart tree
(root chart's own values.yaml → monitoring-logging's own values.yaml →
alloy's own values.yaml) doesn't reliably work via `null` on this project's
pinned Helm v3.9.0, confirmed live across several isolated tests - neither
an empty-map override nor a `null` on the exact key clears it, even though
`--set` can reach and *overwrite* the same key's value just fine. Rather
than fight that further, the nodeSelector case was solved by making
minikube's own node satisfy the selector instead
(`kubectl label node minikube kubernetes.azure.com/agentpool=userpool`, now
in `provision-cluster.sh`, unconditionally - harmless if monitoringLogging
is never enabled).

### Verified live, end to end

After all of the above: `helm template` renders cleanly in both modes
(default off = raw templates only, `monitoringLogging.enabled=true` = new
stack only, confirmed no leftover AKS/managed-csi/kube-system assumptions);
a full `deploy.sh --full --monitoring-logging` applies with zero
unrecognized errors and every pod `Running`/`Completed`; Grafana is reachable
at `http://grafana.local/` and all three datasources (Prometheus, Loki,
Tempo) report healthy; Prometheus's own `/api/v1/targets` shows every scrape
target `up`, including the two explicit `additionalScrapeConfigs`
(`zac-admin`, `tempo`); Loki's query path returns real results (not just a
"success" envelope with no data); Alloy actually discovers and forwards pod
logs for the right namespace. Not yet re-run against the full `tests/`
pytest suite with this flag on - that's the next thing to do before calling
this fully done.

## deploy.sh: pruning workloads that dropped out of the render

`kubectl apply` (this project's whole reason for not using `helm
install`/`upgrade` - see deploy.sh's own header) never deletes anything -
it only adds/updates whatever's in the current render. Toggling a profile
or `monitoringLogging.enabled` off leaves its Deployments/StatefulSets/
DaemonSets running forever, since nothing about the new render mentions
them. Added `scripts/lib/prune-orphaned-workloads.py`, run as deploy.sh's
last step: diffs the live cluster's Deployment/StatefulSet/DaemonSet
against the current render's own set and deletes whatever's left over
(cascades to their Pods via normal Kubernetes GC).

Verified live (read-only dry-run against the real cluster, deploy.sh
itself not actually re-run - see [[project-podiumd-minikube-cluster-caution]]):
running the diff against the currently-deployed `--full
--monitoring-logging` state surfaced two real findings before this was
wired in for real:

- **A genuine orphan, confirmed correct**: `Deployment/greenmail` - dead
  leftover from before greenmail was replaced by mailpit (see that
  commit), exactly the case this feature exists to catch.
- **A false positive that had to be fixed first**: kube-prometheus-stack's
  own operator creates `prometheus-<release>-kube-prom-prometheus` (a
  StatefulSet) at runtime from a `Prometheus` custom resource - only the
  CR itself is ever in the render, never the StatefulSet the operator
  spawns from it. A naive kind+name diff flagged it as orphaned every
  time, which would have deleted (then had the operator immediately
  recreate) Prometheus's own StatefulSet on every single deploy.sh run.
  Fixed by skipping any live object carrying a controller ownerReference -
  the same reasoning Job/CronJob pruning was already excluded for
  (CronJob-spawned Jobs are never themselves part of any render either),
  generalized instead of special-cased per-kind.

Job/CronJob are deliberately excluded from the prunable kinds entirely,
not just filtered by owner ref: `pabc-migrations` is excluded from the
render on purpose (see `exclude-pabc-migration-job.py`) and would look
orphaned on *every* run if Job were included, deleting the very Job
`apply-pabc-migrations.sh`'s own guard exists to protect; and
`storage-permissions-fix` is already unconditionally deleted/recreated
earlier in deploy.sh.

Unrelated but hit while testing this live: the minikube container OOM-killed
itself again (`OOMKilled: true`, exit 137) - same failure mode as the
2026-07-15 crisis, this time on plain restart under the full
`--monitoring-logging` load. Its docker memory cap was still 12GiB;
raised to 20GiB (`docker update --memory=20g --memory-swap=20g minikube`)
against the host's 31GiB/16GiB-free at the time and it started cleanly.
Host disk is also sitting at ~90% capacity (9.8G free) - not yet a hard
blocker, but close enough to flag if anything starts failing on disk space
next.

## tests/: monitoringLogging profile checks

Added `tests/test_monitoring_logging.py`, gated the same way as every other
optional-profile test file (`enabled_profiles` fixture in `conftest.py`,
autouse skip) - except its gate isn't just "is the profile on", since
`monitoringLogging` doesn't add a new profile, it swaps which implementation
backs the existing `metrics` one (see values.yaml's own comment). Added a
dedicated `enabled_profiles["monitoringLogging"]` key, detected via the
`podiumd-minikube-grafana` pod name prefix - unique to the dependency's own
subcharts (release-name-prefixed), never matched by
`templates/metrics/grafana.yaml`'s raw-template Deployment (plain
`grafana`). This keeps `test_metrics.py` (raw templates) and
`test_monitoring_logging.py` (the dependency) mutually exclusive - confirmed
live running both together: `test_metrics.py` skips ("'metrics' profile is
not deployed" - accurate, from its own narrower `any_pod_named("grafana")`
check), `test_monitoring_logging.py` runs and passes.

Three checks, verified against the real cluster (first by hand via
`kubectl port-forward` to Grafana directly to nail down the exact API
shapes before writing anything, then for real through Traefik once the
tunnel bug below was fixed):
- Grafana's provisioned datasources are exactly `{"Prometheus", "loki",
  "Tempo"}` - note lowercase "loki", copied verbatim from
  values.yaml's own datasource name, unlike the other two.
- Prometheus's own scrape targets are all `up`, including the two explicit
  `additionalScrapeConfigs` jobs (`zac-admin`, `tempo`).
- Loki actually holds real log streams for `{namespace="podiumd-minikube"}`
  - not just that it answers queries, which an empty-but-"success" result
  would also satisfy while hiding a completely broken Alloy pipeline. This
  one has no raw-templates equivalent at all (that implementation has no
  log-shipping story).

### Found and fixed along the way: `setup-tunnel.sh`'s stale-IP false positive

Hit while trying to actually run the new tests through Traefik rather than
a port-forward: `./scripts/setup-tunnel.sh` kept reporting "tunnel appears
to be running already" and exiting immediately, but requests to Traefik's
reported external IP just hung. Root cause: the script's own "already
running" check only looked at whether the `traefik` Service had *any* IP
recorded in `status.loadBalancer.ingress` - but confirmed live that this
field survives the actual `minikube tunnel` process dying (nothing
un-assigns it), while the host-side route that process was maintaining
does not. An IP left over from a tunnel that died in an earlier session
looks identical to a healthy one by that check alone.

Fixed by checking for a live `minikube tunnel` process first (`pgrep -f`)
as the real signal, and only trusting an existing IP when one is actually
running - if no process is alive, a recorded IP is now treated as stale and
a fresh tunnel is started regardless. Verified live: killed/lost tunnel
process + stale IP reproduced the original hang; after the fix, the same
state correctly triggers a fresh `minikube tunnel` start, and once that's
actually up, requests to Traefik succeed (`curl -H "Host: grafana.local"`
returns real `200`s) and the full test file passes end to end.

## reset-namespace.sh: emptying the namespace without deleting the cluster

Added `scripts/reset-namespace.sh` - `teardown-cluster.sh` deletes the
whole minikube VM, which is often more than needed just to get back to a
clean slate for re-testing `deploy.sh` from scratch. Deletes the
`podiumd-minikube` namespace (confirmation prompt + `--yes`, same UX as
teardown-cluster.sh), then cleans up what a namespace delete alone never
touches, since none of it is namespaced.

Run for real against the live cluster (not just dry-run) and found two
things worth recording:

- **Expected**: the six `Retain`-policy PVs storage-hooks.yaml creates
  (openzaak/openklant/opennotificaties/openarchiefbeheer/openformulieren/
  objecten) survived the namespace deletion exactly as documented - `Retain`
  means never auto-deleted, by design.
- **Not expected**: every *dynamically*-provisioned PV (postgres, solr,
  grafana x2, loki, tempo, kube-prom-prometheus - 7 total) got stuck in
  `Released` too, despite having reclaim policy `Delete`. minikube's own
  `storage-provisioner` pod is supposed to reclaim these automatically once
  their PVC is gone - confirmed live it just didn't, for any of them, after
  the namespace's cascading PVC deletion (that pod's own 7 restarts over 25
  days suggest it's not entirely stable). Left alone this is silent
  leftover cruft, not a functional blocker (a stale `Released` PV doesn't
  stop a *new* PVC from getting a fresh dynamically-provisioned volume) -
  but real disk usage nonetheless (confirmed live: ~24Gi of requested
  capacity across the seven, though actual on-disk usage was far smaller,
  116M, once found).

Fixed by not hardcoding a PV name list at all - queries every PV's
`spec.claimRef.namespace` instead and deletes any that point at
`podiumd-minikube`, which catches both cases (the explicit Retain ones and
whatever Delete ones the provisioner missed) with one mechanism, and
degrades gracefully to "none found" if nothing needs it. Also clears the
hostPath data under both `/data/podiumd-minikube` (storage-hooks.yaml's
own PVs) and `/tmp/hostpath-provisioner/podiumd-minikube` (minikube's
dynamic provisioner's own directory, confirmed live via one of the
Released PVs' own `spec.hostPath.path`) on the minikube node itself -
deleting a PV object never touches its backing directory.

Also deletes monitoring-logging's own cluster-scoped RBAC/webhook objects
via their `app.kubernetes.io/instance=podiumd-minikube` label (confirmed
live present on all of them) - only fires if that dependency was ever
enabled. Deliberately leaves the CRDs `apply-monitoring-logging-crds.sh`
installs alone - they carry no such label (applied raw from the
dependency's own tarball, never templated) and are harmless to leave
installed regardless.

Verified live end to end, twice: first run against the real cluster
correctly emptied everything and surfaced the Released-PV gap above; after
fixing it, a second run against the now-already-empty cluster exited
cleanly and idempotently ("(none found)" for stale PVs, "No resources
found" for cluster-scoped RBAC).

## deploy.sh --monitoring-logging: a flag with no purpose once the version arg went mandatory

`set-podiumd-version.sh`'s second argument (the monitoring-logging one)
used to be genuinely optional - omit it and monitoring-logging silently
stayed disabled, which was easy to do by accident. Once that was fixed
(mandatory now: an explicit monitoring-logging version, or
`--disable-monitoring-logging`), `values.yaml`'s `monitoringLogging.enabled`
became a reliably-set, persistent piece of state - at which point
`deploy.sh --monitoring-logging` turned out to be redundant: it just forced
`monitoringLogging.enabled=true` (and `metrics.enabled=true`) at deploy
time, duplicating a decision that's already made and already persisted.
Worse, having both a persistent value.yaml flag and a separate per-deploy
CLI flag meant they could disagree - e.g. `set-podiumd-version.sh` sets it
false, but a stale muscle-memory `--monitoring-logging` on the next
`deploy.sh` call flips it back true for that one render.

Fixed by removing the flag entirely. `deploy.sh` now reads
`values.yaml`'s `monitoringLogging.enabled` directly (new shared helper,
`scripts/lib/monitoring-logging-enabled.sh`, also used by
`show-podiumd-version.sh` so both stay in agreement) and, if true,
automatically does the two things that implementation needs at deploy time
that Helm's own templates can't express on their own: applies
monitoring-logging's CRDs first, and repoints ZAC's OTLP endpoint at its
otel-collector Service. `metrics.enabled` stays fully independent, exactly
as before (`--full` or an explicit `--set`) - `monitoringLogging.enabled`
only ever picks *which* implementation backs that profile, never whether
it's on.

Verified all three cases live via an isolated logic harness (not the real
`deploy.sh`, to avoid touching the actual shared cluster for a change
that's pure argument-parsing): no flags with `monitoringLogging.enabled:
true` in `values.yaml` correctly adds the OTLP override; adding `--full`
on top still adds it; and a copy of `values.yaml` with it set to `false`
correctly adds neither.

## Two live-only bugs found verifying monitoring-logging actually works end to end

Bumped podiumd to 4.8.3 / monitoring-logging to 1.0.14 (the actual co-
released pair, looked up the same way as before) and ran a full fresh
`deploy.sh --full` specifically to verify the deploy.sh auto-detection
change above actually results in ZAC's traces landing in monitoring-
logging's Tempo, not just that the pods come up. Two real bugs surfaced,
neither reachable by reading source alone:

**Solr crashlooped on a genuinely fresh PV.** `cp: cannot create directory
'/var/solr/data/zac': No such file or directory`. `templates/solr/
deployment.yaml` used `command: ["solr-precreate", "zac"]`, which Kubernetes
maps to Docker's `--entrypoint`, replacing the `solr:9.10.1-slim` image's
own `docker-entrypoint.sh` (confirmed live via `docker inspect` inside
minikube) instead of layering on top of it. That entrypoint does the
image's first-run setup, including creating `/var/solr/data` - skipped
entirely, so `solr-precreate` had nowhere to write. Never caught before
because this project's own dev PV already had `/var/solr/data` from months
of prior runs; only a truly fresh PVC (this deploy's, and reset-namespace.sh's
own use case) hits it. Fixed: `args:` instead of `command:` - only
overrides the image's default CMD, leaving the real entrypoint's setup
intact. Verified live via a direct `kubectl patch` before committing to the
template edit.

**ZAC's OTel SDK was disabled the entire time, regardless of any endpoint
override.** `charts/zaakafhandelcomponent`'s own `templates/config.yaml`
only renders `OTEL_SDK_DISABLED`/`OTEL_EXPORTER_OTLP_ENDPOINT` at all inside
`{{- if index .Values "opentelemetry-collector" "enabled" }}` - and this
project pins that exact key to `false` (to avoid deploying that chart's
own separate bundled otel-collector). So `podiumd.zac.
opentelemetry_zaakafhandelcomponent.endpoint`, and the whole "repoint ZAC's
OTLP endpoint" mechanism `deploy.sh` sets, had never actually taken effect
on any podiumd version - confirmed live: zero `OTEL_*` keys in the rendered
zac ConfigMap either way, before this fix.

Fixed by flipping `opentelemetry-collector.enabled: true` under `podiumd.zac`
to unlock those two env vars - but that's the *same* key Chart.yaml's own
`condition:` uses to decide whether to deploy that chart's separate bundled
otel-collector subchart, so flipping it also turns that on. Two more bugs
surfaced fixing that one:
- Its default fullname (release name + chart name, both literally
  "opentelemetry-collector") collides exactly with monitoring-logging's own
  otel-collector Deployment name. Since this project applies via `kubectl
  apply`, not a real Helm release, same-name resources don't merge - the
  one rendered later in the manifest stream just overwrites the earlier
  one's live spec outright. Verified live: without a `fullnameOverride`,
  zac's copy rendered after monitoring-logging's real one and would have
  silently replaced it (i.e. broken the whole pipeline this change was
  meant to fix). Fixed with `fullnameOverride: zac-unused-otel-collector`.
- `replicaCount: 0` looked like the obvious way to stop that unwanted
  Deployment from actually running a pod, but isn't: that chart's own
  `deployment.yaml` only emits `replicas:` at all `{{- if and (not
  autoscaling.enabled) (replicaCount) }}` - 0 is falsy in a Go template
  `if`, so the field is omitted entirely rather than rendered as `0`, and
  Kubernetes defaults an omitted `replicas:` to 1. Fixed with an impossible
  `nodeSelector` instead (`podiumd-minikube.local/never-schedule: "true"`) -
  the Deployment exists (satisfying the `enabled` condition upstream) but
  its pod sits permanently `Pending`/`Unschedulable` on this single-node
  cluster, confirmed live, and never actually runs.

End-to-end verification, not just config inspection: after all four fixes,
queried Tempo's own search API directly (`kubectl port-forward` +
`/api/search`) and found real spans rooted at `zac` -
`GET SolrReadinessHealthCheck`, `GET OpenZaakReadinessHealthCheck`, `GET
PabcReadinessHealthCheck` - generated by ZAC's own health-check probes,
proving traces actually reach Tempo now, not just that the SDK reports
itself as configured.

### Follow-up: full test suite run surfaced two more small things

Running `tests/` end-to-end against the above (44 passed, 3 skipped, 0
failed once both were fixed):

- `test_pods.py`'s blanket "every pod is Running/Succeeded" check flagged
  the permanently-`Pending` `zac-unused-otel-collector` placeholder from
  the nodeSelector trick above - not a real problem, but not nothing
  either. Replaced that approach with `scripts/lib/
  exclude-zac-bundled-otel-collector.py`, a Helm post-renderer matching
  this project's existing `exclude-pabc-migration-job.py`/
  `exclude-helm-test-hooks.py` pattern - drops that subchart's resources
  from the manifest entirely, keyed off the `fullnameOverride` already set
  in values.yaml, so there's no pod object at all. Verified live: the old
  Deployment even got picked up and removed automatically by
  `prune-orphaned-workloads.py` on the next `deploy.sh` run.
- `test_login_flow.py`/`test_browser.py` both failed on a genuine
  credential mismatch, confirmed via a direct OAuth password-grant token
  request and Keycloak's own brute-force-detection API (not a lockout -
  0 recorded failures, a real wrong password). Root cause, found by
  comparing the live user's `createdTimestamp` against the vendored realm
  JSON's: this test user isn't live state that can drift on its own at
  all - it's baked into `vendor/dimpact-zaakafhandelcomponent/keycloak/
  zaakafhandelcomponent-realm.json` (including its password credential
  hash) and re-imported identically by Keycloak on every single startup.
  The tests had the wrong password hardcoded (`minikube-test-1234`); the
  real one baked into that file is `beheerder1newiam`, confirmed against a
  live token request before changing anything. Fixed both test files, and
  corrected `test_login_flow.py`'s own docstring, which incorrectly
  suggested resetting the password via the Admin API - that only patches
  the current pod's live state and gets silently reverted by the next
  fresh realm import, so it was never a real fix for this specific case.

## Double-checking everything above from a genuine reset-namespace + redeploy from scratch

Ran `reset-namespace.sh --yes` followed by a fresh `deploy.sh --full`, to
confirm the solr/OTel fixes above hold on a truly empty namespace, not
just an incrementally-redeployed one where old state could be quietly
masking a problem.

First attempt hit an unrelated blocker: the podiumd dependency was pointed
(via `--path`) at `dimpact-samenwerking/helm-charts`'s `feature/podiumd-4.8.4`
branch (WIP, not a release), which fails to render at all - `error calling
include: ... executing "common.names.fullname" at <.Release.Name>: invalid
value; expected string`, inside `openobject`'s own redis-subchart wiring.
Reproduced with a plain `helm template`, confirming it's a bug in that
upstream WIP branch itself, unrelated to anything in this project. Switched
back to the stable, already-verified `podiumd 4.8.3` / `monitoring-logging
1.0.14` pair to complete the check rather than debug someone else's
in-progress branch.

With that: `solr` came up `1/1 Running` immediately on the fresh PV (no
crashloop - confirms the `args:` fix holds cold, not just on a PV that
happened to already have `/var/solr/data`), and no `zac-unused-otel-
collector` pod appeared at all (confirms the exclude-post-renderer fix).
`pabc` and `zac` both went through the same self-resolving startup race
seen before (pabc's init container waits on a migrations Job that doesn't
exist yet at pod-creation time; zac waits on solr's core) and settled on
their own within a few minutes. Full suite: 44 passed, 3 skipped, 0 failed
(one `test_browser.py` flake on the very first pass, from PABC's
authorization data barely having finished migrating - passed cleanly on
retry, not a real regression).

## prune-orphaned-workloads.py didn't cover kube-prometheus-stack's own CRs

Found disabling `monitoringLogging.enabled` (as part of measuring its
resource-usage tradeoff - see below) and redeploying: the flip correctly
pruned the raw Deployment/StatefulSet/DaemonSet objects that dropped out
of the render (Grafana, Loki, Alloy, kube-prom-operator, kube-state-
metrics, otel-collector, Pushgateway, node-exporter), but a whole
`Prometheus` StatefulSet (2/2, genuinely running, non-trivial memory) plus
~25 `PrometheusRule`/`ServiceMonitor`/`PodMonitor` custom resources stayed
behind - the exact same "`kubectl apply` never deletes what drops out of
the render" gap `prune-orphaned-workloads.py` already existed to solve for
Deployments, just never extended to these CR kinds. Root cause: that
script's own `PRUNABLE_KINDS` only ever listed `Deployment`/`StatefulSet`/
`DaemonSet`; `Prometheus`/`PrometheusRule`/`ServiceMonitor`/`PodMonitor` are
themselves top-level, Helm-rendered, owner-less resources (confirmed live -
none of the four have their own `ownerReferences`), exactly analogous to a
Deployment, so it's safe to add them the same way.

Fixed by adding all four to `PRUNABLE_KINDS`. The orphaned `Prometheus` CR
itself now gets deleted directly; its owned StatefulSet+Pod then cascade-
delete automatically via plain Kubernetes garbage collection (no separate
handling needed - that's exactly the ownerReference check the script
already had, working as designed once the actual owner is in scope).
Had to guard each `kubectl get <kind>` against "the server doesn't have a
resource type" specifically (not a blanket non-zero-exit swallow) since
this script runs unconditionally on every `deploy.sh` call, including on a
setup that's never enabled `monitoringLogging` at all and so never
installed these CRDs in the first place (see
`apply-monitoring-logging-crds.sh`/`reset-namespace.sh`'s own header for
why they're only ever applied, never removed).

Verified live: re-ran the exact same render + prune pipeline by hand,
confirmed all ~26 leftover objects deleted and the StatefulSet's pod gone
with them; a subsequent full `deploy.sh --full` came back clean and
idempotent ("No orphaned workload(s)/monitoring CR(s) found").

## Measuring monitoringLogging's real resource cost, and disabling it by default

No `metrics-server` is installed in this cluster, so `kubectl top` isn't
available - measured instead via `docker stats minikube --no-stream` (real
usage, docker driver) and `kubectl describe node`'s "Allocated resources"
(requests/limits), settled ~5 minutes post-deploy on both sides for a fair
comparison (CPU in particular is bursty enough right after a deploy to be
meaningless otherwise).

Real memory usage dropped from ~89% to ~85% (~17.8Gi → ~17.0Gi on a
20Gi-capped container) with `monitoringLogging.enabled` off - a genuine
saving, but far more modest than "a dozen fewer pods" suggests. Most of
what that flag adds is lightweight (kube-state-metrics, prometheus-
operator, node-exporter, Pushgateway); the real weight is Loki + Alloy +
Grafana + kube-prometheus-stack's own Prometheus, and those are already
re-tuned for a single-node box. Declared *limits* dropped far more sharply
(8308Mi → 5652Mi) than real usage did, since limits are ceilings, not
actual consumption - worth calling out explicitly so the numbers aren't
misread. Documented as a before/after table in README.md's new "Resource
usage" section.

Given that comparison, and that today's testing was the only reason it had
been left on, reverted `monitoringLogging.enabled` to `false` as the
committed default - matching this project's original intent (see
values.yaml's own comment on that key) and what most day-to-day dev
sessions actually need. Still fully available via `set-podiumd-version.sh
<version> <monitoring-logging-version>` for whoever needs to test that
implementation specifically. Full suite re-run clean against this default

## Django-admin credential logins: objecttypen, then openzaak/opennotificaties

Started from a simple ask (access objecttypen's django-admin) and ended up
finding + fixing the same underlying bug across four apps.

**objecttypen**: no superuser existed at all (`configuration.superuser` was
never set) and, once added, login still failed with a CSRF error - the
chart's session/CSRF cookies default `Secure`-only (no dedicated
`settings.isHttps`/cookie field on this chart, unlike others), so the
browser silently drops both over our plain `http://objecttypen.local`
ingress. Fixed via `extraEnvVars: IS_HTTPS=False`.

Asked "are there other django-admin interfaces that need a test?" -
checked every django-admin login in the stack the same way (actually
submitting credentials, not just checking the login page returns 200) and
found **openzaak, opennotificaties, and openformulieren already have
superuser credentials configured (matching compose) but their admin login
is currently broken by the exact same class of bug** - none of them had
ever actually been logged into via credentials before (only OIDC-based
ZAC-through-Keycloak and page-reachability checks existed).

Initially assumed objecttypen only needed the cookie fix (a plain POST
landed on the dashboard immediately) and mistakenly credited that to also
setting `settings.disable2fa: true`. **That premise was wrong twice over**:
first, direct inspection of each app's actual installed source *inside the
running pods* (`kubectl exec ... cat .../conf/*.py`) showed `DISABLE_2FA`
is only ever read by each app's own dev-only settings module
(`conf.dev`/`conf.ci`), never by the `conf.docker -> production` chain
these containers actually run - `settings.disable2fa` is a dead
values.yaml field for every one of these apps, not just some. Second, the
manual test that seemed to "prove" objecttypen was the exception
(`kubectl set env deployment/x DISABLE_2FA-` then still logging in) was
itself invalid: that command only removes an explicit `env:` entry, and
can't suppress a value still arriving via `envFrom:` from the ConfigMap -
so `DISABLE_2FA` was never actually absent during that test. A real
`values.yaml`-driven redeploy with the key genuinely gone hit the same
"Set up MFA" wall as the others. Lesson: prefer "confirmed live" checks
that change what's actually deployed, not just what a currently-running
pod's env looks like after an imperative patch.

The real, working fix (matching `openarchiefbeheer`'s already-existing
`docker_no2fa.py` from build-order step 5) is a small vendored Django
settings module per app (`from <app>.conf.docker import *` plus
`MAYKIN_2FA_ALLOW_MFA_BYPASS_BACKENDS = AUTHENTICATION_BACKENDS`), mounted
over `conf/docker_no2fa.py` via the same ConfigMap+extraVolumes+
extraVolumeMounts pattern, with `settings.djangoSettingsModule` pointed at
it. Confirmed via `kubectl exec ... python3 -c "import debug_toolbar"`
(`ModuleNotFoundError` in every one of these images) that switching
`DJANGO_SETTINGS_MODULE` to the real `conf.dev` instead isn't an option -
it unconditionally pulls in `django-debug-toolbar`, not installed in
production images. Applied to objecttypen, openzaak, and opennotificaties;
each app's actual Django project package differs from its profile name
(`objecttypes`, `openzaak`, `nrc` respectively - found live via each
pod's own `/app/src/*/conf/` layout, not guessed from the chart name).

**openformulieren deliberately left unfixed.** Same `isHttps`/2FA-wall
symptoms confirmed live, but a structurally different problem: this
chart's `configuration.superuser` values.yaml key is wired to *nothing* -
grepped every template in the chart, zero references - so the
admin/admin credentials already sitting in values.yaml never create an
actual user. Django's own `manage.py createsuperuser --noinput` works
fine directly (confirmed via `kubectl exec`), so it's fixable, just needs
a real Job/mechanism added, not a settings tweak - a big enough scope
difference from the other three that bundling it in here would've meant
either a rushed Job design or an untested one. Follow-up, not now. (One
brief live mistake during this investigation: testing openformulieren's
shim by `kubectl cp`-ing the settings file into the *running* pod, then
switching `DJANGO_SETTINGS_MODULE` and restarting the Deployment,
crash-looped the *new* pod - `kubectl cp` only touches the currently
running pod's ephemeral filesystem, not what a fresh pod's image
contains. Reverted immediately; no lasting effect since the values.yaml
change was never made for this app.)

Added `tests/test_django_admin_login.py` (replacing the objecttypen-only
draft) covering objecttypen (skips on the openobject/merged podiumd shape
or the profile being off - no separate subchart to test against either
way), openzaak (always-on core, no skip), and opennotificaties (skips if
its profile is off) - a shared `_login()` helper submits the two-factor
wizard form's `admin_login_view-current_step` field correctly for all
three, since the login form itself is real everywhere even where the
second factor turns out not to be enforceable. Full suite re-run clean
aside from the raw-templates Grafana pod's own pre-existing, intermittent
503s on its datasource/proxy endpoints (unrelated to this work, present
before this session touched anything).

### openformulieren: the actual superuser-creation gap

Followed up on the deferred openformulieren fix. Applied the same
isHttps/docker_no2fa.py cookie+2FA fix as the other three (package name
`openforms`, same "Tweestapsauthenticatie instellen" wall confirmed live
without it) - straightforward, matching precedent exactly.

The real gap was the missing superuser mechanism. Added
`templates/openformulieren/create-superuser-job.yaml`, a custom one-shot
Job running a small vendored idempotent script
(`vendor/.../openformulieren/create_superuser.py`, `get_or_create` +
`set_password` so re-applying an already-succeeded, unchanged Job spec
stays a safe no-op - no guard script needed, unlike pabc-migrations,
since this is never destructive) via `manage.py shell < script.py`,
reusing the same Secret/ConfigMap (`envFrom`) the Deployment itself uses
for database connectivity.

First attempt failed with a confusing `AUTH_USER_MODEL refers to model
'auth.User' that has not been installed` instead of a plain
`ModuleNotFoundError` - the Job inherits `DJANGO_SETTINGS_MODULE=
openforms.conf.docker_no2fa` from the shared ConfigMap (needed by the
*Deployment*), but never mounts that shim file itself (no reason to - the
Job never touches a login view), so importing a settings module that
doesn't exist on disk breaks Django's settings loading in a misleading
way. Fixed by explicitly overriding `DJANGO_SETTINGS_MODULE` back to the
real `openforms.conf.docker` in the Job's own `env:` (which takes
precedence over the same-named `envFrom:` entry).

Also found and fixed a real test regression from this Job: `test_pods.py`'s
`ONE_SHOT_JOB_PREFIXES` allowlist didn't know about it yet, so the
`Completed` (not `Ready`) superuser-creation pod started failing
`test_long_running_pods_are_ready`. Added it alongside pabc-migrations/
storage-permissions-fix/etc.

Extended `tests/test_django_admin_login.py`'s shared `_login()` helper to
follow redirects and POST to the *final* URL rather than the original
`/admin/login/` - openformulieren is the first app here to split classic
and OIDC login into separate views (`/admin/login/` 302s to
`/admin/classic-login/?next=/admin/`), and also to extract the form's
`next` hidden field rather than hardcoding it empty, since this app
pre-fills it from that query param. Full suite re-run clean (same
pre-existing Grafana intermittency aside): all four django-admin logins
(objecttypen, openzaak, opennotificaties, openformulieren) now pass.

### openklant and openarchiefbeheer: the last two django-admin gaps

Checked every remaining django-admin login in the stack the same way
again. Both openklant and openarchiefbeheer were still broken, same class
of bug as before - but with two genuinely new wrinkles each.

**openklant is structurally different from every other app fixed so
far.** Reading `open_api_framework.conf.base` (a shared maykinmedia
library `openklant.conf.base` wildcard-imports from) directly inside the
running pod showed it reads `DISABLE_2FA` in the *real production*
settings chain, not just a dev-only module - so a plain
`settings.disable2fa: true` actually works natively here, no
`docker_no2fa.py` shim needed at all. Confirmed this the hard way first:
initially assumed it'd need the same shim as the others, started
authoring `openklant/docker_no2fa.py`, then found `AUTHENTICATION_BACKENDS`
genuinely isn't defined anywhere in `openklant`'s own source tree at all
(would have made the shim itself crash with `NameError` on import) -
tracing the wildcard-import chain (`base.py` → `open_api_framework.conf.base`)
found where it actually lives, and that the shared library already
has its own working `DISABLE_2FA` → `MAYKIN_2FA_ALLOW_MFA_BYPASS_BACKENDS`
logic built in. Deleted the shim, live-tested `disable2fa: true` alone
instead - worked immediately.

**openarchiefbeheer only needed the superuser Job** - its cookie/2FA
fixes were already done in the original build (step 5). Also corrected a
misleading existing comment while in there: it claimed compose itself
replicates the `docker_no2fa.py` fix; real compose actually just has a
commented-out attempt with its own comment admitting it "errors with
`ModuleNotFoundError: No module named 'debug_toolbar'`" and gives up,
leaving 2FA enforced in real compose too - this project's shim is an
original fix, not a port of one.

Both needed a new Job (same idempotent `get_or_create`+`set_password`
pattern as openformulieren's), but hit a real wrinkle openformulieren's
own Job avoided by luck: neither app's `image.tag` is pinned in this
repo's own `values.yaml` (only `openformulieren`'s is), so the Deployment
resolves its tag from the subchart's own `AppVersion` fallback internally
- a raw template outside that subchart can't replicate
`.Chart.AppVersion` (it'd resolve to *this* chart's own AppVersion
instead). Fixed via `.Subcharts.podiumd.Subcharts.<app>.Chart.AppVersion`,
confirmed live against a throwaway debug template first (values.yaml's
own comment on each Job explains why).

Also found and fixed a real bug in the shared `_assert_logged_in()` test
helper while verifying openarchiefbeheer: it checked for
`id="logout-form"`, which turned out to be specific to the other three
apps' template - openarchiefbeheer's admin renders a plain Dutch-locale
GET link ("Afmelden") instead. Login had actually succeeded
("Welkom, **admin**." in the page) but the test still failed. Switched
to checking for the *absence* of the login form's own
`name="auth-username"` field instead, which is universal regardless of
locale or template variant.

Extended `tests/test_django_admin_login.py` with both
(`test_openklant_admin_login`, always-on/no skip; `test_openarchiefbeheer_admin_login`,
skips if its profile is off) and `tests/test_pods.py`'s one-shot allowlist
with both new Jobs. Full suite re-run clean (same pre-existing Grafana
intermittency aside): all six django-admin logins in this stack
(objecttypen, openzaak, opennotificaties, openformulieren, openklant,
openarchiefbeheer) now pass.

Declared that "every django-admin-having app" was covered - it wasn't:
missed **objecten** (objects-api), a genuinely separate app from
objecttypen with its own admin. Checked it the same way and it turned out
to be the simplest fix of all seven: this chart's `start.sh` already
reads `OBJECTS_SUPERUSER_USERNAME/EMAIL/PASSWORD` at boot and creates the
account itself (same wiring objecttypen already had), and its
`conf/base.py` wildcard-imports the same shared `open_api_framework`
library as openklant, so `settings.disable2fa: true` works natively too
- just `isHttps`/`disable2fa`/`superuser` in values.yaml, no shim, no
custom Job. Added `test_objecten_admin_login`. Full suite re-run clean,
including the previously-flaky Grafana checks this time: 51 passed, 3
skipped, 0 failed. All seven django-admin-having apps in this project
(objecten, objecttypen, openzaak, opennotificaties, openformulieren,
openklant, openarchiefbeheer) now have a real, tested credential login.

## Root-causing the Grafana 503 flakiness (it wasn't flakiness)

The "previously-flaky Grafana checks" note above turned out to be luck,
not a fix - the next full-suite run hit the same 503s again. Investigated
properly instead of re-running and hoping: curled `grafana.local`
in a tight loop (rock solid 200s in isolation - the bug needed something
the full suite's exact state produced), checked Grafana's own logs (no
errors at all - the request never made it there), then checked Traefik's:
`kubectl get ingress -n podiumd-minikube` showed **two** Ingress objects
both claiming `grafana.local` - the real one (→ `grafana` Service, port
3000, a live pod) and a `podiumd-minikube-grafana` one left over from an
earlier `monitoringLogging.enabled=true` test session, pointing at a
Service with **zero endpoints** (its Deployment already correctly pruned,
but the Service/Ingress themselves never were). Traefik load-balances
between competing routers for the same host, so roughly half of all
requests hit the dead one and got a real 503 - not flakiness, a
deterministic routing bug that just looked random from outside.

Root cause: `prune-orphaned-workloads.py` only ever covered Deployment/
StatefulSet/DaemonSet (+ the monitoring CRs) - Service/Secret/Ingress were
never in scope, so toggling `monitoringLogging.enabled` always left that
class of leftover behind, silently, forever (confirmed live: a whole pile
of them - `loki`/`alloy`/`kube-prometheus-stack` Services/Secrets - had
been sitting there for 22+ hours). Extended `PRUNABLE_KINDS` to include
Service/Secret/Ingress (deliberately NOT ConfigMap - `split-large-
configmaps.py` pulls oversized ConfigMaps out of the main render stream
entirely and applies them separately via `--server-side`, so they'd never
appear in this script's "desired" set and would get deleted immediately
after being applied, every run - documented as a known gap instead of
silently fixed wrong).

**Broke the cluster once proving this, immediately, the exact way the
user asked to guard against next**: tested by running plain
`./scripts/deploy.sh` (no `--full`) to see the fix work on a small case -
forgetting the cluster was actually running `--full` from the whole
session's prior work. `deploy.sh` (no flags) means core-profile-only, on
purpose - the newly Service/Secret/Ingress-aware prune correctly (per its
own now-broader mandate) deleted every optional-profile Deployment,
Service, Secret, and Ingress in one shot: objecten, objecttypen,
opennotificaties, openarchiefbeheer, openformulieren, the whole
raw-templates metrics stack. Restored immediately via `deploy.sh --full`
(prune reported "nothing to prune" - full round-trip confirmed clean).

Added a real guard for this, not just a personal reminder to be more
careful next time: `LARGE_PRUNE_THRESHOLD` in `prune-orphaned-workloads.py`
now refuses to actually delete anything when the to-delete count exceeds
10, printing what it *would* have pruned and requiring an explicit
`--force` (forwarded from `deploy.sh --force-prune`) to proceed - a
genuine profile mismatch (the exact accident above) or a genuine
intentional large toggle (switching `monitoringLogging.enabled` prunes
~10-50+ resources depending on direction) both hit this by design; the
difference is now that the mismatch case gets a chance to be caught and
aborted instead of silently executing.

Verifying the fix immediately surfaced a second, independent bug it would
otherwise have introduced silently: `prometheus-operated` (the
kube-prometheus-stack operator's own headless Service for Prometheus peer
discovery) showed up in the very first real to-delete list. Checked its
`ownerReferences` before trusting the list - it genuinely has one, pointing
at the `Prometheus` CR, but **without** `controller: true` set on it,
which the existing (pre-dating this change) owner-check specifically
required. Broadened the check to skip on *any* ownerReference, not just a
controller one - confirmed this doesn't weaken protection anywhere else
(every genuine leftover found this session had zero owner references at
all, not merely non-controller ones). The `LARGE_PRUNE_THRESHOLD` guard
caught this one before it could do damage (11 resources, over threshold,
refused) - purely incidental, not something to rely on for correctness,
which is why the ownerReference check itself got fixed too rather than
left to the threshold alone.

Verified end-to-end, both directions, for real: toggled
`monitoringLogging.enabled` true → `deploy.sh --full` (correctly refused
at 11 resources, `prometheus-operated` no longer among them; confirmed
Grafana/Loki/Alloy/Prometheus/Tempo all `Running` and `grafana.local`
serving 200s through the new implementation) → toggled back to false →
`deploy.sh --full` (refused again, ~60 resources this time; `--force-prune`
cleaned every one of them, including the `Prometheus` CR whose deletion
correctly cascades `prometheus-operated` via Kubernetes' own garbage
collection - not this script). Raw-templates Grafana confirmed back,
single Ingress, zero `podiumd-minikube-*` leftovers, 15/15 requests
returning 200. Found and manually cleaned up ~9 real ConfigMap stragglers
along the way (the one kind this fix doesn't cover) - ordinary `tempo`/
`tempo-config` ConfigMaps from the currently-active raw-templates
Deployment were correctly left alone, confirmed by checking the
Deployment's own age against the ConfigMap's.

Full suite re-run clean: 51 passed, 3 skipped, 0 failed - the Grafana
503s are gone for real this time, not by luck.

## Always-on openzaak/openklant workers - a real Celery queue collision found live

Asked to always enable `worker.replicaCount` for openzaak and openklant
(both were `0`, matching compose's own topology - openzaak's celery only
ever runs under other profiles in compose, openklant never runs one at
all). Flipped both to `1` and redeployed - pods came up `1/1 Running`
immediately, looked done.

Checked the workers' own logs anyway before calling it finished (habit
that paid off): openklant-worker was logging `Received unregistered task
of type 'openforms.forms.tasks.activate_forms'` - it was consuming and
silently discarding openformulieren's own scheduled tasks. Root cause:
openklant's `settings.celery.brokerUrl` and openformulieren's both point
at the same `redis://redis:6379/2` - harmless while openklant's worker
was disabled, but Celery workers sharing a broker are *competing
consumers* on the same default `celery` queue, not independent - once
both are active, each one can pop the *other's* messages off the queue,
log "unregistered task", and drop them. Checked whether enabling
openzaak's worker had the identical problem with objecten (both configured
on db1) - no visible errors yet in either's logs at the time, but the
absence of a log line doesn't mean the collision isn't live; competing
consumers only clash on whichever task actually gets round-robined to the
"wrong" one first, so a clean-looking log at one point in time proves
nothing here. Reasoned about it from the shared-DB fact instead of waiting
for it to eventually happen to manifest.

Fixed by giving each newly-active worker its own DB no other app brokers
on - openzaak → db3, openklant → db4 (audited every `redis:6379/N`
occurrence in values.yaml first to confirm both were genuinely unused,
not just "not obviously used"). `opennotificaties`'s own db1 assignment
was deliberately left alone - confirmed it's only ever used as a
result-backend for that app (its real broker is RabbitMQ), and a shared
result-backend DB doesn't have the competing-consumer problem a shared
*broker* DB does - results are looked up by unique task UUID, not
consumed off a shared queue.

Verified live, not just "no errors this time": openklant-worker's own
`mingle` log line flipped from `sync with 1 nodes` (finding
openformulieren-worker as a queue-neighbor) to `all alone` (genuinely
isolated) after the DB reassignment - the clearest possible confirmation
the collision is actually gone, not just quiet. Added
`openzaak-worker`/`openklant-worker` to `test_pods.py`'s
`test_core_profile_pod_present` list, matching their new always-on
status. Full suite: 51 passed, 3 skipped, 0 failed.

## Moving podiumd/monitoring-logging version selection out of Chart.yaml

Asked to store both dependencies' versions in a separate, gitignored YAML
file instead of `Chart.yaml`, with `deploy.sh` reading from it and
`Chart.yaml` never changing when the version does.

Verified the one fact this whole design rests on before writing any code:
does `helm template`/`install` re-validate a loaded subchart's version
against the parent `Chart.yaml`'s own declared dependency constraint?
Tested directly - hand-edited `Chart.yaml`'s podiumd version to a
nonsense "9.9.9" with the real fetched dependency still at 4.8.3 in
`charts/`, and `helm template` rendered clean using the physically-present
4.8.3 chart regardless. Confirmed: that field is consulted by `helm
dependency update`/`build` only, never at render time. This makes
temporarily rewriting `Chart.yaml` to the real desired version, running
`helm dependency update`, then restoring `Chart.yaml`'s original content
completely safe - the already-fetched `charts/*.tgz` stays exactly as
fetched either way.

Initial design kept `Chart.yaml`'s existing committed version as a
"shared default, used when no override exists" - the user redirected mid-
implementation: `Chart.yaml` should hold no real version at all, ever;
`.podiumd-versions.yaml` should be the *only* source, and every entry
point should refuse with a clear "run set-podiumd-version.sh" message if
it's missing, rather than silently falling back to anything. Simpler, and
avoids a subtler problem the fallback design had: `Chart.lock` is also
git-tracked, and `helm dependency update` always rewrites it to reflect
whatever was *actually* resolved - meaning even with `Chart.yaml` itself
reverted after every sync, a local override would still leak into
`Chart.lock` as an uncommitted diff unless that's reverted too. Both
`Chart.yaml` and `Chart.lock` get backed up and restored around every
`helm dependency update` call now.

`Chart.yaml`'s two dependency `version:` fields are now literal
placeholders (`"0.0.0-set-via-podiumd-versions-yaml"`) - a plain `helm
dependency update` run by someone who bypasses this project's scripts
entirely fails loudly against that placeholder instead of silently
fetching some unrelated real release.

monitoring-logging still needs a real, fetchable version in
`.podiumd-versions.yaml` even when `--disable-monitoring-logging` is
chosen - Helm fetches every declared dependency regardless of its
`condition:` value (already known from this project's earlier
`set-podiumd-version.sh` work), so there's no way to skip recording a
real version for it. Added a guard in `set-podiumd-version.sh`: the very
first time someone chooses `--disable-monitoring-logging` before any
monitoring-logging version has ever been recorded, it refuses with the
exact command to run first, rather than either silently picking something
or letting the generic sync-time error surface confusingly from inside
the same command that's supposed to be setting things up.

A second real bug surfaced while writing `sync_podiumd_dependencies`,
caught before it could ship: calling the per-dependency fetch function
sequentially (edit podiumd's block, fetch, revert; then edit monitoring-
logging's block, fetch, revert) is wrong - `helm dependency update`
always re-resolves *every* declared dependency in one pass, so the
second call's own edit-then-revert cycle would transiently put
`Chart.yaml` back to podiumd's *placeholder* value while fetching
monitoring-logging, silently re-fetching (and clobbering) podiumd at the
wrong version. Fixed by editing both dependencies' blocks together before
a single combined `helm dependency update` call, then one combined revert
- not one edit/fetch/revert cycle per dependency.

Verified end-to-end, live, not just read through: (1) no
`.podiumd-versions.yaml` at all - `show-podiumd-version.sh`/`deploy.sh`
both refuse with the expected message; (2) registry mode
(`set-podiumd-version.sh 4.8.3 1.0.14`) - `charts/podiumd-4.8.3.tgz`
fetched, `Chart.yaml`/`Chart.lock` show zero diff from their committed
baseline afterward, `helm template` renders using the 4.8.3 chart
(confirmed via its own `helm.sh/chart` label); (3) re-running `deploy.sh
--full` immediately after skipped the network round-trip entirely (no
`helm dependency update` output at all) since the target tarball was
already present; (4) `--path` mode against a real local podiumd/
monitoring-logging checkout - correctly re-packaged fresh, `Chart.yaml`/
`Chart.lock` still showed zero diff afterward. Along the way, confirmed
(the hard way, via a genuine mistake mid-session) that this project's
existing `podiumd` version alone doesn't explain the `opennotificaties`
`ImagePullBackOff` gap found earlier - checked the actually-fetched
4.8.3 tarball's own bundled `opennotificaties` chart directly and found
it *also* pins `image.tag: "1.16.1"`, not the older cached 1.15.0 - this
is a pre-existing, version-independent image-cache gap (this minikube's
local cache predates whatever podiumd version first introduced that
pin), not something introduced by or fixable via this dependency-version
work. Left unfixed for now, flagged separately - fixing it means either
re-running `provision-cluster.sh`'s image pre-pull step or a manual
`docker pull` + `minikube image load` for that one image.

Full suite: 53 passed, 3 skipped, 2 failed (both the known
`opennotificaties` image-pull gap above) - the one additional failure
seen mid-session (`test_prometheus_scrape_targets_healthy`) reconfirmed
as transient by hand immediately after (pods healthy, a fresh curl
against the same endpoint returned 200), not a regression from this
work.

## The productaanvraag flow: a form submission creating a zaak automatically

Asked to set up "test zaaktype 1" in ZAC and Open Formulieren so a case can
be created via a form - specifically via the *productaanvraag* flow
(Open Formulieren's `objects_api` registration backend writing a
Productaanvraag-Dimpact object to the Objects API, not the more direct
`zgw-create-zaak` backend), so `objecten`/`objecttypen`/`opennotificaties`
all have to talk to each other correctly. First built and verified live
by hand end to end (`kubectl exec`/`curl` against every piece), then made
fully declarative so a fresh `provision-cluster.sh` + `deploy.sh --full`
reproduces it with no manual step at all.

**What's now declarative:**

- `podiumd.opennotificaties.configuration.data` - the `objecten` kanaal, an
  Autorisaties API link back to openzaak (its own abonnement-authorization
  check defaults to the public `https://autorisaties-api.vng.cloud` and
  403s every publish otherwise - confirmed live), the `objectsapi` client
  credential, and a ZAC abonnement on that kanaal.
- `podiumd.objecten.configuration.data` - its own links to Objecttypen API
  and Open Notificaties, a *reference* to the productaanvraag objecttype
  (not the schema itself - see below), and read/write tokens for ZAC and
  Open Formulieren.
- `podiumd.objecttypen.configuration.data` - read tokens for Objects API
  and Open Formulieren.
- `podiumd.openformulieren.configuration.data` - openzaak's
  zaken/documenten/catalogi services, Objects/Objecttypen services, and
  the `local-objects-api` Objects API group.

**Two custom Jobs fill two of the gaps none of the above cover** (both
idempotent, safe in the unguarded `kubectl apply` flow, same as every
other Job in this project). A third gap - the productaanvraag
objecttype/version schema itself has no setup_configuration step anywhere
(confirmed by reading `objecttypes/setup_configuration/steps/` directly -
only tokens are supported) - was *not* filled with a third custom Job:
mid-build, a teammate's own concurrent work
(`scripts/lib/seed-fixtures.sh`, now auto-run by `deploy.sh` whenever the
`objecten` profile is deployed) turned out to already `loaddata` the exact
same vendored fixture
(`vendor/dimpact-zaakafhandelcomponent/objecttypen/demodata.json`, 6
objecttypes including the real Productaanvraag-Dimpact schema - it was
sitting in this repo unwired to anything before either piece of work
used it) for its own, unrelated reason. Found this via a real rebase
conflict (both branches touched `plan.md`'s own tail) while pushing this
work, not before - a first draft of this feature *did* add a third,
separately-loaddata-ing Job, which would have raced the other script for
control of the same `ObjectVersion.status` field (the fixture ships every
version as `status: draft`; whichever of the two loaddata calls happened
to run last would silently win, and only one of them - this feature's own
first draft - ever flipped `draft` to `published`, which Objects API's own
object-create validation requires). Reconciled by deleting that Job
entirely and instead adding the "flip every version to published" fixup as
one extra step inside `seed-fixtures.sh` itself, immediately after its own
existing `objecttypen` `loaddata` call - a single authoritative place for
this fixture to be loaded, no ordering race possible between two
mechanisms doing the same thing.

- `templates/zac/productaanvraag-zaakafhandelparameters-job.yaml` - ZAC
  has no setup_configuration-equivalent at all (a WildFly/Kotlin app, not
  Django). Calls ZAC's own `/rest/zaakafhandelparameters` REST API
  directly (GET the generated default, patch in a case definition/default
  group/niet-ontvankelijk resultaat/productaanvraagtype, PUT it back) as a
  real user (`beheerder1newiam`, direct Keycloak grant) - every endpoint
  asserts a real PABC beheerder role, a client_credentials service account
  doesn't satisfy that.
- `templates/openformulieren/productaanvraag-form-job.yaml` - forms aren't
  configurable through setup_configuration at all. Creates the
  Form/FormDefinition/FormStep/FormRegistrationBackend via
  `manage.py shell`, using the `objects_api` backend (not `zgw-create-zaak`)
  so it round-trips through the same Objects API -> Open Notificaties ->
  ZAC chain a real productaanvraag does.

`podiumd.zac.productaanvraag.productaanvraagtype` is the single shared
value both the ZAC job and the form-import job read - can't drift between
the two sides of the match.

**Two real bugs found live, both fixed properly rather than worked around:**

1. Django's `URLValidator` rejects single-label hostnames (`openzaak`,
   `objecttypen`, ...) - fine for a straight HTTP connection, but breaks
   any self-referencing resource URL that gets round-tripped through a
   validated field later (e.g. `/catalogi/api/v1/zaaktypen?catalogus=<url>`).
   Every internal service-to-service URL added for this flow uses the
   dotted `<service>.podiumd-minikube` Service DNS form instead of the
   bare name this file's own naming-convention comment would otherwise
   suggest - `openzaak.podiumd-minikube`, `objecten.podiumd-minikube`,
   `objecttypen.podiumd-minikube`, `opennotificaties.podiumd-minikube`,
   `zac.podiumd-minikube`. Confirmed live: each one's own ALLOWED_HOSTS
   already includes this dotted form as a podiumd chart default -
   deliberately unused until now.
2. A genuine, reproducible-but-not-on-demand caching bug: django-solo's
   own cache (`SOLO_CACHE="default"`, `SOLO_CACHE_TIMEOUT=300`, set in the
   shared `open_api_framework` library and never overridden anywhere in
   these images) for `notifications_api_common.NotificationsConfig`
   intermittently returns a stale `notifications_api_service=None` from
   Redis while the database row is, every time checked directly, correct.
   Hit this twice in real live testing (Objects API once, then openzaak
   itself once, both raising "Not notifying, Notifications API
   configuration is broken or absent." as a 500 on the exact create
   request the whole flow depends on) but couldn't nail a single
   deterministic reproduction afterward despite trying sequential,
   concurrent, direct-to-pod, and through-Traefik requests. Fixed by
   disabling the cache entirely for both apps rather than depending on
   correctly diagnosing one specific third-party-library race:
   `vendor/dimpact-zaakafhandelcomponent/objecten/docker_no_solo_cache.py`
   (a new settings shim, same ConfigMap+extraVolumes+extraVolumeMounts
   pattern as every existing `docker_no2fa.py`) and `SOLO_CACHE = None`
   bundled into openzaak's *existing* `docker_no2fa.py` shim rather than
   adding a second one. The extra DB round trip this adds is a single
   indexed PK lookup - negligible. Only fixed for the two apps confirmed
   to actually hit it (both squarely in this flow's critical path,
   openzaak's also affecting core zaak creation generally, not just this
   feature) - not spread speculatively to every other app using the same
   library without confirmed need.

**Verification:** `tests/test_productaanvraag_flow.py` (new) - checks the
two custom Jobs succeeded, the opennotificaties kanaal/abonnement exist
(the four bundled config Jobs all self-delete within seconds via their own
`ttlSecondsAfterFinished: 0` default, so their *effects* are checked
instead of the Jobs themselves), the productaanvraag objecttype is
registered and published, ZAC's zaakafhandelparameters are valide, Open
Formulieren's registration backend validates live against the real
Catalogi/Objecttypen APIs, and - the real thing, not a proxy for it - POSTs
an actual productaanvraag object and polls until a matching zaak appears.
Also extended `test_pods.py`'s one-shot-Job allowlist for the two new
custom Jobs. Rebuilt entirely from a fresh `.podiumd-versions.yaml` +
`deploy.sh --full` after rebasing onto the teammate's concurrent
`seed-fixtures.sh`/Chart.yaml-version-selection work above, to confirm the
reconciled version (not just the pre-reconciliation one) actually works.
Full suite: 63 passed, 3 skipped, 1 pre-existing failure unrelated to this
work (`zac-sig-del`/`zac-signaleren` CronJob pods already failing before
this work started).

## PodiumD 4.9 release-notes catch-up

Asked to check PodiumD 4.9's own release notes (Dimpact Confluence, still
"IN VOORBEREIDING"/"NIET BEGONNEN" as a real release) and update components
to the versions listed there, altering values where the release notes
themselves call for it. Confirmed via `helm search repo dimpact/podiumd -l`
that only up to 4.8.4 is actually published - nothing bundled *through*
the podiumd dependency (openklant's chart, objecten's chart, etc.) can be
version-bumped as a whole yet, only individual app image tags this
project already overrides independently of chart version.

**Deliberately scoped out, per explicit instruction and the release notes'
own warnings:**

- **Open Klant** (2.15.0 → 2.17.0 in the notes) - the notes themselves say
  *"LET OP: de update van OK naar 2.16 of hoger kan nog niet want er zitten
  breaking changes in"* - not touched at all.
- **Objecten API's 4.1.1** - not a patch, a full merge of Objecttypen API
  into Objecten API (data migration + a DNS CNAME requirement) - the
  classic→merged "Open Object" shape this project's own
  `detect-objecten-shape.sh`/`seed-fixtures.sh` already anticipate but
  haven't done. Bumped to the latest *3.x* patch instead (3.6.2 - the
  highest chart-published app version in that series; the chart itself
  stays at 2.12.1, no newer 3.x chart release exists) and objecttypen kept
  enabled, staying on the classic split shape.

**Four independent image-tag bumps** (all within podiumd 4.8.4's existing
bundled charts or this project's own raw templates - fully reproducible by
anyone via the normal `set-podiumd-version.sh` flow, no `--path` needed).
Each one's upstream CHANGELOG checked first for schema/migration risk
(this project's own vendored fixture SQL is schema-sensitive for
openzaak/objecten specifically) before bumping - all four turned out to be
bugfix/security-only releases:

- Open Formulieren 3.5.4 → 3.5.5 (one patch behind docker-compose.yaml's
  own, independently-newer 3.5.6 pin).
- Open Zaak 1.29.1 → 1.29.3.
- Objecten 3.6.1 → 3.6.2 (see above).
- Keycloak 26.6.4 → 26.7.1 (this project's own raw template pin, not a
  podiumd-bundled chart at all).

**ZAC 1.0.251 → 1.0.289 (app 5.0.1 → 5.4.2), the one genuinely experimental
piece** - unlike the four above, podiumd 4.8.4 doesn't bundle this newer
zac chart at all, and podiumd 4.9 (which would) isn't published. Reused
the exact same `--path` local-checkout technique as an earlier
now-reverted PKCE experiment mentioned above, but this time against *real,
published* versions instead of an interim unreleased state - cloned
`Dimpact-Samenwerking/helm-charts` fresh into a scratch dir (not the
existing local checkout at `~/IdeaProjects/helm-charts`, which was mid-use
on a teammate's own feature branch with its own uncommitted changes -
asked first, isolated clone was the answer), bumped just its zac dependency
version, `helm dependency update`d, then pointed this project's own
`--path` at it. Also needed an explicit `image.tag: "5.4.2"` override even
after the chart bump - confirmed via `docker manifest inspect` that
`ghcr.io/infonl/zaakafhandelcomponent:5.4` (the chart's own
`.Chart.AppVersion`-derived default) and `:5.4.2` are different digests,
the former frozen at the first 5.4.x patch.

This finally closes out the PKCE story from the "Self-referential URLs"/
NOTES.md entries above: PR #6490 landed in this exact chart bump, so
`auth.enablePkce` (previously a documented, harmless no-op - the old
chart's own `config.yaml` template had no `AUTH_ENABLE_PKCE` line at all)
is now genuinely active. Confirmed live: ZAC's own authorization redirect
now carries a real `code_challenge`/`code_challenge_method=S256`. Flipped
the vendored realm's own PKCE requirement for this client back to
`"S256"` to match - and, since Keycloak only imports a realm once (editing
the JSON file alone doesn't touch an already-existing realm, the same gap
the earlier "bad request connecting to zac.local" incident hit), patched
it into the *already-imported* live realm via the Admin API too. Full
login round trip (real credentials, code exchange, authenticated app
shell) still completes cleanly with PKCE now required.
`tests/test_pkce.py`'s own negative guard
(`test_zac_client_does_not_send_pkce_yet`) flipped to a positive
confirmation (`test_zac_client_now_sends_a_pkce_code_challenge`) to match.

Committed as clearly-marked EXPERIMENTAL (matching the pattern the earlier
PKCE attempt used) with the exact `--path` reproduction recipe in
`values.yaml`'s own comment: `.podiumd-versions.yaml` is gitignored/
personal by this project's own design, so nothing here is reproducible by
just cloning the repo and running the normal `set-podiumd-version.sh 4.8.4
<ml-version>` flow - a teammate who does that gets zac chart 1.0.251 (no
`AUTH_ENABLE_PKCE` support) forced to run image 5.4.2, against a realm that
now *requires* PKCE, which would break every login for them. Revert
`podiumd.zac.image.tag`'s override and the realm's PKCE requirement
together once a real podiumd release bundles zac 1.0.289 by default.

**A genuine, reproducible Apple-Silicon+colima environment bug found
along the way** (now documented in `mac.md`): `docker save --platform
linux/amd64 <ref> -o x.tar` silently produced a ~13KB stub tarball (just a
manifest/config blob, no layer data, exit code 0) for two of the six newly
pulled images specifically (`maykinmedia/objects-api:3.6.2`,
`openzaak/open-zaak:1.29.3` - the other four saved fine) - re-pulling,
re-tagging, and removing-then-re-pulling the image all reproduced the same
stub, ruling out a caching artifact. Worked around with `crane pull
--platform linux/amd64 <ref> x.tar` (bypasses Docker/containerd's own
export path entirely, fetching a proper OCI tarball straight from the
registry) - `minikube image load` accepts its output the same as a
`docker save` one.

Full suite (including `test_browser.py`): 65 passed, 3 skipped, 0 failed -
even the pre-existing `zac-sig-del`/`zac-signaleren` CronJob failure from
the previous entry cleared up on its own (old failed pod history aged out).

## Pointing podiumd at a local `dimpact-samenwerking/alt_helm-charts` checkout surfaced three more real bugs (merged objecten shape)

Testing an unreleased podiumd checkout via `set-podiumd-version.sh --path
~/development/werk/infonl-dimpact/dimpact-samenwerking/alt_helm-charts/charts/podiumd
--disable-monitoring-logging` (that checkout declares podiumd 4.9.0) moved
this cluster from the classic objecten/objecttypen shape to the merged
`openobject` shape for the first time since the productaanvraag flow above
was built - `deploy.sh --full` on that shape surfaced three real, distinct
bugs, none of them latent in the classic shape:

1. **`objecttypes.items[].service_identifier` is `extra_forbidden` on
   merged.** open-object >=4.0.0 merged Objects and Objecttypes into one
   API - its own `ObjectTypesConfigurationStep` dropped the field
   entirely and now rejects it outright instead of ignoring it (confirmed
   against that version's own
   `setup_configuration/models/objecttypes.py`), so the whole
   `objecten-config` Job failed at validation, before any step ran -
   silently skipping the rest of the productaanvraag wiring in that same
   Job (zgw_consumers services, notifications_config, tokenauth) too.
   values.yaml's own block still has to satisfy classic shape (that step's
   model there makes the field mandatory) - fixed by leaving it in
   values.yaml and stripping it back out of the rendered
   `objecten-configuration` ConfigMap post-render, merged-shape-only, via
   the new `scripts/lib/fixup-merged-objecten-shape.py` (wired
   into `deploy.sh`'s `render()` pipeline, gated on a new `OBJECTEN_MERGED`
   env var `detect-objecten-shape.sh` now exports).
2. **The dropped `objecttypen` subchart leaves its own Service name
   dangling.** Merged shape has no separate objecttypen subchart at all
   (per `detect-objecten-shape.sh`'s own existing comment), so
   `prune-orphaned-workloads.py` correctly deletes its `objecttypen`
   Service on the first `--full` deploy after switching - but every
   existing `http://objecttypen.podiumd-minikube/...` reference (this
   chart's own `objecten.configuration.data`, and potentially other apps'
   zgw_consumers entries) is left pointing at a name that no longer
   resolves. Fixed with a plain DNS CNAME, not a config-value chase: new
   `templates/objecten/service-objecttypen-alias.yaml`, an `ExternalName`
   Service named `objecttypen` pointing at `objecten.<namespace>.svc.cluster.local`,
   rendered only when merged (`objecten.merged=true`, also set by
   `detect-objecten-shape.sh`) - every existing reference keeps working
   unchanged, on either shape.
3. **Unrelated to the shape switch, but blocked by it in the same Job:**
   the `token_auth` step's `update_or_create(identifier=...)` failed on a
   `UniqueViolation` on the `token` column itself for both the `zac` and
   `open-formulieren` tokens. Root cause: stale rows already in this
   cluster's `objects` Postgres DB from *before* those two identifiers
   were renamed from `zaakafhandelcomponent`/`openformulieren` to
   `zac`/`open-formulieren` in an earlier commit - the rename was never
   accompanied by a DB fixup, so `update_or_create` couldn't find the old
   row by the new identifier and collided inserting a "new" one with the
   same token value instead. Fixed once, live, with a plain `UPDATE
   token_tokenauth SET identifier=... WHERE identifier=...` for both rows
   (this cluster's own disposable fixture data, not a code change - a
   fresh `objects` DB would never hit this).

All three fixed and reverified end to end: `objecten-config` Job
completes cleanly (`Successfully executed step` for all four steps),
`getent hosts objecttypen.podiumd-minikube` resolves to the `objecten`
Service's ClusterIP from inside the cluster, and a full `deploy.sh --full`
rerun afterward needed zero further fixes. Not yet re-verified against
classic shape after these changes (no classic-shape podiumd version was
deployed live this session) - `objecten.merged` defaults to `false` and
`fixup-merged-objecten-shape.py` no-ops unless `OBJECTEN_MERGED=true`,
so classic should be unaffected, but worth a real deploy against a
published (non-merged) podiumd version before trusting that blindly.

**Also found, not fixed (pre-existing, unrelated to the shape switch):**
the minikube node has no general internet egress to Docker Hub
(`registry-1.docker.io` times out - `curl` exit 28 - confirmed via
`minikube ssh`; `registry.k8s.io` fails the same way on `minikube start`).
`templates/zac/productaanvraag-zaakafhandelparameters-job.yaml`'s
`python:3.13-alpine` init container had never been pulled on this node
before and sits in permanent `ImagePullBackOff` as a result - every other
image already cached from earlier pulls is unaffected. Worth revisiting
(e.g. an image already known-cached, or pre-pulling it in
`provision-cluster.sh`'s own image-list step) if this keeps biting.

## Extending the test suite to actually cover merged shape surfaced three more real bugs

Asked directly whether DNS names (including `objecttypen`) were actually
tested. Answer at the time: only partially -
`tests/test_zgw_service_reachability.py` checked `objecttypen-api`'s
reachability, but only via `objecten`'s *own* zgw_consumers rows - it
never queried `openformulieren`'s separate `objecttypes-api` row pointing
at the same hostname, and `tests/test_productaanvraag_flow.py` required
the classic-only `objecttypen` profile flag to even run at all, so it
silently skipped every one of its 7 tests under merged shape without
anyone noticing - the exact flow this session had just been fixing was
never actually re-verified end to end.

Generalized both:

- `test_zgw_service_reachability.py` now parametrizes over every app that
  registers its own `zgw_consumers.Service` rows (`objecten`,
  `opennotificaties`, `openarchiefbeheer`, `openformulieren`), not just
  `objecten`. Doing so surfaced a *different*, pre-existing, unrelated gap:
  `opennotificaties`/`openarchiefbeheer` both register genuinely external
  reference APIs (`autorisaties-api.vng.cloud`, `selectielijst.openzaak.nl`)
  that this offline minikube box was never going to reach regardless of
  any in-cluster DNS wiring - added `_is_in_cluster_hostname()` to skip
  those specifically (single-label or `*.podiumd-minikube` hosts only get
  checked), rather than either failing on them or dropping reachability
  checking entirely.
- `test_productaanvraag_flow.py`'s `REQUIRED_PROFILES` dropped
  `"objecttypen"` (merged shape's own flow works fully without that
  profile existing at all), and
  `test_productaanvraag_objecttype_is_registered_and_published` now picks
  its Ingress host and auth token per shape instead of hardcoding
  classic's.

Actually running the productaanvraag flow suite against merged shape for
the first time (it had only ever run against classic before, per this same
file's "productaanvraag flow" entry) found two more real, merged-only bugs
beyond the three already fixed above:

1. **openformulieren's own `objecttypes-api` zgw_consumers entry
   authenticates with a token that never existed outside classic shape.**
   `openFormulierenToObjecttypenToken` was only ever registered in
   classic's separate objecttypen app's own token table
   (`podiumd.objecttypen.configuration.data`, itself only rendered on
   classic shape) - merged shape's unified Objects/Objecttypes token table
   only ever gets `fakeOpenFormulierenObjectsToken`. Every read against
   the (now-working, DNS-alias-resolved) `objecttypen.podiumd-minikube`
   host 401'd as a result. Fixed in the same
   `scripts/lib/fixup-merged-objecten-shape.py` post-renderer (renamed
   from `fixup-merged-objecten-configuration.py` now that it patches a
   second ConfigMap too) - rewrites that one service entry's
   `header_value` to the token that actually exists, merged-only.
2. **The DNS alias resolves, but Django's `ALLOWED_HOSTS` still rejects
   the Host header.** Confirmed live: `objecten`'s own `ALLOWED_HOSTS`
   env var (`podiumd.objecten.settings.allowedHosts` in values.yaml,
   comma-joined with the chart's own auto-added bare-name/FQDN forms) only
   ever listed `objecten`'s own names - a request with Host header
   `objecttypen.podiumd-minikube` got Django's generic `Bad Request (400)`
   DisallowedHost page even though the CNAME resolved fine and routed to
   the right pod. A DNS alias alone was never going to be enough for an
   app that validates its own Host header. Fixed in
   `scripts/lib/detect-objecten-shape.sh`'s merged branch: overrides
   `podiumd.objecten.settings.allowedHosts` to values.yaml's own value plus
   `objecttypen,objecttypen.podiumd-minikube` - commas need `\,` escaping
   in a Helm `--set` value (bare commas split into separate key=value
   pairs instead - confirmed live, first attempt errored with `key
   "objecttypen" has no value`), and the whole `--set arg=val` has to be
   single-quoted in the bash array literal too, or bash strips the
   backslash before helm ever sees it.
3. **seed-fixtures.sh's merged branch never ran the
   draft-to-published fixup at all.** Classic's branch runs an
   unconditional `ObjectVersion.objects.exclude(status="published").update(...)`
   fixup after seeding (see this file's own "productaanvraag flow" entry
   for why - Objects API's create-validation only resolves a *published*
   version) - the merged branch's `if OBJECTEN_MERGED` block only ever
   called `seed objecten .../openobject/demodata.json core Object` and
   skipped that fixup entirely. `openobject/demodata.json` ships the exact
   same problem under merged's own renamed model
   (`core.objecttypeversion`, not `core.objectversion`) - confirmed live,
   the Productaanvraag-Dimpact version (pk=4/object_type=4) was seeded
   `status: draft`, so its own `/versions` endpoint never showed a
   published one. Added the equivalent fixup to the merged branch, against
   `objecten`'s own pod (merged has no separate objecttypen pod to exec
   into) and the renamed model.

All three re-verified live: `openformulieren`'s objecttypes-api reachability
check now returns 200 instead of 401, `objecten`'s `ALLOWED_HOSTS` env var
includes `objecttypen`/`objecttypen.podiumd-minikube` and the same request
that 400'd now 200s, and re-running `seed-fixtures.sh` published 3
objecttype versions. Full suite after all fixes: 62 passed, 5 skipped
(3 monitoringLogging-off skips, 1 objecttypen-profile-off skip in
test_django_admin_login.py, 1 same in test_reachability.py - all expected
on this shape), 4 failed - all four tracing to the same single,
already-documented, pre-existing cause: this minikube node currently has
no outbound DNS resolution at all (`minikube ssh -- curl
https://registry-1.docker.io/...` *and* `https://infonl.github.io` both
now fail to resolve, not just Docker Hub specifically as first suspected -
broader than initially scoped, still a node/environment characteristic
unrelated to any podiumd-shape work here, not something fixed this
session).

## Fixing the outbound DNS/egress gap for real, on the host

The pre-existing "minikube node has no outbound DNS resolution" issue
flagged above was not actually a minikube/Docker networking bug at all -
it was this host's own hand-written `/etc/nftables.conf` (Docker's
`firewall-backend: nftables` setting, per `/etc/docker/daemon.json`, means
Docker relies entirely on this file's own rules rather than injecting its
own iptables NAT chains). Diagnosed and fixed live, two separate gaps in
that one file:

1. The `forward` chain's `policy drop` only ever allowed
   `iifname "docker0" oifname "docker0"` and `iifname "br-*" oifname
   "br-*"` (container-to-container on the *same* bridge, explicitly
   commented "Docker ICC") - nothing let a container reach *out* through
   the real interface at all. Added `iifname { "docker0", "br-*" } accept`.
2. The `postrouting` NAT table's masquerade rule was entirely commented
   out, and once restored, only matched `ip saddr 172.17.0.1/16` -
   `docker0`'s own subnet specifically, not minikube's own custom bridge
   subnet (`192.168.49.0/24`) or any other user-defined bridge network.
   Needed `iifname "br-*" oifname != "br-*" counter masquerade` alongside
   the existing `docker0` line - first attempt used `oifname "br-*"`
   (same shape as the ICC-only forward rule, matching same-bridge traffic,
   a no-op for actual egress) before landing on the right one.

Kees applied both edits directly (root-owned system file, out of scope
for this project's own repo) via `sudo nft -f /etc/nftables.conf` -
reloads the ruleset in place, doesn't restart Docker (`docker.service` is
`PartOf=nftables.service`, which a full `systemctl restart nftables`
would cascade into, disrupting every running container needlessly).
Confirmed live end to end: `python:3.13-alpine` (previously stuck in
permanent `ImagePullBackOff`, the "found, not fixed" item above) now
pulls cleanly, and the previously-skipped-by-necessity seed Job that
needs it completes.

## Wiring openzaak's own outbound notifications - the last real gap

With DNS fixed, the productaanvraag flow's own full end-to-end test
(`test_full_productaanvraag_flow_creates_a_zaak`) still failed - a
completely different, unrelated bug surfaced only once ZAC's own
`createZaak` call could actually complete for the first time: OpenZaak's
own `notifications_api_common.NotificationsConfig.notifications_api_service`
was `None` - never wired declaratively anywhere in this chart at all
(compose itself doesn't use django-setup-configuration for openzaak - see
this block's own `job.enabled` comment predating this fix). OpenZaak's
own post-create notification hook raises "Not notifying, Notifications
API configuration is broken or absent" as an *uncaught 500 on the whole
create-zaak request* when this is unset - the zaak row still gets
written, but ZAC sees the 500 and aborts, even though nothing in the
productaanvraag flow actually depends on this notification being
delivered anywhere. This is a real, general gap - `openzaak`'s own
`POST /zaken/api/v1/zaken` (or any besluit/document creation) has
presumably *always* 500'd this way via its REST API; nothing before today
had ever exercised that path live, since the vendored SQL fixture seeds
zaken directly into Postgres, bypassing the hook entirely.

Fixed declaratively in two places:

- `values.yaml`'s `podiumd.openzaak.configuration.data`: added the
  `zgw_consumers` "notifications-api" Service (pointing at opennotificaties,
  `client_id: openzaak`) and `notifications_config_enable`/
  `notifications_config` - following the exact commented-out example
  already present in podiumd's own upstream chart values (openzaak
  supports this out of the box, it just had never been turned on here).
- `values.yaml`'s `podiumd.opennotificaties...vng_api_common_credentials`:
  added the matching `identifier: openzaak` credential, and
  `notifications_kanalen_config`: registered all six of openzaak's own
  kanalen (`zaken`, `besluittypen`, `zaaktypen`, `informatieobjecttypen`,
  `documenten`, `autorisaties` - confirmed live against
  `notifications_api_common.kanalen.KANAAL_REGISTRY` in the running pod),
  not just the one ("zaken") today's bug needed - every other resource
  type's own create/update hook hits the identical "kanaal missing"
  failure the moment `notifications_config` exists at all, and none of
  them had ever been exercised via openzaak's REST API before either.
  Tried `openzaak`'s own `manage.py register_kanalen` management command
  first (an authenticated HTTP call *against* opennotificaties) - 403'd
  live, since the plain credential above has no Autorisatie granting
  kanaal-management scope. Registering kanalen is really an
  opennotificaties-side action anyway (that's where the `Kanaal` resource
  actually lives, on the receiving side, not the publisher's) - the
  declarative `notifications_kanalen_config` route sidesteps the whole
  permission question entirely.

### A second, genuinely nasty bug found chasing this down: django-solo's cache going stale

Even with the database row correctly configured (confirmed repeatedly via
direct `psql`), `get_solo()` kept intermittently returning
`notifications_api_service=None` anyway - the exact same class of bug
`vendor/dimpact-zaakafhandelcomponent/objecten/docker_no_solo_cache.py`
already documents fixing for Objects API during this same flow's original
build (django-solo's own `SOLO_CACHE="default"`/`SOLO_CACHE_TIMEOUT=300`
defaults, from `open_api_framework.conf.base`, never overridden anywhere
in the base image). Wasted real effort re-diagnosing this from scratch
(flushing Redis, writing a whole poll-and-clear provisioning script) before
discovering **openzaak's own vendored `docker_no2fa.py` already had
`SOLO_CACHE = None` appended** - a teammate had already found and fixed
this exact bug for openzaak specifically, concurrently, and it was already
sitting in both the vendor file and the live ConfigMap (confirmed via
`kubectl get configmap openzaak-no2fa-settings`, last updated 7 days
before this session).

The gap was never the fix itself - it was that the fix never reached the
*running* pod: that ConfigMap is mounted via `subPath`
(`extraVolumeMounts` in values.yaml), and **`subPath` ConfigMap mounts
never live-update in Kubernetes**, regardless of how long the ConfigMap
itself has held the correct content - only a pod recreation re-mounts the
current file. This specific openzaak pod had been running since long
before that ConfigMap update and was simply never restarted. One
`kubectl delete pod` (Deployment recreated it immediately) picked up the
already-correct mounted file, confirmed via
`settings.SOLO_CACHE == None` inside the fresh pod - fully resolved, no
code change needed at all. Deleted the poll-and-clear script + its
deploy.sh wiring written while chasing this the hard way - dead weight
now that the real fix (already-committed, just needed a restart) is in
effect.

**Lesson for next time hitting an unexplained stale-singleton-config bug
on any of these apps**: check `vendor/.../<app>/docker_no2fa.py` for an
existing `SOLO_CACHE = None` fix and whether the *running pod* actually
postdates the ConfigMap's last change, before re-diagnosing from
scratch - `subPath` mounts silently going stale after a values.yaml/vendor
edit, with no error or warning anywhere, is easy to miss.

**Full suite after all of the above: 66 passed, 5 skipped (all
expected - monitoringLogging off, objecttypen profile off on merged
shape), 0 failed.**

## Making the zac 5.4.2/PKCE experiment switchable (off by default), and what verifying that on a real fresh deploy turned up

Asked to gate the teammate's zac 5.4.2/PKCE experiment (from the PodiumD
4.9 catch-up entry above) behind an explicit, off-by-default switch,
rather than it silently applying just because values.yaml happened to
carry the override. Landed on `zac.experimentalPkce` (top-level, next to
the existing `zac.enabled`) as the one flag, with three things keyed off
it:

- `scripts/lib/zac-experimental-pkce.sh` - reads the flag, and refuses
  deploy.sh outright (clear message, not a silent misconfiguration) if
  it's on without the currently-selected podiumd version's zac chart
  actually supporting `AUTH_ENABLE_PKCE` - checked by inspecting the
  vendored podiumd tarball directly (same trick as
  `detect-objecten-shape.sh`), since this only ever runs against an
  unpublished/hand-bumped local `--path` checkout with no real version to
  key off. Produces the `--set podiumd.zac.image.tag=5.4.2` override only
  when on (values.yaml's own hardcoded copy of that override removed).
- `scripts/lib/fixup-zac-pkce-realm.py` - new post-renderer, patches the
  vendored realm's own "zaakafhandelcomponent" client
  `pkce.code.challenge.method` back to `""` when the switch is off (the
  checked-in realm.json keeps `"S256"` unconditionally - same
  "one file, patched post-render for the other state" pattern as
  `fixup-merged-objecten-shape.py`).
- `scripts/lib/sync-zac-pkce-realm.sh` - new, runs on every deploy.sh
  unconditionally (zac/keycloak are both core). Reconciles that same
  attribute into the *live*, already-imported Keycloak realm via
  `kcadm.sh` - necessary because Keycloak's `--import-realm` only ever
  imports a realm that doesn't already exist yet, and this project's
  Keycloak persists to the shared Postgres instance (not ephemeral), so
  that's true indefinitely after the first import. Without this, flipping
  the switch on an already-provisioned cluster would silently do nothing
  to the running realm - the exact gap that produced today's earlier
  manual `kcadm.sh` fix in the first place.

`tests/test_pkce.py`'s own `test_zac_client_now_sends_a_pkce_code_challenge`
now skips (rather than failing) when the switch is off, checked against
the live zac ConfigMap's `AUTH_ENABLE_PKCE` presence - the same signal
`zac-experimental-pkce.sh` uses - not against local values.yaml, matching
this suite's own "assert against deployed state" convention.

### Verifying the "off" default on a genuinely fresh deploy surfaced two more real, unrelated bugs

Rather than trust the switch from source-reading alone, verified it end
to end - which meant a real fresh deploy, since this cluster's zac had
already run the 5.4.2 experiment for a while under the previous session.
That surfaced two separate, pre-existing problems neither related to the
switch itself:

1. **Flowable's own schema migration is one-way, and the experiment had
   already crossed that line.** The 5.4.2 pod's newer Flowable engine had
   upgraded `zac`'s own Postgres schema from `7.2.0.2` to `8.0.0.0` sometime
   during the prior session - confirmed via `flowable.act_ge_property`'s
   own `schema.version`/`schema.history` rows. Once that happens, an older
   zac image (what the switch now correctly reverts to) can never start
   again against that same database (`Could not update Flowable database
   schema: unknown version from database: '8.0.0.0'`) - Flowable has no
   downgrade path. Kubernetes' own rollout safety net kept the old,
   working 5.4.2 pod serving traffic throughout (the new 5.1.0 pod just
   never became ready, rather than causing an outage), but the rollback
   itself was permanently stuck. Resolved by asking Kees, who chose a full
   `reset-namespace.sh` + fresh `deploy.sh --full` over a narrower
   schema-only wipe - confirmed clean afterward (fresh Flyway/Flowable
   bootstrap, zac starts on 5.1.0 with no schema conflict).
2. **A latent seed-order race in `openobject/demodata.json`, never
   exercised before because this project's own dev cluster had never done
   a truly fresh `objecten` deploy since the token-identifier rename
   (`zaakafhandelcomponent`->`zac`, `openformulieren`->`open-formulieren`)
   landed.** The vendored fixture still had its own `token.tokenauth` rows
   (pk=2/3, old identifiers) creating the *same token values*
   (`fakeZacObjectsToken`, `fakeOpenFormulierenObjectsToken`) that
   values.yaml's own declarative `objecten.configuration.data` tokenauth
   items already create under the renamed identifiers - `objecten-config`'s
   Job (running first) created them declaratively, then
   `seed-fixtures.sh`'s own `loaddata` immediately hit `IntegrityError:
   ... duplicate key value violates unique constraint
   "token_tokenauth_token_d0421f81_uniq"` trying to insert its own,
   now-fully-redundant copies. Every earlier deploy in this whole session
   skipped this exact code path (`seed()`'s own idempotency check: skip
   `loaddata` entirely once `core.Object` already has *any* data - true
   on every reused cluster this session touched, never true on a truly
   fresh one). Fixed by deleting the two dead rows (and their five
   `token.permission` grants, `token_auth` 2/3) from the vendored fixture
   directly - confirmed nothing else in this project references the old
   identifiers, and the declarative tokens are `is_superuser: true`
   already, making the fixture's own fine-grained per-object-type grants
   redundant on top of being duplicate. A first attempt at this edit via
   `json.dump()` reformatted the *entire* file's array indentation as a
   side effect (every compact `["x"]` became one-item-per-line) - reverted
   and redid it as a surgical text edit of just the seven rows instead, to
   keep the vendored file's own diff legible.

**Full suite after all of the above, on the freshly reset namespace: 65
passed, 6 skipped (all expected - monitoringLogging off, objecttypen
profile off on merged shape, PKCE switch off), 0 failed.**
