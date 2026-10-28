"""Installs this chart, podiumd and monitoring-logging as Helm releases on the minikube cluster."""

import argparse

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from podiumd_minikube import chart
from podiumd_minikube import crds
from podiumd_minikube import dependency
from podiumd_minikube import deployed
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
from podiumd_minikube import releases
from podiumd_minikube import tunnel
from podiumd_minikube import upgrade
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import OLD_NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME

# templates/storage-hooks.yaml's chmod of the PersistentVolumes.
STORAGE_PERMISSIONS_JOB = "storage-permissions-fix"
# Rollouts and config Jobs of a --full deploy, Elasticsearch included.
READY_TIMEOUT = 900

# As `helm upgrade`: the chart's `lookup` reads the live cluster (a plain `helm template`
# gets nothing, so e.g. Grafana's admin password changed on every deploy).
# --take-ownership skips Helm's check that the release owns the live objects; a dry run writes nothing.
LOOKUP_ARGS = ["--dry-run=server", "--take-ownership"]
# A server-side dry run also maps every kind; before the operators' CRDs exist (a fresh
# cluster, where lookup finds nothing anyway) the render falls back to plain helm template.
_CRDS_MISSING = "ensure CRDs are installed first"


@dataclass
class Options:
    """What to render: helm --set flags and the fixups they imply."""

    helm_args: list[str] = field(default_factory=list[str])
    objecten_merged: bool = False

    def render(self, *args: str, lookup: bool = True) -> manifests.Render:
        """`helm template` of the umbrella chart with these options and args, fixed up (hosts, images, tests)."""
        return self._fixed_up(lambda *extra: manifests.helm_template(*extra, *self.helm_args, *args), lookup=lookup)

    def render_release(self, release: releases.Release, *, lookup: bool = True) -> manifests.Render:
        """release as its `helm upgrade` installs it; each object carries the release's namespace."""
        render = self._fixed_up(lambda *extra: releases.template(release, *extra), lookup=lookup)
        for doc in [*render.docs, *render.large_configmaps]:
            manifests.section(doc, "metadata").setdefault("namespace", release.namespace)
        return render

    def _fixed_up(self, template: Callable[..., str], *, lookup: bool) -> manifests.Render:
        text = None
        if lookup:
            try:
                text = template(*LOOKUP_ARGS)
            except process.ProcessError as error:
                if _CRDS_MISSING not in error.stderr:
                    raise
        if text is None:
            text = template()
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


def _delete_jobs(names: list[str]) -> None:
    """Deletes Jobs so the next `helm upgrade` recreates and re-runs them (a Job's pod template is immutable)."""
    if names:
        print(f"Deleting {len(names)} Job(s) so this deploy re-runs them...")
        # No wait: Jobs carry no finalizers, so each is gone before the upgrade (waiting took 5 s for 37).
        kube.kubectl_shown("delete", "job", *names, "-n", NAMESPACE, "--ignore-not-found", "--wait=false")


def one_shot_jobs(own: manifests.Render, podiumd: manifests.Render) -> list[str]:
    """The Jobs deleted before the upgrades: ExternalsPodiumD's list among podiumd's, and all of this chart's."""
    return [*own.jobs, *(name for name in podiumd.jobs if name in upgrade.EXTERNALS_DELETED_JOBS)]


def apply_crds(render: manifests.Render, release: str) -> None:
    """Applies the render's CRDs (the Keycloak operator's from upstream) server-side and waits until established.

    Before the upgrade: Helm installs a chart's crds/ only when absent, and the adfinis subchart's are older;
    a templated CRD must exist before Helm can map the objects of its kind. Those get release's ownership
    metadata, else Helm refuses to adopt them.
    """
    if not render.crds:
        return
    docs = crds.with_upstream_keycloak(render.crds, crds.keycloak_version())
    for doc in docs:
        if manifests.name_of(doc) in render.templated_crds:
            metadata = manifests.section(doc, "metadata")
            metadata["labels"] = {**manifests.section(metadata, "labels"), "app.kubernetes.io/managed-by": "Helm"}
            metadata["annotations"] = {
                **manifests.section(metadata, "annotations"),
                "meta.helm.sh/release-name": release,
                "meta.helm.sh/release-namespace": NAMESPACE,
            }
    print(f"\nApplying {len(docs)} CRD(s) first...")
    kube.kubectl_shown("apply", "--server-side", "--force-conflicts", "-f", "-", stdin=manifests.dump(docs))
    names = [f"crd/{manifests.name_of(doc)}" for doc in docs]
    kube.kubectl_shown("wait", "--for=condition=Established", *names, "--timeout=60s")


def _refuse_old_namespace() -> None:
    """UserError while the namespace of the kubectl-apply deploys still exists."""
    if kube.exists(f"namespace/{OLD_NAMESPACE}"):
        msg = (
            f"namespace {OLD_NAMESPACE} is from before the Helm releases (now namespace {NAMESPACE}): "
            "run podiumd-minikube reset-namespace, then deploy"
        )
        raise process.UserError(msg)


def _ensure_namespace(namespace: str) -> None:
    """Creates namespace with the local CA's ConfigMap, which every pod mounts."""
    print(f"Ensuring namespace '{namespace}' exists...")
    kube.kubectl(
        "apply", "-f", "-", stdin=kube.kubectl("create", "namespace", namespace, "--dry-run=client", "-o", "yaml")
    )
    pki.apply_trust(namespace)


def combined(renders: list[manifests.Render]) -> manifests.Render:
    """One Render of several releases' renders."""
    return manifests.Render(
        docs=[doc for render in renders for doc in [*render.docs, *render.large_configmaps]],
        large_configmaps=[],
        crds=[doc for render in renders for doc in render.crds],
        templated_crds=[name for render in renders for name in render.templated_crds],
        charts={key: value for render in renders for key, value in render.charts.items()},
    )


def _prepare(names: list[str], renders: dict[str, manifests.Render]) -> None:
    """Before the upgrades of names: their CRDs, and the one-shot Jobs they re-run."""
    own, podiumd = renders[RELEASE_NAME], renders[releases.PODIUMD]
    monitoring = renders.get(releases.MONITORING)
    if monitoring and releases.MONITORING in names and crds.needed(monitoring.docs):
        print("\nApplying monitoring-logging's own CRDs first...")
        crds.apply_monitoring_logging_crds()
    if releases.PODIUMD in names:
        apply_crds(podiumd, releases.PODIUMD)
    empty = manifests.Render(docs=[], large_configmaps=[])
    _delete_jobs(
        one_shot_jobs(own if RELEASE_NAME in names else empty, podiumd if releases.PODIUMD in names else empty)
    )


def _changed(found: list[releases.Release], digests: dict[str, str], *, every: bool) -> list[releases.Release]:
    """The releases of found to upgrade: digest changed since the last deploy, or no deployed revision (every: all)."""
    last = deployed.last_digests()
    changed = [
        release
        for release in found
        if every or last.get(release.name) != digests[release.name] or not releases.is_deployed(release)
    ]
    for release in found:
        if release not in changed:
            print(
                f"\n{release.name}: render unchanged since the last deploy; not upgraded (deploy --every-release does)"
            )
    return changed


@dataclass
class Upgraded:
    """What _upgrade did: hold_zac's result, the releases it upgraded, and every release's digest."""

    held: list[int]
    names: list[str]
    digests: dict[str, str]


def _upgrade(
    selected: Options, found: list[releases.Release], renders: dict[str, manifests.Render], *, every: bool
) -> Upgraded:
    """CRDs, the one-shot Jobs, then the `helm upgrade` of each release whose digest changed (every: all).

    This chart's release first: it creates the PV/PVC pairs podiumd's storage templates look up. podiumd and
    monitoring-logging then together: separate namespaces, as ExternalsPodiumD's separate pipelines.
    """
    held = hold_zac(renders[releases.PODIUMD])
    post = {
        release.name: releases.post_renderer_args(
            objecten_merged=selected.objecten_merged, hold_zac=bool(held) and release.name == releases.PODIUMD
        )
        for release in found
    }
    digests = {release.name: releases.digest(renders[release.name], post[release.name]) for release in found}
    changed = _changed(found, digests, every=every)
    names = [release.name for release in changed]
    _prepare(names, renders)
    if own_release := next((release for release in changed if release.name == RELEASE_NAME), None):
        releases.upgrade(own_release, post[RELEASE_NAME])
        if STORAGE_PERMISSIONS_JOB in renders[RELEASE_NAME].jobs:
            # Open Formulieren (UID 1000) writes to its volume at its first start.
            print(f"Waiting for the {STORAGE_PERMISSIONS_JOB} Job to complete...")
            kube.kubectl_shown(
                "wait", "--for=condition=complete", f"job/{STORAGE_PERMISSIONS_JOB}", "-n", NAMESPACE, "--timeout=60s"
            )
    with ThreadPoolExecutor(max_workers=len(found)) as pool:
        upgrades = [pool.submit(releases.upgrade, r, post[r.name]) for r in changed if r.name != RELEASE_NAME]
        for done in upgrades:
            done.result()
    if releases.PODIUMD in names and (undeleted := upgrade.undeleted_fixed_jobs(renders[releases.PODIUMD].docs)):
        print(
            f"\nWARNING: fixed-name Jobs ExternalsPodiumD does not delete before helm upgrade: {', '.join(undeleted)}"
        )
    return Upgraded(held, names, digests)


@lock.holding("deploy")
def deploy(*, full: bool, extra: list[str], command: str = "deploy", every: bool = False) -> None:
    """Syncs dependencies, then the Helm releases with the steps around them; every: also unchanged releases."""
    kube.require_minikube_context()
    disk.check(disk.DEPLOY)
    memory.check(full=full)
    dependency.sync()
    selected = options(full=full, extra=extra)
    _refuse_old_namespace()
    if not pki.CA_CERT.is_file():
        msg = f"no local CA in {pki.PKI_DIR}: run podiumd-minikube provision-cluster (it creates the CA and its issuer)"
        raise process.UserError(msg)

    found = releases.releases(selected.helm_args)
    for namespace in dict.fromkeys(release.namespace for release in found):
        _ensure_namespace(namespace)
    renders = {release.name: selected.render_release(release) for release in found}
    # The podiumd namespace's objects; monitoring-logging's `helm upgrade --wait` covers its own.
    render = combined([renders[RELEASE_NAME], renders[releases.PODIUMD]])
    postgres.migrate_major(render)
    postgres.rename_databases()
    postgres.create_missing_databases()
    postgres.align_with_azure()
    postgres.rename_rows()
    print()
    chart_hosts = hosts.chart_hosts()
    # Before the upgrades: podiumd's hook Jobs reach Keycloak by its host, and Helm waits for them.
    # The Gateway first: NGF creates the Service CoreDNS points at only once the Gateway exists.
    gateway.apply([doc for done in renders.values() for doc in done.docs], chart_hosts)
    dns.apply_hosts(chart_hosts)
    upgraded = _upgrade(selected, found, renders, every=every)
    # What Helm installed: the template render has revision 1's Job names and hook Jobs Helm deleted.
    installed = {release.name: releases.installed(release) for release in found}
    live = combined([installed[RELEASE_NAME], installed[releases.PODIUMD]])
    deployed.record(command, combined(list(installed.values())), upgraded.digests)
    print()
    keycloak.repair_default_client_scopes(render)
    # As podiumd-infra: an unchanged podiumd release on an unsealed vault needs no activation.
    if releases.PODIUMD in upgraded.names or not openbao.unsealed():
        openbao.activate(render)
    _wait_ready(live, upgraded.held)
    print("\nDeployed.")
    tunnel.setup_tunnel(ask=False)


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


ZAC = manifests.ZAC_DEPLOYMENT
# WildFly gives up on ZAC's deployment when Open Zaak answers 502 (still starting) or 403 (a fresh
# install, before openzaak-config) during its boot, and the liveness probe restarts it only after 16 x 30 s.
# Known issues zac-8 and podiumd-chart-30 (fix: an init container); remove hold_zac and restart_zac with it.
BOOT_FAILED = "WFLYCTL0080"
# Jobs that create what ZAC's boot reads: its Open Zaak authorisations and the ALG catalogus;
# a start or restart before they completed fails.
ZAC_JOBS = ("openzaak-config", "create-required-catalogi-job")


def zac_boot_failed() -> bool:
    """Whether ZAC's log shows WildFly gave up on its deployment."""
    result = process.run(["kubectl", "logs", "-n", NAMESPACE, f"deployment/{ZAC}", "-c", ZAC], check=False)
    return BOOT_FAILED in result.stdout


def zac_state() -> str | None:
    """ "ready", "failed" (its last boot failed) or None while it boots."""
    if rolled_out(kube.get_json(f"deployment/{ZAC}", "-n", NAMESPACE)):
        return "ready"
    return "failed" if zac_boot_failed() else None


def hold_zac(render: manifests.Render) -> list[int]:
    """On a fresh install, ZAC's rendered replica count, else [].

    Its first boot would fail before ZAC_JOBS completed; the post-renderer's --hold-zac installs it
    at 0 and _wait_ready scales it up after them.
    """
    if not set(ZAC_JOBS) & set(render.jobs) or kube.exists(f"deployment/{ZAC}"):
        return []
    zac = next((d for d in render.docs if d.get("kind") == "Deployment" and manifests.name_of(d) == ZAC), None)
    return [] if zac is None else [zac["spec"].get("replicas", 1)]


def restart_zac() -> None:
    """Restarts ZAC after a failed boot."""
    print("ZAC's boot failed while Open Zaak was not ready for it; restarting it.")
    kube.kubectl_shown("rollout", "restart", f"deployment/{ZAC}", "-n", NAMESPACE)


def _wait_ready(render: manifests.Render, held: list[int]) -> None:
    """Waits until the render's workloads have rolled out and its Jobs have completed; UserError on failed Jobs.

    held: ZAC's replicas from hold_zac, set once ZAC_JOBS have finished.
    """
    print("\nWaiting until every rendered workload has rolled out and every Job has completed...")
    workloads = [
        f"{doc['kind'].lower()}/{manifests.name_of(doc)}"
        for doc in render.docs
        if doc.get("kind") in ("Deployment", "StatefulSet", *manifests.ECK_KINDS)
    ]
    kinds = ",".join(sorted({workload.split("/")[0] for workload in workloads}))
    names = render.jobs

    def job_state() -> tuple[list[str], list[str]]:
        return unfinished_jobs(kube.get_json("job", "-n", NAMESPACE)["items"], names)

    def stuck() -> list[str]:
        # One kubectl call per poll: one per workload took 2.4 s with --full.
        found: list[dict[str, Any]] = kube.get_json(kinds, "-n", NAMESPACE)["items"] if kinds else []
        live = {f"{item['kind'].lower()}/{manifests.name_of(item)}": item for item in found}
        return [w for w in workloads if w not in live or not rolled_out(live[w])]

    # Not `rollout status`: it gives up at the progress deadline (600s), which a
    # first deploy passes while pods wait for Elasticsearch, yet still converge.
    restarted: list[str] = []

    def settled() -> tuple[list[str]] | None:
        running, failed = job_state()
        prepared = not set(ZAC_JOBS) & set(running)
        if held and prepared:
            print("ZAC's Jobs have finished; starting ZAC.")
            kube.kubectl_shown("scale", f"deployment/{ZAC}", f"--replicas={held.pop()}", "-n", NAMESPACE)
        waiting = stuck()
        if f"deployment/{ZAC}" in waiting and prepared and not restarted and zac_boot_failed():
            restart_zac()
            restarted.append(ZAC)
        return None if running or waiting else (failed,)

    result = polling.wait_until(settled, timeout=READY_TIMEOUT, interval=10)
    if result is None:
        running, _ = job_state()
        msg = (
            f"after {READY_TIMEOUT}s: not rolled out: {', '.join(stuck()) or '-'}; running: {', '.join(running) or '-'}"
        )
        if held:
            msg += f"; deployment/{ZAC} still at 0 replicas: the next deploy starts it"
        raise process.UserError(msg)
    if result[0]:
        msg = f"Job(s) failed: {', '.join(result[0])}: check `kubectl logs job/<name> -n {NAMESPACE}`"
        raise process.UserError(msg)
    print("All workloads rolled out, all Jobs completed.")


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Adds deploy's arguments to parser; unknown ones (--set x=y) go to helm."""
    parser.add_argument("--full", action="store_true", help="enable every optional profile")
    parser.add_argument(
        "--otel",
        action="store_true",
        help="apps send OpenTelemetry metrics (with the metrics profile); ~50 MiB per web pod",
    )
    parser.add_argument(
        "--every-release",
        action="store_true",
        help="also upgrade a release whose render is unchanged since the last deploy",
    )
    parser.add_argument(
        "--skip-roles",
        action="store_true",
        help="realm import without roles and groups (chart default; a fresh realm then lacks them)",
    )
    return parser
