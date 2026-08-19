# Provenance

Everything under this directory was copied from
`dimpact-zaakafhandelcomponent` at commit `a69b38b5aaec9e80d0fd7f6bbfb63f0558fbf060`
(2026-07-15), except where noted as newly authored for this project. Nothing
here is a live reference — this project never reads
`dimpact-zaakafhandelcomponent` at `helm template`/`helm install` time.

## Copied verbatim

- `openzaak/fake-test-document.pdf`
  ← `scripts/docker-compose/imports/openzaak/uploads/fake-test-document.pdf`.
  Missed in the initial step 0 pass (only added once step 3 needed it) -
  embedded as a ConfigMap `binaryData` entry (`.Files.Get | b64enc`) and
  mounted via `subPath` directly at the three target paths inside
  `/app/private-media`, since the bundled `openzaak` chart has no
  `initContainers`/`extraInitContainers` support at all (only
  `extraVolumes`/`extraVolumeMounts`, which turned out sufficient - see
  `plan.md`'s step 3 notes).
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
- `objecten/demodata.json`, `objecttypen/demodata.json`, `openobject/demodata.json`
  — Django fixtures used by `scripts/lib/seed-fixtures.sh` (`manage.py loaddata`),
  matching docker-compose's own `*-import` one-shot containers.
  **Exception to this file's pinned commit** (these three predate/postdate
  it): `objecten/demodata.json` ← `scripts/docker-compose/imports/objects-api/fixtures/demodata.json`
  at commit `16e90ce2a` (last commit to touch that file before it was
  deleted); `objecttypen/demodata.json` ← `scripts/docker-compose/imports/objecttypes-api/fixtures/demodata.json`
  at commit `52976809f` (same reason); both from before
  `dimpact-zaakafhandelcomponent`'s Open Object 4.0 upgrade (commit
  `a98d5ae2b`, "chore: upgrade to Open Object 4.0.2 in Docker Compose"),
  which merged the two separate `objects-api`/`objecttypes-api` apps (and
  their fixtures) into one. `openobject/demodata.json` ←
  `scripts/docker-compose/imports/open-object/fixtures/demodata.json` at
  that same commit `a98d5ae2b`, i.e. the merged app's own combined fixture.
  Kept as three separate files (not reconciled into one) because
  `scripts/lib/seed-fixtures.sh` needs to support both podiumd shapes: classic
  (`objecten`+`objecttypen` as two separate subcharts/databases - use the
  first two) and merged (`openobject`, one subchart serving both APIs -
  use the third).

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
  Also: the `zaakafhandelcomponent` client's `pkce.code.challenge.method`
  attribute cleared from `"S256"` to `""`. Found live: this attribute
  requires PKCE, but PKCE support in ZAC itself
  (`feat: add configurable PKCE support for the OIDC authorization code
  flow`, PR #6490) is unreleased - our pinned `podiumd.zac.image.tag:
  "5.0.1"` predates it, so its bundled `oidc.json` has no `enable-pkce`
  field at all and never sends a `code_challenge`, making Keycloak reject
  every login with `invalid_request: Missing parameter:
  code_challenge_method`. Revert this one attribute back to `"S256"` once
  `podiumd.zac.image.tag` is bumped to a release that includes PR #6490,
  and set `podiumd.zac` env var `AUTH_ENABLE_PKCE: "true"` at the same time
  (no values.yaml field for it - the chart's `zac` container env comes
  entirely from its own generated ConfigMap/Secret, so this needs a Helm
  post-renderer or an upstream chart change).

  **Update (PodiumD 4.9 release prep)**: done. `podiumd.zac.image.tag`
  bumped to `5.4.2` (chart 1.0.289, past PR #6490) - `AUTH_ENABLE_PKCE`
  already had a values.yaml field by this point
  (`podiumd.zac.auth.enablePkce: true`, harmless no-op until now, see that
  field's own comment) since the chart itself grew a proper
  `config.yaml` line for it in the interim, so no post-renderer was needed
  after all. This one attribute reverted back to `"S256"` here (fresh
  imports) and patched into the already-imported live realm via the Admin
  API (Keycloak only imports a realm once - editing this file alone
  doesn't affect a realm that already exists, the same gap the
  `zac.local` `/etc/hosts` line above already needed the same workaround
  for). Confirmed live: ZAC's own authorization redirect now carries a
  real `code_challenge`/`code_challenge_method=S256`, and the full login
  round trip (real credentials, code exchange, authenticated app shell)
  still completes cleanly - see `tests/test_pkce.py`'s
  `test_zac_client_now_sends_a_pkce_code_challenge`.

  **Update (made switchable, off by default)**: the whole zac 5.4.2/PKCE
  experiment above depends on a local-only, hand-bumped podiumd `--path`
  checkout (podiumd 4.9 itself still isn't released) - having it always
  on just because this values.yaml carries the override meant every
  deploy silently required that manual checkout step, with no way to opt
  out short of hand-editing values.yaml/this realm.json back. Gated
  behind a new top-level `zac.experimentalPkce` (off by default) instead -
  `scripts/lib/zac-experimental-pkce.sh` reads it and refuses deploy.sh
  with a clear message if it's on without a PKCE-aware zac chart actually
  selected (checked by inspecting the vendored podiumd tarball directly,
  the same way `scripts/lib/detect-objecten-shape.sh` detects its own
  shape - not a version-number guess). `scripts/lib/fixup-zac-pkce-realm.py`
  now patches this file's own `pkce.code.challenge.method` back to `""`
  post-render whenever the switch is off (this file itself still carries
  `"S256"` unconditionally - values.yaml can't branch a vendored file's
  content on a flag, same reason `fixup-merged-objecten-shape.py` exists
  for `objecten.configuration.data`), and
  `scripts/lib/sync-zac-pkce-realm.sh` reconciles that same value into the
  *live*, already-imported realm on every deploy - the exact Admin-API
  patch described above, now automatic and idempotent instead of a
  one-off manual fix. `tests/test_pkce.py`'s own
  `test_zac_client_now_sends_a_pkce_code_challenge` skips entirely when
  the switch is off.

  The switch's *off* state (the default) is fully verified end to end,
  including on a genuinely fresh deploy - see plan.md. Its *on* state is
  not, currently: the local chart checkout the "Update (PodiumD 4.9
  release prep)" entry above was verified against isn't available right
  now, and the zac chart repo doesn't have a git branch with that bump
  either - only each piece of the switch mechanism itself (detection,
  the realm patch, the live sync) has been tested in isolation. Treat
  `zac.experimentalPkce: true` as untested until a real chart checkout
  with zac 1.0.289+ is reachable again to verify against.

  Also: seven new clients added (`openzaak`, `openklant`, `objecten`,
  `objecttypen`, `opennotificaties`, `openformulieren`,
  `openarchiefbeheer`) - none exist in the original imported realm at all,
  since none of these apps' own django-admin login goes through OIDC by
  default in this project (plain username/password, matching compose).
  Added ahead of actually wiring each one up in `values.yaml`'s
  `podiumd.<app>.configuration.data` (`oidc_db_config_admin_auth`), per the
  production podiumd chart's own PKCE rollout plan (see
  `docs/apps/keycloak/keycloak-security-updates.md`'s PKCE Enforcement
  section, in the sibling `helm-charts` repo - not vendored here) - enabling
  it for every component at once caused a real production incident
  (HTTP 403 on every login page, some components' bundled
  `mozilla_django_oidc` too old for PKCE) and was rolled back. `openzaak`
  is the only one with an actual `oidc_db_config_admin_auth` step wired up
  so far, but **not** with PKCE - all seven clients' own
  `pkce.code.challenge.method` was left `""` here.

  **Update (PodiumD 4.9 release prep)**: corrects an earlier, now-stale
  version of this same paragraph, which had claimed `openzaak`'s client
  was set to `"S256"` on the strength of a `mozilla_django_oidc` "5.0.2"
  that doesn't match anything ever actually checked live - confirmed now,
  directly, that's wrong: checked all seven Django apps' own running pods
  (`openzaak`/`openklant`/`objecten`/`objecttypen`/`opennotificaties`/
  `openformulieren`/`openarchiefbeheer`, each bundling `mozilla-django-oidc-db`
  1.1.1 or 2.0.1 depending on the app) and *none* of them have PKCE support
  at any version bundled here - no `OIDCProvider` model field with "pkce"
  in the name, `grep -ri pkce` across each installed package tree finds
  nothing, and upstream's own CHANGELOG.rst plus a GitHub search across
  every issue/PR in `maykinmedia/mozilla-django-oidc-db` for "pkce" also
  return nothing. Unlike ZAC's own PKCE story elsewhere in this file, this
  isn't a "revisit after the next version bump" situation - it's a genuine
  upstream gap in the shared library itself, with no visible sign it's
  even planned. All seven clients' `pkce.code.challenge.method` stay `""`
  accordingly, guarded live by `tests/test_pkce.py`'s
  `test_django_app_client_does_not_require_pkce` (parametrized over all
  seven) - see that test module's own docstring for the fuller
  investigation. `openarchiefbeheer`'s `oidc_db_config_admin_auth` step is
  separately still not wired up at all, since that app (v1.1.1, this
  project's current pin) has no OIDC support in its chart yet - unrelated
  to the PKCE finding above, which only concerns the Keycloak client
  attribute, not whether login through it is actually usable yet.
  Also: the pre-existing `pabc` client's `pkce.code.challenge.method` set
  to `"S256"` (previously absent - Keycloak's own default is "not
  required"). Unlike every Django app here, safe to enable unconditionally
  - confirmed live (curling `pabc.local/api/challenge` directly) that
  PABC's own ASP.NET Core OpenIdConnect middleware already sends a real
  `code_challenge`/`code_challenge_method=S256` on every login attempt
  regardless of this setting (no `Oidc__*` env var controls it - it's just
  always on by default in this framework version). Confirmed live end to
  end that Keycloak now enforcing it doesn't break anything: a full
  browser navigation through the captured authorization URL, real
  credentials, and the code exchange all complete cleanly with no
  PKCE-related error. See `values.yaml`'s own `podiumd.pabc.settings.oidc.pkceEnabled`
  comment for a *separate*, unrelated finding from the same investigation
  (PABC's own hardcoded `CookieSecurePolicy.Always` blocks the actual
  session from ever being established over this project's HTTP-only
  ingress, PKCE or not) - a known limitation, not fixed here.

  **Update (ITA/KISS added, then disabled again)**: two more Keycloak
  clients added, `ita` and `kiss` - neither a docker-compose service at
  all, both brought in purely to extend this same PKCE investigation to
  two more PodiumD-only components (`podiumd.ita`/`podiumd.kiss` in
  values.yaml, both `enabled: false`). Confirmed by cloning each app's own
  public source that both hardcode `options.UsePkce = true` in their own
  OpenIdConnect setup, the same pattern as pabc's own
  `AuthenticationExtensions.cs` (almost the same file, in fact) - so PKCE
  itself is unconditionally on for both, same category of finding as pabc.
  Unlike pabc, though, neither app can actually serve a single HTTP request
  in this project's HTTP-only environment at all: both also never set
  `RequireHttpsMetadata` anywhere in their source (it stays at the
  OpenIdConnect middleware's own default of `true`), and both apps'
  authentication middleware evidently resolves the OIDC handler's options
  eagerly on *every* request - even the chart's own `/healthz` probe,
  confirmed live via a 500 citing exactly this - so every single request
  throws `InvalidOperationException: The MetadataAddress or Authority must
  use HTTPS unless disabled for development by setting
  RequireHttpsMetadata=false` before ever reaching a redirect. No
  values.yaml/`extraEnvVars`-level fix exists (this value is never read
  from configuration in either app's source, only ever set - or not - in
  code), so the only real fix would be actual TLS termination in front of
  Keycloak, deliberately out of scope for this HTTP-only-by-design project.
  Both Keycloak clients kept (`pkce.code.challenge.method: ""`, matching
  every other not-yet-verified client's own starting point), and both
  `podiumd.ita`/`podiumd.kiss` values.yaml blocks kept fully wired but
  `enabled: false` - re-enabling either just recreates the same
  permanently crash-looping Deployment, not a live-testable one, without a
  source change on the app's own side. See `values.yaml`'s own
  `podiumd.ita`/`podiumd.kiss` comments and
  `tests/test_pkce.py`'s module docstring for the fuller detail.

  **Found while cleaning up afterward** (unrelated to ita/kiss, but the
  same live realm): `scripts/lib/sync-zac-pkce-realm.sh` (added by the
  same teammate commit that made the zac 5.4.2/PKCE experiment
  switchable) calls `kcadm.sh` directly via `kubectl exec` to reconcile
  the live realm's `zaakafhandelcomponent` client - this OOM-kills the
  Keycloak pod outright (exit 137, confirmed live), the same `kcadm.sh`
  memory-limit issue already documented above as the reason this file's
  own live fixes use a port-forward + raw Admin API `curl` instead of
  `kcadm.sh`. Every `deploy.sh --full` run currently fails to reconcile
  silently (and periodically restarts Keycloak) as a result - worth
  fixing in that script the same way, not done here (out of scope for the
  task that surfaced it). Separately, `tests/test_pkce.py`'s own
  `_zac_experimental_pkce_live` skip-condition helper was checking the
  zac ConfigMap's `AUTH_ENABLE_PKCE` key, which - per this file's own
  paragraph above - is deliberately left `"true"` unconditionally as a
  no-op, so that check always returned true regardless of
  `zac.experimentalPkce`'s real value. Fixed in that test to check the
  realm client's own `pkce.code.challenge.method` instead, the same live
  signal every other guard test in that module already uses.

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
