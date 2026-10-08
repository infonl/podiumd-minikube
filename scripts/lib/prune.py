"""Deletes resources that an earlier deploy created but the current render no longer contains.

`kubectl apply` never deletes what drops out of a render; toggling a profile
or monitoringLogging.enabled left the old Deployments, Services and Ingresses
running (two Grafanas answering grafana.local).

Not pruned: Jobs and CronJobs (pabc-migrations is excluded from the render on
purpose; CronJob runs are never rendered), the objects the scripts create
outside the render (SCRIPT_MADE), anything with an ownerReference
(operator-created, like Prometheus' StatefulSet and prometheus-operated
Service), and anything without this chart's labels (podiumd-tests' Secret was
deleted twice).
"""

import json
import sys

from lib import kube
from lib import pki
from lib import process
from lib.manifests import ECK_KINDS
from lib.manifests import Doc
from lib.manifests import section
from lib.paths import NAMESPACE
from lib.paths import RELEASE_NAME

PRUNABLE_KINDS = (
    "Deployment", "StatefulSet", "DaemonSet",
    "Prometheus", "PrometheusRule", "ServiceMonitor", "PodMonitor",
    *ECK_KINDS,
    "Service", "Secret", "Ingress", "ConfigMap", "ServiceAccount", "HorizontalPodAutoscaler",
)  # fmt: skip
SCRIPT_MADE = frozenset({("ConfigMap", pki.TRUST_CONFIGMAP)})

# A deliberate profile toggle prunes a few objects; a deploy without --full
# against a --full cluster prunes dozens (this once removed every optional profile).
LARGE_PRUNE_THRESHOLD = 10


def live_objects(kind: str) -> list[Doc] | None:
    """Live objects of kind in the namespace; None when the kind's CRD is not installed."""
    result = process.run(["kubectl", "get", kind, "-n", NAMESPACE, "-o", "json"], check=False)
    if result.returncode != 0:
        if "the server doesn't have a resource type" in result.stderr:
            return None
        raise process.ProcessError(["kubectl", "get", kind], result.returncode, result.stderr)
    return json.loads(result.stdout)["items"]


def is_ours(item: Doc) -> bool:
    """Whether a live object carries this chart's labels (helm template sets one or both)."""
    labels = section(section(item, "metadata"), "labels")
    return (
        labels.get("app.kubernetes.io/instance") == RELEASE_NAME or labels.get("app.kubernetes.io/managed-by") == "Helm"
    )


def orphans(desired_docs: list[Doc]) -> list[tuple[str, str]]:
    """(kind, name) of this chart's live, unowned objects of PRUNABLE_KINDS that desired_docs lack."""
    desired = SCRIPT_MADE | {
        (doc["kind"], doc["metadata"]["name"]) for doc in desired_docs if doc.get("kind") in PRUNABLE_KINDS
    }
    found: list[tuple[str, str]] = []
    for kind in PRUNABLE_KINDS:
        for item in live_objects(kind) or []:
            metadata = item["metadata"]
            if (kind, metadata["name"]) not in desired and not metadata.get("ownerReferences") and is_ours(item):
                found.append((kind, metadata["name"]))
    return found


def prune(desired_docs: list[Doc], *, force: bool) -> None:
    """Deletes orphans; refuses more than LARGE_PRUNE_THRESHOLD unless force."""
    to_delete = orphans(desired_docs)
    if not to_delete:
        print("No orphaned objects found - nothing to prune.")
        return
    if len(to_delete) > LARGE_PRUNE_THRESHOLD and not force:
        listing = "\n".join(f"  {kind}/{name}" for kind, name in to_delete)
        print(f"Would prune:\n{listing}", file=sys.stderr)
        msg = (
            f"refusing to prune {len(to_delete)} resources (threshold {LARGE_PRUNE_THRESHOLD}): this usually "
            "means fewer profile flags than the cluster runs (e.g. deploy after deploy --full); "
            "re-run with the same flags, or add --force-prune if this is intended"
        )
        raise process.UserError(msg)
    for kind, name in to_delete:
        kube.kubectl_shown("delete", kind, name, "-n", NAMESPACE)
    print("Pruned objects not part of the current render: " + ", ".join(f"{k}/{n}" for k, n in to_delete))
