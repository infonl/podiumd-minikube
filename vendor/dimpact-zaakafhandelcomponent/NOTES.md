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
- `postgres/fixtures/openzaak/01-setup-zac-config.sql`
  ← `scripts/docker-compose/imports/openzaak-database/database/`: the
  Applicaties and the catalogus; no zaaktypen (podiumd's
  create_required_catalogi_job adds the e2e zaaktype). Read by
  `templates/postgres/configmap-fixtures.yaml`, which gives the catalogus
  ZAC's bron RSIN.
- `postgres/fixtures/openklant/1-setup-applicatie.sql`
  ← `scripts/docker-compose/imports/openklant-database/database/`. Open
  Archiefbeheer's fixture held only the admin user, which
  `templates/openarchiefbeheer/create-superuser-job.yaml` creates.
  Read by `templates/postgres/configmap-fixtures.yaml`.
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
- `zac/seed_productaanvraag_zaakafhandelparameters.py`: ZAC's
  zaakafhandelparameters for podiumd's e2e zaaktype. Read by
  `templates/zac/productaanvraag-zaakafhandelparameters-job.yaml`.
