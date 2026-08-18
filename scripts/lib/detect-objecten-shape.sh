#!/usr/bin/env bash
# Sourced (not executed) by deploy.sh and provision-cluster.sh, after
# `helm dependency update` has already populated charts/podiumd-*.tgz.
# Requires CHART_DIR to already be set by the caller.
#
# Some podiumd versions still ship objecten/objecttypen as two separate
# subcharts; newer ones merge both into a single "openobject" chart
# (aliased back to the values key "objecten" - see
# set-podiumd-version.sh's own --path header for the full background).
# The two shapes need different --set flags to actually deploy correctly:
#   - classic: objecttypen is its own subchart, gated by
#     podiumd.objecttypen.enabled (values.yaml's own objecttypen block
#     already carries the rest, including disabling
#     create_required_objecttypen_job).
#   - merged: there is no objecttypen subchart to enable. values.yaml's
#     objecten.image.tag override (pinned to the old objects-api version,
#     "3.6.1") does not exist as a tag on the new maykinmedia/open-object
#     image - nulling it out here falls back to whatever tag/digest the
#     currently-selected podiumd version's openobject chart itself
#     defaults to (confirmed live: this is the intended way to fall back
#     to a subchart default from a parent override, not a workaround).
#     create_required_objecttypen_job's disable-flag also moves from
#     podiumd.objecttypen.* (gone) to podiumd.objecten.* (confirmed live
#     against the merged chart's own create-required-objecttypen.yaml
#     template, which gates on .Values.objecten.create_required_objecttypen_job.enabled).
#     Also sets our own objecten.merged (not podiumd.*) - values.yaml's
#     own objecten.configuration.data has no service_identifier field for
#     merged's objecttypes.items (dropped upstream, Objects/Objecttypes
#     merged into one API - confirmed against that version's own
#     setup_configuration/models/objecttypes.py) - and
#     templates/objecten/service-objecttypen-alias.yaml uses it to only
#     stand up its DNS-CNAME Service (aliasing the dropped "objecttypen"
#     hostname onto "objecten") on the shape that actually needs it. That
#     DNS alias alone isn't enough, though - confirmed live, a request for
#     http://objecttypen.podiumd-minikube/... against the merged app still
#     404/400s (Django's own DisallowedHost check, ALLOWED_HOSTS only ever
#     has "objecten"'s own names in it) - so this also overrides
#     podiumd.objecten.settings.allowedHosts to add "objecttypen" and
#     "objecttypen.podiumd-minikube" to whatever values.yaml's own
#     objecten.settings.allowedHosts already has, merged-only.
#
# Detected by checking which subchart directory actually made it into the
# vendored podiumd tarball - not by guessing from a version number, since
# this merge may land in an unpublished/locally-pathed podiumd checkout
# (see set-podiumd-version.sh --path) with no meaningful version to key off.
#
# Also exports OBJECTEN_MERGED (true/false) - a plain boolean other
# scripts can branch on directly (see scripts/lib/seed-fixtures.sh) without
# re-deriving it or parsing OBJECTEN_SHAPE_SETS themselves.
PODIUMD_TGZ="$(ls "${CHART_DIR}"/charts/podiumd-*.tgz 2>/dev/null | head -1)"
# Deliberately not `tar ... | grep -q ...`: under `set -o pipefail` (as in
# every caller of this script), `grep -q` exits on its first match, SIGPIPEs
# `tar` before it finishes writing, and pipefail then reports the whole
# pipeline as failed even though grep *did* find the match - silently
# taking the wrong (classic) branch below. Confirmed live: this exact
# pipeline gives the right answer standalone but the wrong one under
# pipefail. Capturing tar's output first avoids the early-exit SIGPIPE
# entirely.
PODIUMD_TGZ_LIST="$([[ -n "${PODIUMD_TGZ}" ]] && tar -tzf "${PODIUMD_TGZ}" || true)"
if grep -q "^podiumd/charts/openobject/" <<< "${PODIUMD_TGZ_LIST}"; then
  OBJECTEN_MERGED=true
  OBJECTEN_SHAPE_SETS=(
    --set podiumd.objecten.image.tag=null
    --set podiumd.objecten.create_required_objecttypen_job.enabled=false
    --set objecten.merged=true
    --set 'podiumd.objecten.settings.allowedHosts=objecten.local\,objecttypen\,objecttypen.podiumd-minikube'
  )
else
  OBJECTEN_MERGED=false
  OBJECTEN_SHAPE_SETS=(--set podiumd.objecttypen.enabled=true --set objecten.merged=false)
fi
