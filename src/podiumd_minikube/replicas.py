"""Runs Redis and Keycloak with the chart's pod counts for the tests that need more than one pod.

values.yaml runs one pod of each. `on` renders the chart with the counts below
and applies only what differs from the plain render: changed fields as a merge
patch, objects only that render has (a PodDisruptionBudget the chart renders for
more than one pod). `off` does the reverse; a deploy also resets it.
"""

import json

from typing import Any
from typing import cast

from podiumd_minikube import deploy
from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import manifests
from podiumd_minikube import polling
from podiumd_minikube import process
from podiumd_minikube import services
from podiumd_minikube.manifests import Doc
from podiumd_minikube.paths import NAMESPACE

# StatefulSet -> (the value that sets its pod count, the chart's default count).
MULTI_POD = {
    "redis-ha": ("podiumd.redis-operator.redis-ha.replicaCount", 3),
    "keycloak": ("podiumd.keycloak.instances", 2),
}
SETS = [arg for value, count in MULTI_POD.values() for arg in ("--set", f"{value}={count}")]
# Keycloak starts in about a minute; its realm sync waits for it.
READY_TIMEOUT = 300

Key = tuple[str, str, str]


def _key(doc: Doc) -> Key:
    return doc["apiVersion"], doc["kind"], manifests.name_of(doc)


def merge_patch(old: Any, new: Any) -> Any:
    """The JSON merge patch (RFC 7386) that turns old into new; lists are replaced whole."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return new
    before = cast("Doc", old)
    after = cast("Doc", new)
    patch: Doc = {key: None for key in before if key not in after}
    for key, value in after.items():
        if key not in before:
            patch[key] = value
        elif before[key] != value:
            patch[key] = merge_patch(before[key], value)
    return patch


def changes(source: list[Doc], target: list[Doc]) -> tuple[list[Doc], dict[Key, Any], list[Key]]:
    """(objects to create, merge patches per object, objects to delete) that turn source into target."""
    before = {_key(doc): doc for doc in source}
    after = {_key(doc): doc for doc in target}
    created = [doc for key, doc in after.items() if key not in before]
    patches = {key: merge_patch(before[key], doc) for key, doc in after.items() if key in before and before[key] != doc}
    deleted = [key for key in before if key not in after]
    return created, patches, deleted


def _resource(key: Key) -> str:
    api_version, kind, name = key
    group = api_version.rpartition("/")[0]
    return f"{kind}.{group}/{name}" if group else f"{kind}/{name}"


def _counted(counts: dict[str, int]) -> list[str] | None:
    """None while a StatefulSet in counts lacks its pod count or has not rolled out; else its names."""
    for name, count in counts.items():
        statefulset = kube.get_json(f"statefulset/{name}", "-n", NAMESPACE)
        if statefulset["spec"].get("replicas") != count or not deploy.rolled_out(statefulset):
            return None
    return list(counts)


@lock.holding("replicas")
def switch(*, on: bool) -> None:
    """Applies the chart's pod counts (on) or values.yaml's one pod (off), then waits for the pods."""
    kube.require_minikube_context()
    selected = deploy.options(full=False, extra=[])
    print("Rendering with one pod and with the chart's pod counts...")
    one, several = selected.render().docs, selected.render(*SETS).docs
    created, patches, deleted = changes(*((one, several) if on else (several, one)))
    if created:
        kube.kubectl_shown("apply", "-n", NAMESPACE, "-f", "-", stdin=manifests.dump(created))
    for key, patch in patches.items():
        kube.kubectl_shown("patch", _resource(key), "-n", NAMESPACE, "--type", "merge", "-p", json.dumps(patch))
    for key in deleted:
        kube.kubectl_shown("delete", _resource(key), "-n", NAMESPACE, "--ignore-not-found")
    counts = {name: count if on else 1 for name, (_, count) in MULTI_POD.items()}
    print(f"Waiting for {', '.join(f'{name} at {count}' for name, count in counts.items())}...")
    if polling.wait_until(lambda: _counted(counts), timeout=READY_TIMEOUT, interval=5) is None:
        msg = f"after {READY_TIMEOUT}s: not at their pod count: check `kubectl get statefulset -n {NAMESPACE}`"
        raise process.UserError(msg)
    if not on:
        # The operator promotes no replica: when the master was a removed pod, redis-ha-0 stays a read-only replica.
        kube.kubectl_shown(
            "exec", "-n", NAMESPACE, "redis-ha-0", "-c", "redis-ha", "--", "redis-cli", "REPLICAOF", "NO", "ONE"
        )
    # The operator then records the master in the RedisReplication's status, and the label CronJob labels it.
    kube.first_pod(services.REDIS_MASTER)
    print("Done.")
