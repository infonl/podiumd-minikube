#!/usr/bin/env bash
# Sourced (not executed) by deploy.sh and provision-cluster.sh, after
# `helm dependency update` has already populated charts/podiumd-*.tgz.
# Requires CHART_DIR to already be set by the caller.
#
# Reads top-level zac.experimentalPkce from values.yaml (off by default -
# see that key's own comment) and, if on, verifies the currently-selected
# podiumd version's own zac chart actually supports AUTH_ENABLE_PKCE
# before doing anything else - refusing deploy.sh with a clear message
# otherwise. This isn't a hypothetical guard: this exact experiment
# already caused a real incident once (see NOTES.md's PKCE entry) by
# having the vendored realm *require* PKCE while the then-deployed zac
# chart had no way to send it, rejecting every single login. Detected the
# same way scripts/lib/detect-objecten-shape.sh detects its own shape -
# by inspecting the actual vendored podiumd tarball's contents, not a
# version-number guess, since this experiment only ever runs against an
# unpublished/hand-bumped local `--path` checkout (see
# values.yaml's own podiumd.zac.image.tag comment for the exact recipe)
# with no meaningful podiumd version to key off.
#
# Exports ZAC_EXPERIMENTAL_PKCE (true/false) - read by
# scripts/lib/fixup-zac-pkce-realm.py (post-render fixup for the vendored
# realm's own PKCE requirement) and scripts/lib/sync-zac-pkce-realm.sh
# (reconciles that same requirement into Keycloak's already-imported live
# realm, since --import-realm only ever runs once - see that script's own
# header). Also produces ZAC_PKCE_SETS, an array of extra --set flags for
# deploy.sh/provision-cluster.sh's own helm template/dependency-listing
# calls - only the image.tag override so far, empty when off (chart
# default applies).
VALUES_YAML="${CHART_DIR}/values.yaml"

zac_experimental_pkce_enabled() {
  awk '
    /^zac:$/ { in_block = 1; next }
    in_block && /^[^[:space:]#]/ { in_block = 0 }
    in_block && /^[[:space:]]+experimentalPkce:[[:space:]]*true/ { found = 1 }
    END { exit !found }
  ' "${VALUES_YAML}"
}

if zac_experimental_pkce_enabled; then
  ZAC_EXPERIMENTAL_PKCE=true

  PODIUMD_TGZ="$(ls "${CHART_DIR}"/charts/podiumd-*.tgz 2>/dev/null | head -1)"
  ZAC_CONFIG_TEMPLATE="$(tar -xzOf "${PODIUMD_TGZ}" podiumd/charts/zaakafhandelcomponent/templates/config.yaml 2>/dev/null || true)"
  if ! grep -q "AUTH_ENABLE_PKCE" <<< "${ZAC_CONFIG_TEMPLATE}"; then
    echo "ERROR: zac.experimentalPkce is true, but the currently-selected podiumd" >&2
    echo "version's own zac chart has no AUTH_ENABLE_PKCE support in its" >&2
    echo "templates/config.yaml at all - deploying like this would leave the" >&2
    echo "vendored realm requiring PKCE (see scripts/lib/fixup-zac-pkce-realm.py)" >&2
    echo "while zac itself never sends it, rejecting every single login - the" >&2
    echo "exact incident NOTES.md's PKCE entry already documents happening once." >&2
    echo >&2
    echo "Bump the zac chart dependency to (at least) 1.0.289 in whichever local" >&2
    echo "--path checkout .podiumd-versions.yaml currently points at first - see" >&2
    echo "values.yaml's own podiumd.zac.image.tag comment for the exact recipe -" >&2
    echo "then re-run this. Or set zac.experimentalPkce back to false." >&2
    exit 1
  fi

  ZAC_PKCE_SETS=(--set podiumd.zac.image.tag=5.4.2)
else
  ZAC_EXPERIMENTAL_PKCE=false
  ZAC_PKCE_SETS=()
fi
export ZAC_EXPERIMENTAL_PKCE
