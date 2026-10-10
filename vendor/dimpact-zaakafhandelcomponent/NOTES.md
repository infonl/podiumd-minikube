# Provenance

Every file here is either copied from `dimpact-zaakafhandelcomponent` (at
commit `a69b38b5aaec9e80d0fd7f6bbfb63f0558fbf060`, 2026-07-15) or written
for this project. Nothing here is a live reference: `helm template` never
reads `dimpact-zaakafhandelcomponent`. Each entry names the source, any
change, and what in this repository reads it; the reasoning behind the
changes is in `.claude/plans/plan.md`.

## Copied verbatim

- `wiremocks/{bag-wiremock,smartdocuments-wiremock}/`
  ← `scripts/docker-compose/imports/{same name}/` (`mappings/`, `__files/`,
  `README.md`). Read by `templates/wiremock/` through
  `podiumd-minikube.wiremockSets` (BAG always, SmartDocuments with the
  `wiremock` profile).
- `postgres/fixtures/openzaak/*.sql` (10 files)
  ← `scripts/docker-compose/imports/openzaak-database/database/*.sql`.
  Read by `templates/postgres/configmap-fixtures.yaml`, which loads only
  `01` (Applicaties, catalogus), `05` (zaaktype-test-1) and `07` (sequence
  resets only), leaving out the other zaaktypes, the demo documents and the
  docker-compose services nothing here uses.
- `postgres/fixtures/{openklant,openarchiefbeheer}/1-setup-applicatie.sql`
  ← `scripts/docker-compose/imports/{openklant,openarchiefbeheer}-database/database/`.
  Read by `templates/postgres/configmap-fixtures.yaml`.
- `metrics/{otel-collector,tempo,prometheus,grafana-datasources}.yaml`
  ← `scripts/docker-compose/imports/{otel-collector,tempo,prometheus,grafana}/`.
  Read by `templates/metrics/` (the `metrics` profile without
  `monitoringLogging`).
- `pabc/pabc-mapping-data.json`
  ← `scripts/docker-compose/imports/pabc-database/json-mapping/`.
  Read by `templates/pabc/configmap-mapping-data.yaml` (the pabc-migrations Job).

## Copied and changed

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
  `templates/keycloak/`; Keycloak imports it once, so `lib.keycloak`
  reconciles the live realm on every deploy.

## Written for this project

- `postgres/00-create-databases.sql`: the databases, roles and PostGIS
  extensions of the single shared Postgres, with each docker-compose
  database container's credentials. Read by `templates/postgres/` (init) and
  `lib.postgres` (databases added later).
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
