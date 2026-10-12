# Provenance

Every file here is either copied from `dimpact-zaakafhandelcomponent` (at
commit `a69b38b5aaec9e80d0fd7f6bbfb63f0558fbf060`, 2026-07-15) or written
for this project. Nothing here is a live reference: `helm template` never
reads `dimpact-zaakafhandelcomponent`. Each entry names the source, any
change, and what in this repository reads it; the reasoning behind the
changes is in `.claude/plans/plan.md`.

## Copied verbatim

- `wiremocks/bag-wiremock/`
  ← `scripts/docker-compose/imports/bag-wiremock/` (`mappings/`, `__files/`).
  Read by `templates/wiremock/`. ZAC's SmartDocuments mappings are not
  copied: SmartDocuments is off in ZAC, as in every reference environment.
- `postgres/fixtures/openzaak/{01,05,07}-*.sql`
  ← `scripts/docker-compose/imports/openzaak-database/database/`: `01`
  (Applicaties, catalogus), `05` (zaaktype-test-1), `07` (sequence resets
  only). The other zaaktypes' files are not copied. Read by
  `templates/postgres/configmap-fixtures.yaml`, which leaves out the demo
  documents and the docker-compose services nothing here uses.
- `postgres/fixtures/{openklant,openarchiefbeheer}/1-setup-applicatie.sql`
  ← `scripts/docker-compose/imports/{openklant,openarchiefbeheer}-database/database/`.
  Read by `templates/postgres/configmap-fixtures.yaml`.
- `pabc/pabc-mapping-data.json`
  ← `scripts/docker-compose/imports/pabc-database/json-mapping/`.
  Read by `templates/pabc/configmap-mapping-data.yaml` (the pabc-migrations Job).

## Copied and changed

- `metrics/{prometheus,grafana-datasources}.yaml`
  ← `scripts/docker-compose/imports/{prometheus,grafana}/`. Changes: no
  Tempo (neither reference environment runs it), so no `tempo` scrape job
  and no Tempo datasource (`deleteDatasources` removes it from Grafana's
  database on clusters deployed before), Prometheus is the default; Prometheus scrapes
  ZAC's WildFly metrics at `zac-admin:9990`. Read by `templates/metrics/`
  (the `metrics` profile without `monitoringLogging`).

- `keycloak/zaakafhandelcomponent-realm.json`
  ← `scripts/docker-compose/imports/keycloak/realms/zaakafhandelcomponent-realm.json`.
  Changes: `*.local` redirect URIs and web origins for `zaakafhandelcomponent`
  and `pabc` (the render adds https twins, `manifests.with_https`);
  `pabc` requires PKCE (`S256`; its middleware always sends it); clients
  for the Django apps (`openzaak`, `openklant`, `objecten`, `objecttypen`,
  `opennotificaties`, `openformulieren`, `openarchiefbeheer`, PKCE off:
  their mozilla-django-oidc-db has no PKCE support) and for `ita`, `kiss`,
  `openinwoner`, `referentielijsten`, `openbeheer` and `openbao`, with the
  podiumd chart's redirect URIs, client roles and mappers; the seven
  Django apps' clients also get the `username` and `client roles` (claim
  `groups`) mappers and the `administrators` role. Realm settings as
  podiumd's realm template: access token 60 s, brute-force protection
  (failure factor 5), refresh token revocation, events and admin events,
  SMTP to mailpit.
  `zaakafhandelcomponent` keeps `S256`; `manifests.fix_realm` clears it in
  the render while `zac.experimentalPkce` is off. Read by
  `templates/keycloak/`; Keycloak imports it once, so `podiumd_minikube.keycloak`
  reconciles the live realm on every deploy.

## Written for this project

- `postgres/00-create-databases.sql`: the databases, roles and PostGIS
  extensions of the single shared Postgres, with each docker-compose
  database container's credentials. Read by `templates/postgres/` (init) and
  `podiumd_minikube.postgres` (databases added later).
- `postgres/01-seed-fixtures.sh`: ZAC's per-database `init.sh` /
  `fill-data-on-startup.sh` scripts merged into one for the shared
  Postgres; waits for each app's migrations, runs its fixture SQL, then
  resets the sequences the fixtures advanced by hand. Read by
  `templates/postgres/deployment.yaml` (executable, so initdb runs it).
- `{objecttypen,openzaak,opennotificaties,openformulieren,openarchiefbeheer}/docker_no2fa.py`:
  settings modules that honour `DISABLE_2FA`, which the images only read in
  their dev settings. Read by `templates/<app>/configmap-no2fa-settings.yaml`.
- `objecten/docker_no_solo_cache.py`: disables django-solo's cache for
  Objecten's notifications configuration, which went stale. Read by
  `templates/objecten/configmap-no-solo-cache-settings.yaml`.
- `{openklant,openformulieren,openarchiefbeheer}/create_superuser.py`: the
  admin user for apps whose charts create none. Read by
  `templates/<app>/create-superuser-job.yaml`.
- `openformulieren/create_productaanvraag_form.py`: the productaanvraag
  form with its Objects API registration. Read by
  `templates/openformulieren/productaanvraag-form-job.yaml`.
- `zac/seed_productaanvraag_zaakafhandelparameters.py`: ZAC's
  zaakafhandelparameters for zaaktype-test-1. Read by
  `templates/zac/productaanvraag-zaakafhandelparameters-job.yaml`.
