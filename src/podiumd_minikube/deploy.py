"""Renders this chart and applies it to the minikube cluster."""

import argparse
import re

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from podiumd_minikube import chart
from podiumd_minikube import crds
from podiumd_minikube import dependency
from podiumd_minikube import disk
from podiumd_minikube import dns
from podiumd_minikube import gateway
from podiumd_minikube import hosts
from podiumd_minikube import keycloak
from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import manifests
from podiumd_minikube import memory
from podiumd_minikube import openbao
from podiumd_minikube import pki
from podiumd_minikube import polling
from podiumd_minikube import postgres
from podiumd_minikube import process
from podiumd_minikube import prune
from podiumd_minikube import upgrade
from podiumd_minikube.paths import NAMESPACE

STORAGE_HOOKS = "templates/storage-hooks.yaml"
STORAGE_PERMISSIONS_JOB = "storage-permissions-fix"
# Rollouts and config Jobs of a --full deploy, Elasticsearch included.
READY_TIMEOUT = 900

# Server-side rejections start "Error from server ("; the others are client-side.
_CLIENT_SIDE_ERRORS = re.compile(
    "no matches for kind|does not match the namespace|ensure CRDs are installed|cannot be handled as"
)


# As `helm upgrade`: the chart's `lookup` reads the live cluster (a plain `helm template`
# gets nothing, so e.g. Grafana's admin password changed on every deploy).
# --take-ownership skips Helm's check that a release owns the live objects: kubectl
# apply manages them here, and a dry run writes nothing.
LOOKUP_ARGS = ["--dry-run=server", "--take-ownership"]
# A server-side dry run also maps every kind; before the operators' CRDs exist (a fresh
# cluster, where lookup finds nothing anyway) the render falls back to plain helm template.
_CRDS_MISSING = "ensure CRDs are installed first"


@dataclass
class Options:
    """What to render: helm --set flags and the fixups they imply."""

    helm_args: list[str] = field(default_factory=list[str])
    objecten_merged: bool = False

    def render(self, *args: str) -> manifests.Render:
        """`helm template` with these options and args, fixed up."""
        try:
            text = manifests.helm_template(*LOOKUP_ARGS, *self.helm_args, *args)
        except process.ProcessError as error:
            if _CRDS_MISSING not in error.stderr:
                raise
            text = manifests.helm_template(*self.helm_args, *args)
        return manifests.fix_up(text, objecten_merged=self.objecten_merged, ca_trust=True)


def options(*, full: bool, extra: list[str]) -> Options:
    """Options for a deploy with or without --full, plus extra helm args."""
    selected = Options(helm_args=chart.bundled_image_sets())
    if full:
        shape = chart.objecten_shape()
        selected.helm_args += [*chart.FULL_PROFILE_SETS, *shape.sets]
        selected.objecten_merged = shape.merged
    selected.helm_args += [*openbao.seal_key_args(), *extra]
    return selected


def apply_errors(output: str) -> int:
    """Number of failed objects in `kubectl apply` output."""
    lines = output.splitlines()
    return sum(1 for line in lines if line.startswith("Error from server (")) + sum(
        1 for line in lines if _CLIENT_SIDE_ERRORS.search(line)
    )


def expected_storage_errors(storage: manifests.Render) -> int:
    """podiumd's own Azure PV and PVC per app in storage-hooks.yaml, rejected as immutable on purpose."""
    return 2 * sum(1 for doc in storage.docs if doc.get("kind") == "PersistentVolume")


def jobs(render: manifests.Render) -> list[str]:
    """The Jobs in render (all idempotent)."""
    return [manifests.name_of(doc) for doc in render.docs if doc.get("kind") == "Job"]


def _rerun_jobs(render: manifests.Render) -> None:
    """Deletes the render's Jobs so the apply recreates and re-runs them.

    As ExternalsPodiumD's pipeline does before every deploy: a Job is
    immutable, so a changed spec cannot be applied, and an unchanged one only
    re-runs once its TTL has removed it.
    """
    names = jobs(render)
    if names:
        print(f"Deleting {len(names)} Job(s) so this deploy re-runs them...")
        kube.kubectl_shown("delete", "job", *names, "-n", NAMESPACE, "--ignore-not-found")


def _apply_storage_hooks(selected: Options) -> manifests.Render:
    """Applies storage-hooks.yaml's hostPath PV/PVC pairs before the rest, so podiumd's cannot replace them."""
    # Its volume list follows the enabled profiles, and Jobs are immutable.
    kube.kubectl_shown("delete", "job", STORAGE_PERMISSIONS_JOB, "-n", NAMESPACE, "--ignore-not-found")
    print("Applying storage-hook PV/PVC pairs first...")
    storage = selected.render("-s", STORAGE_HOOKS)
    kube.kubectl_shown("apply", "-n", NAMESPACE, "-f", "-", stdin=storage.manifest)
    if kube.exists(f"job/{STORAGE_PERMISSIONS_JOB}"):
        print(f"Waiting for the {STORAGE_PERMISSIONS_JOB} Job to complete...")
        kube.kubectl_shown(
            "wait", "--for=condition=complete", f"job/{STORAGE_PERMISSIONS_JOB}", "-n", NAMESPACE, "--timeout=60s"
        )
    return storage


def _apply_crds(render: manifests.Render) -> None:
    """Applies the render's CRDs (the Keycloak operator's from upstream) server-side and waits until established."""
    if not render.crds:
        return
    docs = crds.with_upstream_keycloak(render.crds, crds.keycloak_version())
    print(f"\nApplying {len(docs)} CRD(s) first...")
    kube.kubectl_shown("apply", "--server-side", "--force-conflicts", "-f", "-", stdin=manifests.dump(docs))
    names = [f"crd/{manifests.name_of(doc)}" for doc in docs]
    kube.kubectl_shown("wait", "--for=condition=Established", *names, "--timeout=60s")


def _apply_full_manifest(render: manifests.Render, expected: int) -> None:
    print("\nApplying the full manifest...")
    print(f'NOTE: expect {expected} long "spec is immutable" errors below for PersistentVolumes/')
    print("PersistentVolumeClaims - HARMLESS. podiumd's own Azure-CSI storage objects are")
    print("rejected on purpose so this chart's hostPath ones (applied above) stay in place.")
    applied = process.run(
        ["kubectl", "apply", "-n", NAMESPACE, "-f", "-"], stdin=render.manifest, check=False, merge_stderr=True
    )
    print(applied.stdout, end="")
    actual = apply_errors(applied.stdout)
    print()
    if applied.returncode == 0:
        print("Applied cleanly.")
    elif actual == expected:
        print(f'{actual} "spec is immutable" error(s) above - expected and HARMLESS (podiumd\'s')
        print("own competing Azure-CSI storage objects being correctly rejected, so this chart's")
        print("hostPath PV/PVCs stay in place), not a real failure.")
    else:
        msg = f"{actual} apply error(s), expected exactly {expected} (immutable PV/PVC): check the output above"
        raise process.UserError(msg)
    if render.large_configmaps:
        print("\nApplying large ConfigMap(s) via --server-side...")
        kube.kubectl_shown(
            "apply", "--server-side", "-n", NAMESPACE, "-f", "-", stdin=manifests.dump(render.large_configmaps)
        )


@lock.holding("deploy")
def deploy(*, full: bool, force_prune: bool, extra: list[str]) -> None:
    """Syncs dependencies, applies the render, then the guarded and post-apply steps."""
    kube.require_minikube_context()
    disk.check(disk.DEPLOY)
    memory.check(full=full)
    dependency.sync()
    selected = options(full=full, extra=extra)

    print(f"Ensuring namespace '{NAMESPACE}' exists...")
    namespace = kube.kubectl("create", "namespace", NAMESPACE, "--dry-run=client", "-o", "yaml")
    kube.kubectl("apply", "-f", "-", stdin=namespace)

    if not pki.CA_CERT.is_file():
        msg = f"no local CA in {pki.PKI_DIR}: run podiumd-minikube provision-cluster (it creates the CA and its issuer)"
        raise process.UserError(msg)
    # Before anything with pods: every pod, the storage Job included, mounts it.
    pki.apply_trust(NAMESPACE)
    storage = _apply_storage_hooks(selected)
    render = selected.render()
    postgres.migrate_major(render)
    postgres.rename_databases()
    postgres.create_missing_databases()
    postgres.align_with_azure()
    postgres.rename_rows()
    if crds.needed(render.docs):
        print("\nApplying monitoring-logging's own CRDs first...")
        crds.apply_monitoring_logging_crds()
    _rerun_jobs(render)
    _apply_crds(render)
    _apply_full_manifest(render, expected_storage_errors(storage))
    print()
    chart_hosts = hosts.chart_hosts()
    # The Gateway first: NGF creates the Service CoreDNS points at only once the Gateway exists.
    gateway.apply(render.docs, chart_hosts)
    dns.apply_hosts(chart_hosts)

    if undeleted := upgrade.undeleted_fixed_jobs(render.docs):
        print(
            f"\nWARNING: fixed-name Jobs ExternalsPodiumD does not delete before helm upgrade: {', '.join(undeleted)}"
        )
    print()
    keycloak.repair_default_client_scopes(render)
    openbao.bootstrap(render)
    print(f"\nPruning {', '.join(prune.PRUNABLE_KINDS)} not part of this render...")
    prune.prune([*render.docs, *render.large_configmaps], force=force_prune)
    _wait_ready(render)
    print(
        "\nDone. Next: podiumd-minikube setup-tunnel for external reachability, or run the suite in tests/ to verify."
    )


def rolled_out(workload: dict[str, Any]) -> bool:
    """Whether a workload runs its current spec and is ready.

    Elasticsearch and Kibana: ECK's own status (its StatefulSets are not rendered); a
    single node is yellow.
    """
    status = workload.get("status", {})
    current = status.get("observedGeneration", 0) >= workload["metadata"].get("generation", 0)
    if workload.get("kind") in manifests.ECK_KINDS:
        return current and status.get("health") in ("green", "yellow") and status.get("phase", "Ready") == "Ready"
    wanted = workload.get("spec", {}).get("replicas", 1)
    updated = status.get("updatedReplicas", 0)
    # readyReplicas also counts old pods while a rollout replaces them.
    old_gone = status.get("replicas", 0) <= updated
    return current and updated >= wanted and old_gone and status.get("readyReplicas", 0) >= wanted


def unfinished_jobs(live: list[dict[str, Any]], names: list[str]) -> tuple[list[str], list[str]]:
    """(running, failed) among the Jobs called names, from `kubectl get job -o json` items live."""
    outcomes: dict[str, str] = {}
    for job in live:
        conditions: list[dict[str, Any]] = job.get("status", {}).get("conditions") or []
        done = {c["type"] for c in conditions if c.get("status") == "True"} & {"Complete", "Failed"}
        if done:
            outcomes[job["metadata"]["name"]] = "Failed" if "Failed" in done else "Complete"
    running = [name for name in names if name not in outcomes]
    failed = [name for name in names if outcomes.get(name) == "Failed"]
    return running, failed


def _wait_ready(render: manifests.Render) -> None:
    """Waits until the render's workloads have rolled out and its Jobs have completed; UserError on failed Jobs."""
    print("\nWaiting until every rendered workload has rolled out and every Job has completed...")
    workloads = [
        f"{doc['kind'].lower()}/{manifests.name_of(doc)}"
        for doc in render.docs
        if doc.get("kind") in ("Deployment", "StatefulSet", *manifests.ECK_KINDS)
    ]
    names = jobs(render)

    def job_state() -> tuple[list[str], list[str]]:
        return unfinished_jobs(kube.get_json("job", "-n", NAMESPACE)["items"], names)

    def stuck() -> list[str]:
        return [w for w in workloads if not rolled_out(kube.get_json(w, "-n", NAMESPACE))]

    # Not `rollout status`: it gives up at the progress deadline (600s), which a
    # first deploy passes while pods wait for Elasticsearch, yet still converge.
    def settled() -> tuple[list[str]] | None:
        running, failed = job_state()
        return None if running or stuck() else (failed,)

    result = polling.wait_until(settled, timeout=READY_TIMEOUT, interval=10)
    if result is None:
        running, _ = job_state()
        msg = (
            f"after {READY_TIMEOUT}s: not rolled out: {', '.join(stuck()) or '-'}; running: {', '.join(running) or '-'}"
        )
        raise process.UserError(msg)
    if result[0]:
        msg = f"Job(s) failed: {', '.join(result[0])}: check `kubectl logs job/<name> -n {NAMESPACE}`"
        raise process.UserError(msg)
    print("All workloads rolled out, all Jobs completed.")


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Adds deploy's arguments to parser; unknown ones (--set x=y) go to helm."""
    parser.add_argument("--full", action="store_true", help="enable every optional profile")
    parser.add_argument(
        "--skip-roles",
        action="store_true",
        help="realm import without roles and groups (chart default; a fresh realm then lacks them)",
    )
    parser.add_argument(
        "--force-prune", action="store_true", help=f"prune more than {prune.LARGE_PRUNE_THRESHOLD} orphaned resources"
    )
    return parser
