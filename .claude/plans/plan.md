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
- **OIDC client secrets**: PodiumD's own `keycloak-podiumd-realm-secrets.yaml`
  **auto-generates** an OIDC client secret for every app, unconditionally,
  whenever neither `configuration.secrets.keycloak_client_secret` nor
  `configuration.oidcSecret` is explicitly set on that app (confirmed via the
  comment in `templates/validations.yaml`) — this happens regardless of
  whether `keycloak-operator` itself is enabled. Since we need the *specific*
  secrets already baked into our vendored ZAC realm.json (e.g.
  `openzaakZaakafhandelcomponentClientSecret`), every relevant app gets
  `podiumd.<app>.configuration.secrets.keycloak_client_secret` set explicitly
  to the matching compose value — never left to auto-generate.

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
expects (`<namespace>-<app>` for the PV, `podiumd.<app>.persistence.
existingClaim` for the PVC), backed by minikube's `standard` StorageClass.
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
confirmed on openzaak.

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
  list, not just "disable flower".** The Maykin chart family structurally
  supports separate worker/beat/nginx sub-deployments per app (confirmed in
  the earlier `helm show values` research — worker/beat/flower/nginx resource
  blocks exist in the shared chart shape), but whether each is actually
  *needed* varies per app, and compose's own service list is the ground
  truth for "needed":
  - **openklant**: compose runs it as a **single bare container** — no
    `openklant-celery` service exists anywhere in `docker-compose.yaml`. If
    the openklant chart defaults `worker.enabled`/`nginx.enabled` on, both
    must be explicitly set to `false` — running them would be pure excess
    over compose, not parity.
  - **openzaak**: compose runs `openzaak-nginx` **unconditionally** (no
    `profiles:` entry — required for chunked transfer-encoding on large file
    uploads, matching how compose already fronts it) but gates
    `openzaak-celery` behind `profiles: ["opennotificaties",
    "openformulieren"]`. So: `podiumd.openzaak.nginx.enabled: true` (leave on)
    but `podiumd.openzaak.worker.enabled: false` for the core profile,
    flipped to `true` only when `opennotificaties.enabled` or
    `openformulieren.enabled` is set.
  - **openarchiefbeheer**/**openformulieren** (both profile-gated, checked at
    step 5): compose runs *both* a `-celery` and a separate `-celery-beat`
    service for each — so both `worker.enabled` and `beat.enabled` (exact key
    names TBD per chart) should stay on when that profile is active, matching
    compose 1:1 there.
  - **opennotificaties**: compose runs one `opennotificaties-celery` (no
    separate beat service) — only `worker.enabled: true`, `beat.enabled:
    false`, when its profile is active.
  - **objecten**: compose runs `objecten-api-celery` whenever the `objecten`
    profile itself is active (no separate beat) — `worker.enabled: true`,
    `beat.enabled: false` in that case.
  This audit needs a final per-chart confirmation of the exact
  `worker.enabled`/`nginx.enabled`/`beat.enabled` key names at implementation
  time (steps 3 and 5), the same way `flower.enabled` was already confirmed —
  but the *target state* (which pods compose actually runs, per app) is fully
  settled now, from `docker-compose.yaml`'s own service list, not guesswork.

## Storage: minikube's default StorageClass

Everything that would otherwise need an Azure-specific storage class is now
either replaced with a plain single-container Deployment (Redis, Solr — no
operator, so no Azure-tied PVC templates at all) or handled by the
pre-provisioned-PV/PVC hook mechanism described above (openzaak, openklant,
and later opennotificaties/openarchiefbeheer/openformulieren). The shared
Postgres template and every pre-provisioned PV/PVC use minikube's own default
StorageClass, `standard` (backed by the `storage-provisioner` addon,
hostPath-based dynamic provisioning) — `storageClassName` is either left
unset (falls back to the cluster's marked-default class) or explicitly set to
`standard`, never inheriting any Azure-specific default.

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

**Not yet approved by the user in final form** — implementation (build order
steps 0–5 above) has **not started**. Next step once resumed: step 0
(vendoring).
