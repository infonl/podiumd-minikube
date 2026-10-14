#!/bin/bash
# Merged replacement for the per-service init.sh/fill-data-on-startup.sh
# pairs in docker-compose.yaml's openzaak-database and openklant-database,
# consolidated onto this one shared Postgres instance. Each block keeps its
# source script's behaviour (readiness query, fixture SQL), parameterized by
# database/user. Each block backgrounds its own wait loop, as the originals
# do, so both seed in parallel once their app has migrated. Open
# Archiefbeheer's admin user comes from its create-superuser Job instead.

FIXTURES_DIR="$(dirname "$0")/fixtures"

# A major migration restores a dump that already holds the fixtures
# (podiumd_minikube.postgres.RESTORING, outside PGDATA on the same volume).
if [ -e /var/lib/postgresql/data/restoring ]; then
  echo "Restoring a dump: fixtures skipped"
  exit 0
fi

# The fixture SQL inserts explicit ids without advancing the sequences, so the
# app's next insert collides ("duplicate key ... Key (id)=(1)"). Sets every
# column-owned sequence (serial and identity) in database $2 to at least the
# column's max, as table owner $1. Idempotent.
reset_sequences() {
  psql -U "$1" -d "$2" -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT sn.nspname AS seq_schema, s.relname AS seq, tn.nspname AS tbl_schema, t.relname AS tbl, a.attname AS col
    FROM pg_class s
    JOIN pg_namespace sn ON sn.oid = s.relnamespace
    JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass
      AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
    JOIN pg_class t ON t.oid = d.refobjid
    JOIN pg_namespace tn ON tn.oid = t.relnamespace
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
    WHERE s.relkind = 'S'
  LOOP
    EXECUTE format(
      'SELECT setval(%L, GREATEST((SELECT COALESCE(max(%I), 0) FROM %I.%I), (SELECT last_value FROM %I.%I)))',
      r.seq_schema || '.' || r.seq, r.col, r.tbl_schema, r.tbl, r.seq_schema, r.seq);
  END LOOP;
END $$;
SQL
  echo "[$2] Sequences reset to their tables' max id."
}

seed_openzaak() {
  echo ">>>> [openzaak] Waiting until Open Zaak has initialized the database <<<<"
  while true; do
    verifier=$(psql -U openzaak -d openzaak -t -A -c "select count(id) from accounts_user where username = 'admin'")
    if [ "1" = "$verifier" ]; then
      echo "[openzaak] Running database setup scripts ..."
      for file in "$FIXTURES_DIR"/openzaak/*.sql; do
        echo "[openzaak] Running $file ..."
        psql -U openzaak openzaak \
          -v BAG_API_CLIENT_MP_REST_URL="${BAG_API_CLIENT_MP_REST_URL}" \
          -v BAG_API_KEY="${BAG_API_KEY}" \
          -f "$file"
      done
      reset_sequences openzaak openzaak
      break
    else
      echo "[openzaak] Open Zaak is not running yet"
      sleep 5
    fi
  done
  echo ">>>> [openzaak] Data import script finished <<<<"
}

seed_openklant() {
  # The number of expected records in the django_migrations table after Open
  # Klant has finished its database migrations. Update this if a future Open
  # Klant version changes its migration count (copied verbatim from the
  # source fill-data-on-startup.sh comment).
  local expected_migrations=176
  echo ">>>> [openklant] Waiting until Open Klant has initialized the database <<<<"
  while true; do
    verifier=$(psql -U openklant -d openklant -t -A -c "select count(*) from django_migrations")
    if [ "$expected_migrations" != "$verifier" ]; then
      echo "[openklant] Open Klant not running yet. Sleeping 2 seconds ..."
      sleep 2
    else
      echo "[openklant] Open Klant is running!"
      break
    fi
  done
  echo "[openklant] Running database setup scripts ..."
  for file in "$FIXTURES_DIR"/openklant/*.sql; do
    echo "[openklant] Running $file ..."
    psql -U openklant openklant -f "$file"
  done
  reset_sequences openklant openklant
  echo ">>>> [openklant] Database was initialized successfully <<<<"
}

seed_openzaak &
seed_openklant &
