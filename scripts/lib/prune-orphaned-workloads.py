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

Prometheus/PrometheusRule/ServiceMonitor/PodMonitor are in PRUNABLE_KINDS
for exactly the same reason a Deployment would be, not because they're
special - confirmed live: disabling monitoringLogging.enabled dropped
those CRs out of the render as expected, but left them (and, since nothing
else owned them, the whole StatefulSet-per-Prometheus-CR chain above) live
in the namespace - `kubectl apply` never deletes what drops out, same gap
as the Deployment case this script already existed for. Each of these
kinds is looked up defensively: their CRDs are only ever applied once
monitoringLogging has been enabled at least once (see
apply-monitoring-logging-crds.sh), and are deliberately never removed
after (see reset-namespace.sh's own header) - but a setup that has *never*
enabled it doesn't have them installed at all, and `kubectl get` on a
truly unknown kind is a hard error, not an empty list.
"""
import json
import subprocess
import sys

import yaml

PRUNABLE_KINDS = (
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "Prometheus",
    "PrometheusRule",
    "ServiceMonitor",
    "PodMonitor",
)

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
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        if "the server doesn't have a resource type" in result.stderr:
            continue
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
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
    print("No orphaned workload(s)/monitoring CR(s) found - nothing to prune.")
