# Provenance

Everything under this directory was copied from
`dimpact-zaakafhandelcomponent` at commit `a69b38b5aaec9e80d0fd7f6bbfb63f0558fbf060`
(2026-07-15), except where noted as newly authored for this project. Nothing
here is a live reference — this project never reads
`dimpact-zaakafhandelcomponent` at `helm template`/`helm install` time.

## Copied verbatim

- `wiremocks/{brp-personen-wiremock,smartdocuments-wiremock,kvk-wiremock,bag-wiremock}/`
  ← `scripts/docker-compose/imports/{same-name}/` (full `mappings`/`__files`
  directories, plus each service's own `README.md`).
- `postgres/fixtures/openzaak/*.sql` (10 files)
  ← `scripts/docker-compose/imports/openzaak-database/database/*.sql`.
- `postgres/fixtures/openklant/1-setup-applicatie.sql`
  ← `scripts/docker-compose/imports/openklant-database/database/1-setup-applicatie.sql`.
- `postgres/fixtures/openarchiefbeheer/1-setup-applicatie.sql`
  ← `scripts/docker-compose/imports/openarchiefbeheer-database/database/1-setup-applicatie.sql`.
- `postgres/fixtures/zac/init-zac-database.sql`
  ← `scripts/docker-compose/imports/zac-database/init-zac-database.sql`
  (also folded directly into `postgres/00-create-databases.sql`'s final
  section — kept here too as a traceable standalone copy).
- `metrics/{otel-collector,tempo,prometheus,grafana-datasources}.yaml`
  ← `scripts/docker-compose/imports/{otel-collector,tempo,prometheus,grafana}/*.yaml`.
- `policies/main/*.rego` + `policies/main/policies`
  ← `src/main/resources/policies/*`.
- `policies/test/*.rego`
  ← `src/test/resources/policies/*.rego`.

## Copied and patched (not byte-for-byte)

- `keycloak/zaakafhandelcomponent-realm.json`
  ← `scripts/docker-compose/imports/keycloak/realms/zaakafhandelcomponent-realm.json`,
  with the `zaakafhandelcomponent` and `pabc` clients' `redirectUris`/
  `webOrigins` each gaining one appended entry (`http://zac.local/*`/
  `http://zac.local` and `http://pabc.local/*`/`http://pabc.local`
  respectively) — every existing entry (localhost, host.docker.internal) is
  kept unchanged, so a port-forward-based fallback still works too. Diffed
  against a reformatted copy of the original to confirm only these four
  array entries changed and nothing else. See `plan.md`'s Keycloak section
  for why this is needed (Traefik-exposed `.local` hostnames aren't in the
  original realm's allow-list).

## Newly authored (not copied from anywhere)

- `postgres/00-create-databases.sql` — creates the 10 databases/roles this
  chart's single shared Postgres instance needs (one per
  docker-compose.yaml database container it replaces), with credentials
  copied verbatim from each service's `POSTGRES_USER`/`POSTGRES_PASSWORD`/
  `POSTGRES_DB` env vars in `docker-compose.yaml`, plus explicit
  `CREATE EXTENSION postgis` statements for the four databases that need it
  (openzaak, objects, opennotificaties, openarchiefbeheer) rather than
  relying on docker-entrypoint-initdb.d script-ordering. **Note**: includes
  `openformulieren`, which the original plan draft's "9 databases" list had
  omitted — it also runs its own Postgres container in `docker-compose.yaml`
  and needs a database here once that profile is wired.
- `postgres/01-seed-fixtures.sh` — merges the three separate
  `init.sh`/`fill-data-on-startup.sh` pairs (openzaak/openklant/
  openarchiefbeheer) into one script, since this shared instance only runs
  `docker-entrypoint-initdb.d` once for the whole cluster rather than once
  per original per-service container. Each app's own readiness-polling query
  and fixture SQL are unchanged from its source script — only parameterized
  by database/user instead of assuming the container's single default
  database, and merged into one file with three backgrounded functions
  instead of three separate scripts. Marked executable
  (`chmod +x`) so Postgres's `docker-entrypoint-initdb.d` mechanism executes
  it directly as a subprocess (matching how the original `init.sh` files were
  executable) rather than sourcing it into the parent shell.

  Dropped intentionally: the original scripts each ran `useradd <appname>`
  before their `psql` calls. Postgres's official Docker image runs every
  `docker-entrypoint-initdb.d` script under a temporarily-trusted local
  connection (finalized `pg_hba.conf` rules only take effect after all init
  scripts complete), so these `useradd` calls don't gate `psql` auth in
  practice — running three of them (one per app) in a single merged script
  would just be noise.
