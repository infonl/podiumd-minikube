#!/usr/bin/env bash
# Seeds demo/fixture data for chart components that docker-compose seeds
# via a one-shot "*-import" container running `manage.py loaddata` inside
# the app itself, rather than a Postgres-side init script (those are
# already baked into postgres/00-create-databases.sql +
# 01-seed-fixtures.sh instead, run automatically at Postgres startup - no
# separate script needed for those).
#
# Checked every scripts/docker-compose/imports/*/init.sh in
# dimpact-zaakafhandelcomponent for this specific "loaddata inside the
# running app" pattern: objects-api/objecttypes-api (pre-merge) and
# open-object (post-merge) are the *only* hits - every other seeded
# component there is either a Postgres init script (already handled) or
# django-setup-configuration YAML data (a declarative mechanism already
# wired through this chart's own values.yaml). So this script currently
# only covers objecten/objecttypen/openobject - add another `seed` call
# below if a future component needs the same "loaddata after the pod is
# up" treatment.
#
# Unlike deploy.sh's rendered manifest, loading fixture data has to run
# *after* the target pod exists and has migrated - not something a
# `kubectl apply` can express - so this is a separate script, called
# automatically at the end of deploy.sh whenever the 'objecten' Deployment
# is actually part of this render (see that script's own comment on this),
# but also safe/useful to run manually any time.
#
# Safe to re-run - genuinely idempotent, not just non-destructive: each
# seed() call checks whether its target model already has any rows before
# doing anything else, and skips straight past the wait/copy/loaddata work
# entirely if so ("if not done before" - the actual behavior deploy.sh's
# own automatic call relies on, not just a side-effect of `loaddata`
# upserting by PK). The superuser-creation step is separately guarded by
# its own existence check, same as before.
#
# Supports both podiumd shapes (see scripts/lib/detect-objecten-shape.sh):
#   - classic: objecten and objecttypen are two separate subcharts with
#     two separate databases, mirroring dimpact-zaakafhandelcomponent's
#     docker-compose before its Open Object 4.0 upgrade (commit a98d5ae2b)
#     - seeds both, each from its own app's own fixture.
#   - merged (openobject): one subchart/database serves both APIs,
#     mirroring docker-compose today - seeds the single combined fixture
#     into the same "objecten" Deployment name either way (see H.1 in
#     that migration doc for why the name stays "objecten").
#
# Usage: ./scripts/lib/seed-fixtures.sh
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="podiumd-minikube"
VENDOR_DIR="${CHART_DIR}/vendor/dimpact-zaakafhandelcomponent"

source "${CHART_DIR}/scripts/lib/require-minikube-context.sh"
source "${CHART_DIR}/scripts/lib/detect-objecten-shape.sh"

# Known, confirmed-upstream defect (see plan.md): the classic `objecten`
# target (maykinmedia/objects-api) still declares a `service` ForeignKey
# and `_name` field on its ObjectType model, but the migrations actually
# shipped never create those columns - so *any* write to ObjectType fails,
# fixture or not. Reproduced independently of any fixture data (a bare
# `ObjectType(...).save()` in the Django shell fails identically) and
# confirmed present in both 3.6.1 and 3.6.2 - not something a different
# fixture, or patching this one, can fix. Matched by output rather than
# skipped unconditionally, so this stops warning on its own the moment a
# future image version actually fixes it upstream.
KNOWN_OBJECTEN_BUG_SIGNATURE='column "service_id" of relation "core_objecttype" does not exist'

seed() {
  local deployment="$1" fixture="$2" app_label="$3" model_name="$4"
  kubectl wait --for=condition=available "deployment/${deployment}" -n "${NAMESPACE}" --timeout=180s
  local pod
  pod="$(kubectl get pod -n "${NAMESPACE}" -l "app.kubernetes.io/name=${deployment}" -o jsonpath='{.items[0].metadata.name}')"

  # "Not done before" check: query the fixture's own primary model
  # directly (via Django's app registry, not a hardcoded table/db name -
  # works unchanged whether this is the classic shape's own database or
  # the merged openobject one) rather than assuming loaddata's own exit
  # code means anything about pre-existing data (it always reports
  # "Installed N object(s)" and exits 0 whether the rows were new or
  # upserted over identical existing ones).
  local already_seeded
  already_seeded="$(kubectl exec -n "${NAMESPACE}" "${pod}" -- python /app/src/manage.py shell -c "
from django.apps import apps
Model = apps.get_model('${app_label}', '${model_name}')
print('yes' if Model.objects.exists() else 'no')
" 2>/dev/null | tail -1)"
  if [ "${already_seeded}" = "yes" ]; then
    echo "'${deployment}' already has ${app_label}.${model_name} data - skipping (not re-seeding)."
    return 0
  fi

  echo "Seeding '${deployment}' from ${fixture}..."
  kubectl cp "${fixture}" "${NAMESPACE}/${pod}:/tmp/demodata.json"

  local error_log
  error_log="$(mktemp)"
  if ! kubectl exec -n "${NAMESPACE}" "${pod}" -- python /app/src/manage.py loaddata /tmp/demodata.json 2>"${error_log}"; then
    if grep -qF "${KNOWN_OBJECTEN_BUG_SIGNATURE}" "${error_log}"; then
      echo >&2
      echo "WARNING: seeding '${deployment}' failed - confirmed upstream bug in" >&2
      echo "maykinmedia/objects-api, not a problem with this project's fixture" >&2
      echo "or this script: its ObjectType model still declares a 'service'" >&2
      echo "field that its own shipped migrations never create a column for," >&2
      echo "so any write to ObjectType fails (reproduced with a bare" >&2
      echo "ObjectType(...).save(), no fixture involved; confirmed present in" >&2
      echo "both 3.6.1 and 3.6.2). Nothing to fix on this side - see plan.md." >&2
      rm -f "${error_log}"
      exit 1
    fi
    cat "${error_log}" >&2
    rm -f "${error_log}"
    exit 1
  fi
  rm -f "${error_log}"

  kubectl exec -n "${NAMESPACE}" "${pod}" -- python /app/src/manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@example.com', 'admin')
"
}

if [ "${OBJECTEN_MERGED}" = true ]; then
  seed objecten "${VENDOR_DIR}/openobject/demodata.json" core Object
else
  seed objecten "${VENDOR_DIR}/objecten/demodata.json" core Object
  seed objecttypen "${VENDOR_DIR}/objecttypen/demodata.json" core ObjectType
fi

echo
echo "Done."
