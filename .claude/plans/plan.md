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
