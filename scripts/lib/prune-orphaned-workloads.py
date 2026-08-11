#!/usr/bin/env python3
"""
Deletes Deployments/StatefulSets/DaemonSets/Services/Secrets/Ingresses left
running in the namespace from an earlier deploy.sh run with a different
set of profile/--set flags, but no longer present in the current render
at all.

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

Any live object with an ownerReference is skipped the same way,
generalizing that same reasoning: confirmed live that
monitoring-logging's kube-prometheus-stack dependency has its own operator
create `prometheus-<release>-kube-prom-prometheus` (a StatefulSet) from a
`Prometheus` custom resource at runtime - only that CR itself is ever part
of the render, never the StatefulSet it spawns - so without this check
that StatefulSet would look "orphaned" and get deleted (then immediately
recreated by the operator) on every single deploy.sh run.

Deliberately checks for *any* ownerReference, not just one with
`controller: true` (an earlier version of this check did, before Service
was added to PRUNABLE_KINDS) - confirmed live that the same operator's own
`prometheus-operated` Service (the headless Service it creates for peer
discovery, owned by the same `Prometheus` CR) sets an ownerReference
*without* `controller: true` on it, which the stricter check would have
missed entirely. Caught by LARGE_PRUNE_THRESHOLD below before it could
actually delete anything - that safety net exists for exactly this class
of "the code looked right but wasn't" case, not only the operator-error
case it was originally written for.

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

Service/Secret/Ingress added for the exact same "two Grafanas at once"
scenario, found live the hard way: toggling monitoringLogging.enabled off
after having it on left the *raw-templates* Grafana's own `grafana.local`
Ingress competing with monitoring-logging's orphaned
`<release>-grafana.local` Ingress (its backing Service - and Deployment,
already pruned above - long gone, zero endpoints) - Traefik load-balanced
between the two, so roughly half of all requests to grafana.local got a
real 503 from the dead one. Not just Grafana's own objects either -
monitoring-logging's entire kube-prometheus-stack/loki/alloy footprint
(Services, Secrets, ConfigMaps) was left behind the same way; only the
ConfigMaps stay unpruned (see below).

ConfigMap is deliberately NOT in PRUNABLE_KINDS, unlike the other object
kinds above: split-large-configmaps.py (run as part of deploy.sh's own
render() pipeline, same one whose output feeds this script) pulls any
ConfigMap over the kubectl-apply annotation size limit out of the main
manifest stream entirely and applies it separately via
`kubectl apply --server-side` - see that script's own header. That means
a large ConfigMap (monitoring-logging's own bundled Grafana dashboards,
the concrete case that script exists for) is genuinely live and desired,
but would never appear in the manifest this script reads from stdin -
adding ConfigMap here would make this script delete it immediately after
deploy.sh just applied it, every single run. Fixing that would mean
teaching this script to also read split-large-configmaps.py's own output
file, not just stdin - not done here since no orphaned ConfigMap has ever
actually broken anything the way the orphaned Ingress above did (it's
inert clutter, not a routing conflict).

LARGE_PRUNE_THRESHOLD refuses to actually prune (just prints what it
would have) when the to-delete count is suspiciously high, without
--force - added after confirming live exactly how easy this is to trigger
by accident: running plain `./scripts/deploy.sh` (core profile only)
against a cluster that was actually running `--full` deleted the entire
optional-profile stack - every objecten/objecttypen/opennotificaties/
openarchiefbeheer/openformulieren Deployment plus their Services/Secrets/
Ingresses, and the raw-templates metrics stack - in one run, simply
because `--full` was left off this particular invocation. A genuine,
deliberate single-profile toggle prunes a handful of resources; a mismatch
between "what's live" and "what this invocation's flags describe" prunes
dozens. `--force` (forwarded from deploy.sh's own `--force-prune`) skips
this check for the rare case a prune that large is really intended.
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
    "Service",
    "Secret",
    "Ingress",
)

LARGE_PRUNE_THRESHOLD = 10

namespace = sys.argv[1]
force = "--force" in sys.argv[2:]

docs = [doc for doc in yaml.safe_load_all(sys.stdin) if doc]
desired = {
    (doc["kind"], doc["metadata"]["name"])
    for doc in docs
    if doc.get("kind") in PRUNABLE_KINDS
}

to_delete = []
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
        if item["metadata"].get("ownerReferences"):
            continue
        to_delete.append((kind, name))

if not to_delete:
    print("No orphaned workload(s)/monitoring CR(s) found - nothing to prune.")
    sys.exit(0)

if len(to_delete) > LARGE_PRUNE_THRESHOLD and not force:
    print(
        f"REFUSING to prune: {len(to_delete)} resource(s) would be deleted, "
        f"over the safety threshold of {LARGE_PRUNE_THRESHOLD}.",
        file=sys.stderr,
    )
    print(
        "This usually means deploy.sh was run with fewer profile flags than "
        "the cluster is actually running (e.g. plain './scripts/deploy.sh' "
        "after a '--full' deploy), not a genuine, deliberate scale-down.",
        file=sys.stderr,
    )
    print("Would prune:", file=sys.stderr)
    for kind, name in to_delete:
        print(f"  {kind}/{name}", file=sys.stderr)
    print(
        "\nIf this is really what you want deployed, re-run deploy.sh with "
        "the same flags plus --force-prune.",
        file=sys.stderr,
    )
    sys.exit(1)

for kind, name in to_delete:
    subprocess.run(["kubectl", "delete", kind, name, "-n", namespace], check=True)

print(
    "Pruned orphaned workload(s) not part of the current render: "
    + ", ".join(f"{k}/{n}" for k, n in to_delete)
)
