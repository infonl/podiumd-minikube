"""Installs this chart, podiumd and monitoring-logging as Helm releases on the minikube cluster."""

import argparse

from collections.abc import Callable
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


def _apply_crds(render: manifests.Render) -> None:
    """Applies the render's CRDs (the Keycloak operator's from upstream) server-side and waits until established.

    Before the upgrade: Helm installs a chart's crds/ only when absent, and the adfinis subchart's are older.
    """
    if not render.crds:
        return
    docs = crds.with_upstream_keycloak(render.crds, crds.keycloak_version())
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
        charts={key: value for render in renders for key, value in render.charts.items()},
    )


def _upgrade(selected: Options, found: list[releases.Release], renders: dict[str, manifests.Render]) -> list[int]:
    """CRDs, the one-shot Jobs, then each release's `helm upgrade`; returns hold_zac's result."""
    own, podiumd = renders[RELEASE_NAME], renders[releases.PODIUMD]
    monitoring = renders.get(releases.MONITORING)
    if monitoring and crds.needed(monitoring.docs):
        print("\nApplying monitoring-logging's own CRDs first...")
        crds.apply_monitoring_logging_crds()
    _apply_crds(podiumd)
    held = hold_zac(podiumd)
    _delete_jobs(one_shot_jobs(own, podiumd))
    for release in found:
        # This chart first: its pre-install hooks create the PV/PVC pairs podiumd's storage templates look up.
        post_render = releases.post_renderer_args(
            objecten_merged=selected.objecten_merged, hold_zac=bool(held) and release.name == releases.PODIUMD
        )
        releases.upgrade(release, post_render)
    if undeleted := upgrade.undeleted_fixed_jobs(podiumd.docs):
        print(
            f"\nWARNING: fixed-name Jobs ExternalsPodiumD does not delete before helm upgrade: {', '.join(undeleted)}"
        )
    return held


@lock.holding("deploy")
def deploy(*, full: bool, extra: list[str], command: str = "deploy") -> None:
    """Syncs dependencies, then the Helm releases with the steps around them."""
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
    held = _upgrade(selected, found, renders)
    deployed.record(command, render)
    print()
    chart_hosts = hosts.chart_hosts()
    # The Gateway first: NGF creates the Service CoreDNS points at only once the Gateway exists.
    gateway.apply([doc for done in renders.values() for doc in done.docs], chart_hosts)
    dns.apply_hosts(chart_hosts)
    print()
    keycloak.repair_default_client_scopes(render)
    openbao.bootstrap(render)
    _wait_ready(render, held)
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


ZAC = "zac"
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
    """On a fresh install, sets ZAC's replicas to 0 in render; returns the rendered count, else [].

    Its first boot would fail before ZAC_JOBS completed; the post-renderer's --hold-zac installs it
    at 0 and _wait_ready scales it up after them.
    """
    if not set(ZAC_JOBS) & set(render.jobs) or kube.exists(f"deployment/{ZAC}"):
        return []
    zac = next((d for d in render.docs if d.get("kind") == "Deployment" and manifests.name_of(d) == ZAC), None)
    if zac is None:
        return []
    spec = zac["spec"]
    replicas = spec.get("replicas", 1)
    spec["replicas"] = 0
    return [replicas]


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
    # Here, not in openbao.bootstrap: on a fresh install the Job retries until Keycloak
    # answers through the edge (5.5 min seen), and nothing else needs to wait for it.
    unwired = [openbao.CONFIG_JOB] if openbao.CONFIG_JOB in names else []

    def settled() -> tuple[list[str]] | None:
        running, failed = job_state()
        if unwired and openbao.CONFIG_JOB not in running + failed:
            openbao.wire_outway()
            unwired.clear()
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
        "--skip-roles",
        action="store_true",
        help="realm import without roles and groups (chart default; a fresh realm then lacks them)",
    )
    return parser
