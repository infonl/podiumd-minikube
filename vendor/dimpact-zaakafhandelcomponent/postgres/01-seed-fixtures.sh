#!/bin/bash
# Merged replacement for the three per-service init.sh/fill-data-on-startup.sh
# pairs in docker-compose.yaml's openzaak-database/openklant-database/
# openarchiefbeheer-database, consolidated onto this one shared Postgres
# instance. Each block below is unchanged in *behavior* from its source
# script - same readiness query, same fixture SQL files - only parameterized
# by database/user instead of assuming the container's single default
# database, and merged into one file since this instance only runs
# docker-entrypoint-initdb.d once for the whole cluster.
#
# Each block backgrounds its own wait loop (matching the original scripts'
# own `&`) so all three seed independently and in parallel once their
# respective app has finished migrating.

FIXTURES_DIR="$(dirname "$0")/fixtures"

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
  echo ">>>> [openklant] Database was initialized successfully <<<<"
}

seed_openarchiefbeheer() {
  # Same caveat as openklant's expected_migrations above.
  local expected_migrations=154
  echo ">>>> [openarchiefbeheer] Waiting until Open Archiefbeheer has initialized the database <<<<"
  while true; do
    verifier=$(psql -U openarchiefbeheer -d openarchiefbeheer -t -A -c "select count(*) from django_migrations")
    echo "[openarchiefbeheer] Migrations found: $verifier"
    if [ "$expected_migrations" != "$verifier" ]; then
      echo "[openarchiefbeheer] Open Archiefbeheer not running yet. Sleeping 2 seconds ..."
      sleep 2
    else
      echo "[openarchiefbeheer] Open Archiefbeheer is running!"
      break
    fi
  done
  echo "[openarchiefbeheer] Running database setup scripts ..."
  for file in "$FIXTURES_DIR"/openarchiefbeheer/*.sql; do
    echo "[openarchiefbeheer] Running $file ..."
    psql -U openarchiefbeheer openarchiefbeheer -f "$file"
  done
  echo ">>>> [openarchiefbeheer] Database was initialized successfully <<<<"
}

seed_openzaak &
seed_openklant &
seed_openarchiefbeheer &
