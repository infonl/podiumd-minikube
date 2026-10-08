"""Renders this chart and applies it to the minikube cluster."""

import argparse
import re

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from lib import chart
from lib import crds
from lib import dependency
from lib import disk
from lib import dns
from lib import gateway
from lib import hosts
from lib import keycloak
from lib import kube
from lib import manifests
from lib import memory
from lib import openbao
from lib import pabc
from lib import pki
from lib import polling
from lib import postgres
from lib import process
from lib import prune
from lib import seed
from lib import values
from lib.paths import NAMESPACE
from lib.paths import RELEASE_NAME

STORAGE_HOOKS = "templates/storage-hooks.yaml"
STORAGE_PERMISSIONS_JOB = "storage-permissions-fix"
# Rollouts and config Jobs of a --full deploy, Elasticsearch included.
READY_TIMEOUT = 900

# Server-side rejections start "Error from server ("; the others are client-side.
_CLIENT_SIDE_ERRORS = re.compile(
    "no matches for kind|does not match the namespace|ensure CRDs are installed|cannot be handled as"
)


@dataclass
class Options:
    """What to render: helm --set flags and the fixups they imply."""

    helm_args: list[str] = field(default_factory=list[str])
    objecten_merged: bool = False
    zac_pkce: bool = False

    def render(self, *args: str) -> manifests.Render:
        """`helm template` with these options and args, fixed up."""
        text = manifests.helm_template(*self.helm_args, *args)
        return manifests.fix_up(text, objecten_merged=self.objecten_merged, zac_pkce=self.zac_pkce, ca_trust=True)


def options(*, full: bool, extra: list[str]) -> Options:
    """Options for a deploy with or without --full, plus extra helm args."""
    pkce = chart.zac_pkce()
    selected = Options(helm_args=[*pkce.sets], zac_pkce=pkce.enabled)
    if full:
        shape = chart.objecten_shape()
        selected.helm_args += [*chart.FULL_PROFILE_SETS, *shape.sets]
        selected.objecten_merged = shape.merged
    if values.monitoring_logging_enabled():
        # monitoring-logging's collector Service, named by that chart's fullname template.
        endpoint = f"http://{RELEASE_NAME}-opentelemetry-collector:4317"
        selected.helm_args += ["--set", f"podiumd.zac.opentelemetry_zaakafhandelcomponent.endpoint={endpoint}"]
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
    """The Jobs in render (all idempotent; pabc-migrations is not in a render)."""
    return [manifests.name_of(doc) for doc in [*render.docs, *render.after_seed] if doc.get("kind") == "Job"]


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
    """Applies the render's CRDs server-side and waits until they are established."""
    if not render.crds:
        return
    print(f"\nApplying {len(render.crds)} CRD(s) first...")
    kube.kubectl_shown("apply", "--server-side", "--force-conflicts", "-f", "-", stdin=manifests.dump(render.crds))
    names = [f"crd/{manifests.name_of(doc)}" for doc in render.crds]
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
        msg = f"no local CA in {pki.PKI_DIR}: run scripts/provision-cluster (it creates the CA and its issuer)"
        raise process.UserError(msg)
    # Before anything with pods: every pod, the storage Job included, mounts it.
    pki.apply_trust(NAMESPACE)
    storage = _apply_storage_hooks(selected)
    if values.monitoring_logging_enabled():
        print("\nApplying monitoring-logging's own CRDs first...")
        crds.apply_monitoring_logging_crds()
    postgres.create_missing_databases()
    render = selected.render()
    _rerun_jobs(render)
    _apply_crds(render)
    _apply_full_manifest(render, expected_storage_errors(storage))
    print()
    chart_hosts = hosts.chart_hosts()
    # The Gateway first: NGF creates the Service CoreDNS points at only once the Gateway exists.
    gateway.apply(render.docs, chart_hosts)
    dns.apply_hosts(chart_hosts)

    print()
    keycloak.sync_realm(zac_pkce=selected.zac_pkce)
    openbao.bootstrap(render)
    print("\nApplying pabc-migrations (guarded - see scripts/lib/pabc.py)...")
    pabc.apply_migrations(force=False)
    print(f"\nPruning {', '.join(prune.PRUNABLE_KINDS)} not part of this render...")
    prune.prune([*render.docs, *render.large_configmaps, *render.after_seed], force=force_prune)

    # Live state after pruning decides, so a profile just switched off is not seeded.
    print()
    if kube.exists("deployment/objecten"):
        print("Seeding fixture data (see scripts/lib/seed.py)...")
        seed.seed_fixtures(merged=chart.objecten_shape().merged)
    else:
        print("'objecten' profile not deployed - skipping seeding.")
    if render.after_seed:
        print("\nApplying the Job(s) that must run after seeding...")
        kube.kubectl_shown("apply", "-n", NAMESPACE, "-f", "-", stdin=manifests.dump(render.after_seed))
    _wait_ready(render)
    print("\nDone. Next: ./scripts/setup-tunnel for external reachability, or run the suite in tests/ to verify.")


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
    by_name = {job["metadata"]["name"]: job for job in live}
    running: list[str] = []
    failed: list[str] = []
    for name in names:
        conditions: list[dict[str, Any]] = by_name.get(name, {}).get("status", {}).get("conditions") or []
        done = {c["type"] for c in conditions if c.get("status") == "True"}
        if "Failed" in done:
            failed.append(name)
        elif "Complete" not in done:
            running.append(name)
    return running, failed


def _wait_ready(render: manifests.Render) -> None:
    """Waits until the render's workloads have rolled out and its Jobs have completed; UserError on failed Jobs."""
    print("\nWaiting until every rendered workload has rolled out and every Job has completed...")
    workloads = [
        f"{doc['kind'].lower()}/{manifests.name_of(doc)}"
        for doc in render.docs
        if doc.get("kind") in ("Deployment", "StatefulSet", *manifests.ECK_KINDS)
    ]

    # Not `rollout status`: it gives up at the progress deadline (600s), which a
    # first deploy passes while pods wait for Elasticsearch, yet still converge.
    def all_rolled_out() -> bool:
        return all(rolled_out(kube.get_json(w, "-n", NAMESPACE)) for w in workloads)

    if not polling.wait_until(all_rolled_out, timeout=READY_TIMEOUT, interval=10):
        stuck = [w for w in workloads if not rolled_out(kube.get_json(w, "-n", NAMESPACE))]
        msg = f"not rolled out after {READY_TIMEOUT}s: {', '.join(stuck)}"
        raise process.UserError(msg)
    names = jobs(render)

    def settled() -> tuple[list[str], list[str]] | None:
        running, failed = unfinished_jobs(kube.get_json("job", "-n", NAMESPACE)["items"], names)
        return None if running else (running, failed)

    result = polling.wait_until(settled, timeout=READY_TIMEOUT, interval=5)
    if result is None:
        running, _ = unfinished_jobs(kube.get_json("job", "-n", NAMESPACE)["items"], names)
        msg = f"Job(s) still running after {READY_TIMEOUT}s: {', '.join(running)}"
        raise process.UserError(msg)
    if result[1]:
        msg = f"Job(s) failed: {', '.join(result[1])}: check `kubectl logs job/<name> -n {NAMESPACE}`"
        raise process.UserError(msg)
    print("All workloads rolled out, all Jobs completed.")


def parser(description: str) -> argparse.ArgumentParser:
    """deploy's arguments; unknown ones (--set x=y) go to helm."""
    result = argparse.ArgumentParser(description=description, allow_abbrev=False)
    result.add_argument("--full", action="store_true", help="enable every optional profile")
    result.add_argument(
        "--force-prune", action="store_true", help=f"prune more than {prune.LARGE_PRUNE_THRESHOLD} orphaned resources"
    )
    return result


def main(argv: list[str], description: str) -> None:
    """Parses argv and deploys."""
    args, extra = parser(description).parse_known_args(argv)
    deploy(full=args.full, force_prune=args.force_prune, extra=extra)
