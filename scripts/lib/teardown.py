"""Irreversible removal of the namespace or the whole cluster, after confirmation."""

import sys

from typing import Any

from lib import kube
from lib import lock
from lib import openbao
from lib import process
from lib import tunnel
from lib.manifests import name_of
from lib.manifests import section
from lib.paths import NAMESPACE
from lib.paths import PROFILE


def confirmed(summary: str, *, yes: bool) -> bool:
    """Prints summary; True when yes or the user types 'yes'."""
    print(summary)
    if yes:
        return True
    if input("Type 'yes' to continue: ") == "yes":
        return True
    print("Aborted - nothing was deleted.")
    return False


def bound_volumes(volumes: list[Any], namespace: str = NAMESPACE) -> list[str]:
    """Names of PersistentVolumes whose claimRef is in namespace.

    Covers storage-hooks.yaml's Retain PVs and dynamic Delete PVs that
    minikube's provisioner left Released after the namespace was deleted.
    """
    return [name_of(pv) for pv in volumes if section(section(pv, "spec"), "claimRef").get("namespace") == namespace]


RESET_SUMMARY = f"""This permanently empties namespace '{NAMESPACE}':
  - every object in it, and all data: Postgres, Solr, PABC, every app's media
  - every PersistentVolume bound to it, plus its hostPath data on the minikube node
  - monitoring-logging's cluster-scoped RBAC/webhook objects, if it was ever enabled
It keeps the cluster and the monitoring-logging CRDs (deploy re-applies them).
"""


@lock.holding("reset-namespace")
def reset_namespace(*, yes: bool) -> int:
    """Deletes the namespace and what it leaves behind cluster-wide; 1 when not confirmed."""
    kube.require_minikube_context()
    if not confirmed(RESET_SUMMARY, yes=yes):
        return 1
    print(f"Deleting namespace '{NAMESPACE}'...")
    kube.kubectl_shown("delete", "namespace", NAMESPACE, "--ignore-not-found")
    print("Waiting for the namespace to finish deleting (finalizers can make this slow)...")
    process.run(["kubectl", "wait", "--for=delete", f"namespace/{NAMESPACE}", "--timeout=120s"], check=False)
    print(f"Deleting PersistentVolumes still bound to '{NAMESPACE}'...")
    stale = bound_volumes(kube.get_json("pv")["items"])
    if stale:
        kube.kubectl_shown("delete", "pv", *stale)
    else:
        print("  (none found)")
    print("Clearing hostPath data on the minikube node...")
    cleared = process.run(
        [
            "minikube",
            "ssh",
            "-p",
            PROFILE,
            "--",
            f"sudo rm -rf /data/{NAMESPACE}/* /tmp/hostpath-provisioner/{NAMESPACE}",
        ],
        check=False,
    )
    if cleared.returncode != 0:
        print("  WARNING: could not reach the minikube node to clear hostPath data - do it by hand.", file=sys.stderr)
    print("Deleting monitoring-logging's cluster-scoped RBAC/webhook objects (if any)...")
    kube.kubectl_shown(
        "delete", "clusterrole,clusterrolebinding,mutatingwebhookconfiguration,validatingwebhookconfiguration",
        "-l", f"app.kubernetes.io/instance={NAMESPACE}", "--ignore-not-found",
    )  # fmt: skip
    openbao.forget_vault()
    print(f"\nDone. '{NAMESPACE}' is gone - run ./scripts/deploy to redeploy from scratch.")
    return 0


TEARDOWN_SUMMARY = f"""This permanently deletes the '{PROFILE}' minikube cluster:
  - every object and all hostPath data (Postgres, Solr, every app's media)
  - the minikube container itself
Docker images on the host survive, so a new cluster loads them without downloading.
"""


@lock.holding("teardown-cluster")
def teardown_cluster(*, yes: bool) -> int:
    """Stops minikube tunnel and deletes the profile; 1 when not confirmed."""
    if not confirmed(TEARDOWN_SUMMARY, yes=yes):
        return 1
    tunnel.stop_tunnel()
    print(f"Deleting minikube profile '{PROFILE}'...")
    process.run(["minikube", "delete", "-p", PROFILE], capture=False)
    openbao.forget_vault()
    print("\nDone. After provision-cluster and deploy, run setup-tunnel; /etc/hosts stays valid (fixed edge IP).")
    return 0
