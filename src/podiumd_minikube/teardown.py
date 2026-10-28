"""Irreversible removal of the namespaces or the whole cluster, after confirmation."""

import json
import sys

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import openbao
from podiumd_minikube import process
from podiumd_minikube import provision
from podiumd_minikube import tunnel
from podiumd_minikube.manifests import name_of
from podiumd_minikube.manifests import section
from podiumd_minikube.paths import MONITORING_NAMESPACE
from podiumd_minikube.paths import MONITORING_RELEASE
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import OLD_NAMESPACE
from podiumd_minikube.paths import PODIUMD_RELEASE
from podiumd_minikube.paths import PROFILE
from podiumd_minikube.paths import RELEASE_NAME


def confirmed(summary: str, *, yes: bool) -> bool:
    """Prints summary; True when yes or the user types 'yes'."""
    print(summary)
    if yes:
        return True
    try:
        if input("Type 'yes' to continue: ") == "yes":
            return True
    except EOFError:
        print("\nNo input to confirm with: pass --yes.")
    print("Aborted - nothing was deleted.")
    return False


def bound_volumes(volumes: list[Any], namespace: str = NAMESPACE) -> list[str]:
    """Names of PersistentVolumes whose claimRef is in namespace.

    Covers storage-hooks.yaml's Retain PVs and dynamic Delete PVs that
    minikube's provisioner left Released after the namespace was deleted.
    """
    return [name_of(pv) for pv in volumes if section(section(pv, "spec"), "claimRef").get("namespace") == namespace]


# The kubectl-apply deploys' namespace goes too, so a deploy can start clean after the Helm switch.
RESET_NAMESPACES = (NAMESPACE, MONITORING_NAMESPACE, OLD_NAMESPACE)
RESET_INSTANCES = (RELEASE_NAME, PODIUMD_RELEASE, MONITORING_RELEASE)
RESET_SUMMARY = f"""This permanently empties namespaces {", ".join(RESET_NAMESPACES)}:
  - every object in them, the Helm releases, and all data: Postgres, Solr, PABC, every app's media
  - every PersistentVolume bound to them, plus its hostPath data on the minikube node
  - the releases' cluster-scoped RBAC/webhook objects
It keeps the cluster and the CRDs (deploy re-applies them).
"""


# The releases per namespace; the old namespace's objects came from kubectl apply.
NAMESPACE_RELEASES = {
    NAMESPACE: (PODIUMD_RELEASE, RELEASE_NAME),
    MONITORING_NAMESPACE: (MONITORING_RELEASE,),
    OLD_NAMESPACE: (),
}
NAMESPACE_TIMEOUT = "120s"


def clear_finalizers(namespace: str) -> int:
    """Clears the finalizers of every object left in namespace; returns how many objects had them.

    The operators that would finish them (Redis, Solr, ECK, Keycloak) go with the namespace, which then
    stays Terminating (podiumd-infra's reset.py does the same).
    """
    kinds = kube.kubectl("api-resources", "--verbs=list,delete", "--namespaced", "-o", "name").split()
    # A kind the API server cannot list does not stop the others: kubectl prints what it found.
    listed = process.run(["kubectl", "get", ",".join(kinds), "-n", namespace, "-o", "json"], check=False)
    items: list[dict[str, Any]] = json.loads(listed.stdout or "{}").get("items") or []
    held = [item for item in items if item["metadata"].get("finalizers")]
    for item in held:
        group = item["apiVersion"].split("/")[0] if "/" in item["apiVersion"] else ""
        kind = f"{item['kind'].lower()}.{group}" if group else item["kind"].lower()
        patch = ["patch", f"{kind}/{name_of(item)}", "-n", namespace, "--type=merge"]
        process.run(["kubectl", *patch, "-p", '{"metadata":{"finalizers":null}}'], check=False)
    return len(held)


def _empty(namespace: str) -> str:
    """Uninstalls namespace's releases without hooks, clears finalizers, deletes it; returns what it did.

    No hooks: zookeeper-operator's pre-delete hook holds an uninstall for its 120 s timeout. No grace
    period: the data goes anyway, and the namespace would wait for every pod's shutdown.
    """
    if not kube.exists(f"namespace/{namespace}"):
        return f"{namespace}: does not exist"
    for release in NAMESPACE_RELEASES[namespace]:
        process.run(["helm", "uninstall", release, "-n", namespace, "--no-hooks", "--timeout", "120s"], check=False)
    cleared = clear_finalizers(namespace)
    process.run(
        ["kubectl", "delete", "pods", "--all", "-n", namespace, "--grace-period=0", "--force", "--wait=false"],
        check=False,
    )
    delete = ["kubectl", "delete", "namespace", namespace, "--ignore-not-found", f"--timeout={NAMESPACE_TIMEOUT}"]
    if process.run(delete, check=False).returncode != 0:
        msg = (
            f"namespace {namespace} is still Terminating after {NAMESPACE_TIMEOUT}: `kubectl get namespace {namespace}"
            " -o yaml` names what holds it in status.conditions"
        )
        raise process.UserError(msg)
    return f"{namespace}: deleted ({cleared} object(s) with finalizers cleared)"


CLUSTER_KINDS = "clusterrole,clusterrolebinding,mutatingwebhookconfiguration,validatingwebhookconfiguration"


def cluster_leftovers(items: list[Any]) -> list[str]:
    """kind/name of the cluster-scoped objects of RESET_INSTANCES: by label, or by Helm's release annotation.

    Some carry no instance label (the Keycloak and ZooKeeper operators' ClusterRoles); a release that
    finds one owned by another release name fails to install.
    """
    found: list[str] = []
    for item in items:
        metadata = section(item, "metadata")
        instance = section(metadata, "labels").get("app.kubernetes.io/instance")
        release = section(metadata, "annotations").get("meta.helm.sh/release-name")
        if instance in RESET_INSTANCES or release in RESET_INSTANCES:
            found.append(f"{item['kind'].lower()}/{name_of(item)}")
    return found


@lock.holding("reset-namespace")
def reset_namespace(*, yes: bool) -> int:
    """Deletes the namespaces and what they leave behind cluster-wide; 1 when not confirmed."""
    kube.require_minikube_context()
    if not confirmed(RESET_SUMMARY, yes=yes):
        return 1
    print(f"Deleting namespaces {', '.join(RESET_NAMESPACES)} and their releases...")
    with ThreadPoolExecutor(max_workers=len(RESET_NAMESPACES)) as pool:
        for line in pool.map(_empty, RESET_NAMESPACES):
            print(f"  {line}")
    print("Deleting PersistentVolumes still bound to them...")
    volumes = kube.get_json("pv")["items"]
    stale = [name for namespace in RESET_NAMESPACES for name in bound_volumes(volumes, namespace)]
    if stale:
        kube.kubectl_shown("delete", "pv", *stale)
    else:
        print("  (none found)")
    print("Clearing hostPath data on the minikube node...")
    paths = " ".join(f"/data/{ns}/* /tmp/hostpath-provisioner/{ns}" for ns in RESET_NAMESPACES)
    try:
        kube.node(f"sudo rm -rf {paths}")
    except process.ProcessError:
        print("  WARNING: could not reach the minikube node to clear hostPath data - do it by hand.", file=sys.stderr)
    print("Deleting the releases' cluster-scoped RBAC/webhook objects (if any)...")
    if leftovers := cluster_leftovers(kube.get_json(CLUSTER_KINDS)["items"]):
        kube.kubectl_shown("delete", *leftovers, "--ignore-not-found")
    else:
        print("  (none found)")
    openbao.forget_vault()
    print("\nDone. Run podiumd-minikube deploy to redeploy from scratch.")
    return 0


TEARDOWN_SUMMARY = f"""This permanently deletes the '{PROFILE}' minikube cluster:
  - every object and all hostPath data (Postgres, Solr, every app's media)
  - the minikube container itself
Docker images on the host survive, so a new cluster loads them without downloading;
only older tags of this chart's image repositories are removed.
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
    provision.remove_old_tags(provision.chart_images())
    print("\nDone. After provision-cluster and deploy, run setup-tunnel; /etc/hosts stays valid (fixed edge IP).")
    return 0
