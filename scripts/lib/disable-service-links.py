#!/usr/bin/env python3
"""
Helm post-renderer (see `helm template/install --post-renderer`). Sets
`enableServiceLinks: false` on every pod spec in the rendered manifest -
Deployments, StatefulSets, DaemonSets, and CronJobs - regardless of which
chart the object came from.

Why this needs to exist: Kubernetes' legacy "service links" feature
auto-injects a `<SERVICE_NAME>_PORT`-style env var for every Service in the
namespace into every pod. Our own raw templates (postgres, redis, keycloak,
solr, wiremock) already set `enableServiceLinks: false` directly, added
after Solr crashed live parsing the injected `SOLR_PORT=tcp://10.x.x.x:8983`
as its own numeric config value of the same name. Confirmed live a second
time on a podiumd-nested subchart we don't control the template for:
opennotificaties-worker's own wait-for-rabbitmq entrypoint script reads
`RABBITMQ_PORT` expecting a bare port number, gets the injected
`tcp://10.96.128.255:5672` instead, and never actually starts the celery
worker - stuck retrying forever, only visible as a recurring liveness-probe
kill/restart cycle every ~8 minutes (matching the probe's own
failureThreshold * periodSeconds window), not as an obvious crash.

Since none of the podiumd-nested charts expose `enableServiceLinks` as a
values.yaml field (checked directly - it isn't referenced in any of their
templates at all, so there's no override point to fix this one subchart at
a time even if we wanted to), and since ANY app in this chart could
plausibly hit the identical collision for some *other* auto-injected
variable name we haven't found yet, this disables it universally as a
post-renderer instead of chasing individual instances.

Deliberately excludes bare `kind: Job` (unlike CronJob, whose own object
updates freely - only the Jobs it later spawns are immutable): a Job's own
`spec.template` is immutable once created, so patching it here would make
`kubectl apply` fail outright on any already-existing Job whose rendered
spec doesn't yet have this field - including `pabc-migrations-1`, which
`scripts/apply-pabc-migrations.sh` deliberately protects from being
casually deleted and recreated (see that script for why: this Job clears
PABC's database before reseeding it, every time it runs). None of this
chart's own one-shot Jobs (`storage-permissions-fix`, `opa-tests`,
`pabc-migrations-1`) run a long-lived process with a "wait for X"
entrypoint script of the kind that actually collides with an injected
env var, so there's no functional need to patch them anyway.
"""
import sys

import yaml

WORKLOAD_KINDS_TEMPLATE_PATH = {
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]

for doc in docs:
    path = WORKLOAD_KINDS_TEMPLATE_PATH.get(doc.get("kind"))
    if not path:
        continue
    node = doc
    for key in path:
        node = node.get(key)
        if node is None:
            break
    if isinstance(node, dict):
        node["enableServiceLinks"] = False

yaml.safe_dump_all(docs, sys.stdout, default_flow_style=False, sort_keys=False)
