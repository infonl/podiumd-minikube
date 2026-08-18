#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). Patches
the "keycloak-realm" ConfigMap's own zaakafhandelcomponent-realm.json -
specifically the "zaakafhandelcomponent" client's pkce.code.challenge.method
attribute - to match ZAC_EXPERIMENTAL_PKCE in the environment (set by
deploy.sh from scripts/lib/zac-experimental-pkce.sh's own detection - see
its header for why this whole thing is gated at all).

The vendored realm.json itself carries "S256" (the PKCE-enabled end
state) unconditionally - values.yaml can't branch a vendored file's
content on a flag (values.yaml is never templated by Helm, only
templates/ is), so this strips it back to "" (PKCE not required,
matching the chart-default zac that never sends it) whenever
zac.experimentalPkce is false, the same "checked-in file represents one
shape, a post-renderer patches it back for the other" pattern already
used for values.yaml's own objecten.configuration.data (see
fixup-merged-objecten-shape.py).

Only fixes what a fresh `--import-realm` would pick up - Keycloak's own
persistent (Postgres-backed, not ephemeral) realm storage means an
already-provisioned cluster's *live* realm won't see this at all until
scripts/lib/sync-zac-pkce-realm.sh also reconciles it there, same as this
script's own ConfigMap patch alone was never going to be enough (confirmed
live - see that script's own header).
"""
import os
import sys

import yaml

PKCE_ENABLED = os.environ.get("ZAC_EXPERIMENTAL_PKCE") == "true"

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
for doc in docs:
    if doc.get("kind") != "ConfigMap" or doc.get("metadata", {}).get("name") != "keycloak-realm":
        continue
    for key, raw in (doc.get("data") or {}).items():
        if not key.endswith("zaakafhandelcomponent-realm.json") or not raw:
            continue
        import json

        realm = json.loads(raw)
        for client in realm.get("clients", []):
            if client.get("clientId") == "zaakafhandelcomponent":
                client["attributes"]["pkce.code.challenge.method"] = "S256" if PKCE_ENABLED else ""
        doc["data"][key] = json.dumps(realm, indent=2)

print(yaml.dump_all(docs, sort_keys=False))
