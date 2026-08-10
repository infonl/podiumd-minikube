#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). Pulls any
ConfigMap too big for plain `kubectl apply` out of the main manifest stream
and writes it to LARGE_CONFIGMAPS_OUT instead, unchanged otherwise.

Why this needs to exist: `kubectl apply` stores the entire object as a
`kubectl.kubernetes.io/last-applied-configuration` annotation for its
client-side 3-way merge diffing, and that annotation itself is capped at
262144 bytes - confirmed live: the monitoring-logging dependency's own
bundled Grafana dashboards (templates/metrics-dashboards.yaml, packing
9+ full dashboard JSON exports into one ConfigMap) blow past that on their
own, well under Kubernetes' own much higher per-object size limit, so the
object itself is perfectly valid - only kubectl's own client-side apply
annotation rejects it ("metadata.annotations: Too long: may not be more
than 262144 bytes"). `kubectl apply --server-side` doesn't use that
annotation at all, so deploy.sh applies whatever lands here that way,
separately - see its own comment for why that's not just the *default*
apply mode for everything else too.
"""
import os
import sys

import yaml

SIZE_LIMIT = 200_000  # headroom under kubectl's 262144-byte annotation cap
LARGE_OUT = os.environ.get("LARGE_CONFIGMAPS_OUT")

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
small, large = [], []
for doc in docs:
    is_large_configmap = doc.get("kind") == "ConfigMap" and len(yaml.safe_dump(doc)) > SIZE_LIMIT
    (large if is_large_configmap else small).append(doc)

if large:
    if not LARGE_OUT:
        sys.exit(
            f"split-large-configmaps.py: {len(large)} ConfigMap(s) exceed {SIZE_LIMIT} bytes "
            "but LARGE_CONFIGMAPS_OUT isn't set - refusing to silently drop them."
        )
    with open(LARGE_OUT, "w") as f:
        yaml.safe_dump_all(large, f, default_flow_style=False, sort_keys=False)

yaml.safe_dump_all(small, sys.stdout, default_flow_style=False, sort_keys=False)
