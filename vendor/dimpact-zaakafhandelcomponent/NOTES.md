# Provenance

Every file here is either copied from `dimpact-zaakafhandelcomponent` (at
commit `a69b38b5aaec9e80d0fd7f6bbfb63f0558fbf060`, 2026-07-15) or written
for this project. Nothing here is a live reference: `helm template` never
reads `dimpact-zaakafhandelcomponent`. Each entry names the source, any
change, and what in this repository reads it; the reasoning behind the
changes is in `.claude/plans/plan.md`.

## Copied verbatim

- `bag/*.json`
  ← `scripts/docker-compose/imports/bag-wiremock/__files/`; its WireMock
  mappings became `files/bag-mock/nginx.conf`. Read by
  `templates/bag-mock/`. ZAC's SmartDocuments mappings are not copied:
  SmartDocuments is off in ZAC, as in every reference environment.

## Written for this project

- `postgres/00-create-databases.sql`: the databases, roles and PostGIS
  extensions of the single shared Postgres. Read by `templates/postgres/`
  (init) and `podiumd_minikube.postgres` (databases added later). No
  fixture data: the apps' setup_configuration Jobs and podiumd's catalogi
  Job create what ZAC's compose fixtures held, as the reference
  environments.
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
