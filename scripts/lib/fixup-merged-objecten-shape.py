#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). No-op
unless OBJECTEN_MERGED=true in the environment (set by deploy.sh from
scripts/lib/detect-objecten-shape.sh's own detection - see its header for
the two objecten/objecttypen shapes this chart supports). Classic shape
runs a real, separate Objecttypen API with its own token store; merged
shape (open-object >=4.0.0) folded it into Objects entirely - one
values.yaml block can't satisfy both shapes' schemas/data at once, and
values.yaml itself is never templated by Helm (only templates/ is), so
every merged-only fixup lives here instead, patching the already-rendered
manifests post-render, the same way strip-image-digests.py and friends
already patch other cross-cutting concerns after the fact.

Two independent fixups so far, both found live deploying against a real
merged-shape podiumd checkout for the first time (see plan.md):

1. objecten-configuration ConfigMap: values.yaml's
   objecten.configuration.data has to carry a `service_identifier` under
   objecttypes.items for classic shape (its ObjectTypesConfigurationStep
   requires it - a real, separate Objecttypes API to reference by
   service). Merged shape's own step model rejects that same field
   outright (Pydantic extra_forbidden) instead of ignoring it - confirmed
   against that version's own
   setup_configuration/models/objecttypes.py - so it's stripped back out
   here.
2. openformulieren-configuration ConfigMap: values.yaml's
   podiumd.openformulieren.configuration.data registers an
   "objecttypes-api" zgw_consumers.Service entry authenticating with
   `openFormulierenToObjecttypenToken` - a token that only ever existed
   in the classic shape's separate objecttypen app's own token table
   (values.yaml's podiumd.objecttypen.configuration.data, itself only
   rendered on classic shape). Merged shape has no such table at all -
   Objects' own token table only ever gets
   `fakeOpenFormulierenObjectsToken` (values.yaml's
   podiumd.objecten.configuration.data tokenauth block) - so that
   Service's api_root now resolves (via
   templates/objecten/service-objecttypen-alias.yaml's DNS alias) but
   every request against it 401s. Rewritten here to the token that
   actually exists on merged shape - confirmed live (it's the same
   is_superuser=true token already granted full access, no separate
   objecttypes-scoped token needed once Objects and Objecttypes are the
   same app).
"""
import os
import sys

import yaml

if os.environ.get("OBJECTEN_MERGED") != "true":
    sys.stdout.write(sys.stdin.read())
    sys.exit(0)

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
for doc in docs:
    if doc.get("kind") != "ConfigMap":
        continue
    name = doc.get("metadata", {}).get("name")
    raw = doc.get("data", {}).get("configuration.yaml")
    if name == "objecten-configuration" and raw:
        inner = yaml.safe_load(raw)
        for item in inner.get("objecttypes", {}).get("items", []):
            item.pop("service_identifier", None)
        doc["data"]["configuration.yaml"] = yaml.safe_dump(inner, sort_keys=False)
    elif name == "openformulieren-configuration" and raw:
        inner = yaml.safe_load(raw)
        for service in inner.get("zgw_consumers", {}).get("services", []):
            if service.get("identifier") == "objecttypes-api":
                service["header_value"] = "Token fakeOpenFormulierenObjectsToken"
        doc["data"]["configuration.yaml"] = yaml.safe_dump(inner, sort_keys=False)

print(yaml.dump_all(docs, sort_keys=False))
