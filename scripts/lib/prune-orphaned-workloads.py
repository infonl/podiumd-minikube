#!/usr/bin/env python3
"""
Deletes Deployments/StatefulSets/DaemonSets left running in the namespace
from an earlier deploy.sh run with a different set of profile/--set flags,
but no longer present in the current render at all.

Why this needs to exist: deploy.sh applies via plain `kubectl apply`, never
`helm upgrade` (see deploy.sh's own header for why) - which only ever
adds/updates resources present in the current render, never deletes ones
that dropped out of it (unlike `helm upgrade`, which diffs against the
previous release). See values.yaml's own monitoringLogging comment for the
concrete case this was written for: flipping that flag leaves the *other*
implementation's Grafana/Tempo/otel-collector Deployments running right
alongside the new ones ("two Grafanas at once") until someone notices and
cleans them up by hand. Toggling any other profile off (objecten,
opennotificaties, ...) has the same problem for whatever Deployments that
profile owns.

Reads the exact same rendered manifest deploy.sh applies (stdin) for the
namespace given as argv[1]. Deleting a Deployment/StatefulSet/DaemonSet
cascades to its own ReplicaSets/Pods via normal Kubernetes garbage
collection - that's what actually "stops the pods".

Job/CronJob are deliberately left out of PRUNABLE_KINDS: pabc-migrations is
excluded from this render entirely (see exclude-pabc-migration-job.py) and
would look "orphaned" on every single run if included here, deleting the
very Job apply-pabc-migrations.sh's own guard exists to protect;
storage-permissions-fix is already unconditionally deleted/recreated
earlier in deploy.sh; and CronJob-spawned Job instances (zac-sig-del,
zac-signaleren) are never themselves part of any render - only the owning
CronJob is - so diffing them here would delete every run's history instead
of leaving that to their own successfulJobsHistoryLimit/
failedJobsHistoryLimit.

Any live object with a controller ownerReference is skipped the same way,
generalizing that same reasoning: confirmed live that
monitoring-logging's kube-prometheus-stack dependency has its own operator
create `prometheus-<release>-kube-prom-prometheus` (a StatefulSet) from a
`Prometheus` custom resource at runtime - only that CR itself is ever part
of the render, never the StatefulSet it spawns - so without this check
that StatefulSet would look "orphaned" and get deleted (then immediately
recreated by the operator) on every single deploy.sh run.
"""
import json
import subprocess
import sys

import yaml

PRUNABLE_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

namespace = sys.argv[1]

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
desired = {
    (doc["kind"], doc["metadata"]["name"])
    for doc in docs
    if doc.get("kind") in PRUNABLE_KINDS
}

pruned = []
for kind in PRUNABLE_KINDS:
    result = subprocess.run(
        ["kubectl", "get", kind, "-n", namespace, "-o", "json"],
        capture_output=True, text=True, check=True,
    )
    for item in json.loads(result.stdout)["items"]:
        name = item["metadata"]["name"]
        if (kind, name) in desired:
            continue
        owners = item["metadata"].get("ownerReferences") or []
        if any(owner.get("controller") for owner in owners):
            continue
        subprocess.run(["kubectl", "delete", kind, name, "-n", namespace], check=True)
        pruned.append(f"{kind}/{name}")

if pruned:
    print("Pruned orphaned workload(s) not part of the current render: " + ", ".join(pruned))
else:
    print("No orphaned Deployment/StatefulSet/DaemonSet found - nothing to prune.")
