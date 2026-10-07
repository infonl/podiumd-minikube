"""Renders this chart and applies it to the minikube cluster."""

import argparse
import re

from dataclasses import dataclass
from dataclasses import field

from lib import chart
from lib import crds
from lib import dependency
from lib import dns
from lib import hosts
from lib import keycloak
from lib import kube
from lib import manifests
from lib import pabc
from lib import pki
from lib import process
from lib import prune
from lib import seed
from lib import tls
from lib import values
from lib.paths import NAMESPACE
from lib.paths import RELEASE_NAME

STORAGE_HOOKS = "templates/storage-hooks.yaml"
STORAGE_PERMISSIONS_JOB = "storage-permissions-fix"

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
    selected.helm_args += extra
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


def config_jobs(render: manifests.Render) -> list[str]:
    """The setup_configuration Jobs (`<app>-config`) in render."""
    return [
        manifests.name_of(doc)
        for doc in render.docs
        if doc.get("kind") == "Job" and manifests.name_of(doc).endswith("-config")
    ]


def _rerun_config_jobs(render: manifests.Render) -> None:
    """Deletes the config Jobs so the apply recreates and re-runs them.

    As ExternalsPodiumD's pipeline does before every deploy: a Job is
    immutable, and otherwise only re-runs once its TTL has removed it.
    """
    jobs = config_jobs(render)
    if jobs:
        print(f"Deleting {len(jobs)} config Job(s) so this deploy re-runs them...")
        kube.kubectl_shown("delete", "job", *jobs, "-n", NAMESPACE, "--ignore-not-found")


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
    dependency.sync()
    selected = options(full=full, extra=extra)

    print(f"Ensuring namespace '{NAMESPACE}' exists...")
    namespace = kube.kubectl("create", "namespace", NAMESPACE, "--dry-run=client", "-o", "yaml")
    kube.kubectl("apply", "-f", "-", stdin=namespace)

    storage = _apply_storage_hooks(selected)
    if values.monitoring_logging_enabled():
        print("\nApplying monitoring-logging's own CRDs first...")
        crds.apply_monitoring_logging_crds()

    if not pki.CA_CERT.is_file():
        msg = f"no local CA in {pki.PKI_DIR}: run scripts/provision-cluster (it creates the CA and its issuer)"
        raise process.UserError(msg)
    pki.apply_trust(NAMESPACE)
    render = selected.render()
    _rerun_config_jobs(render)
    _apply_full_manifest(render, expected_storage_errors(storage))
    print()
    chart_hosts = hosts.chart_hosts()
    tls.apply_certificate(chart_hosts)
    dns.apply_hosts(chart_hosts)

    print()
    keycloak.sync_zac_pkce(enabled=selected.zac_pkce)
    print("\nApplying pabc-migrations (guarded - see scripts/lib/pabc.py)...")
    pabc.apply_migrations(force=False)
    print("\nPruning Deployments/StatefulSets/DaemonSets/Services/Secrets/Ingresses not part of this render...")
    prune.prune(render.docs, force=force_prune)

    # Live state after pruning decides, so a profile just switched off is not seeded.
    print()
    if kube.exists("deployment/objecten"):
        print("Seeding fixture data (see scripts/lib/seed.py)...")
        seed.seed_fixtures(merged=chart.objecten_shape().merged)
    else:
        print("'objecten' profile not deployed - skipping seeding.")
    print("\nDone. Next: ./scripts/setup-tunnel for external reachability, or run the suite in tests/ to verify.")


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
