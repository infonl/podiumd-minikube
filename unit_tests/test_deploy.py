"""podiumd_minikube.deploy: helm options, the release order and the wait for a settled cluster."""

import argparse
import json

from pathlib import Path
from typing import Any

import pytest

from conftest import FakeRun

from podiumd_minikube import chart
from podiumd_minikube import crds
from podiumd_minikube import dependency
from podiumd_minikube import deploy
from podiumd_minikube import deployed
from podiumd_minikube import disk
from podiumd_minikube import dns
from podiumd_minikube import gateway
from podiumd_minikube import hosts
from podiumd_minikube import keycloak
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import memory
from podiumd_minikube import openbao
from podiumd_minikube import pki
from podiumd_minikube import postgres
from podiumd_minikube import process
from podiumd_minikube import releases
from podiumd_minikube import tunnel
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import OLD_NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME

SEAL = ["--set-file", "seal=key"]


@pytest.fixture
def fixed_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chart, "bundled_image_sets", list)
    monkeypatch.setattr(openbao, "seal_key_args", lambda: SEAL)


def test_options_full_adds_profiles_shape_and_extra_args(monkeypatch: pytest.MonkeyPatch, fixed_sets: None):
    monkeypatch.setattr(chart, "objecten_shape", lambda: chart.ObjectenShape(merged=True, sets=["--set", "shape=1"]))
    selected = deploy.options(full=True, extra=["--set", "x=y"])
    assert selected.objecten_merged
    assert selected.helm_args == [*chart.FULL_PROFILE_SETS, "--set", "shape=1", *SEAL, "--set", "x=y"]


def test_options_core_has_no_profile_sets(fixed_sets: None):
    selected = deploy.options(full=False, extra=[])
    assert not selected.objecten_merged
    assert selected.helm_args == SEAL


def test_parser_passes_unknown_arguments_to_helm():
    args, extra = deploy.add_arguments(argparse.ArgumentParser()).parse_known_args(["--set", "a=b", "--full"])
    assert args.full
    assert extra == ["--set", "a=b"]


def test_jobs_are_every_job_in_the_render():
    render = manifests.Render(
        docs=[
            {"kind": "Job", "metadata": {"name": "openzaak-config"}},
            {"kind": "Job", "metadata": {"name": "pabc-migrations-1"}},
            {"kind": "ConfigMap", "metadata": {"name": "objecten-config"}},
        ],
        large_configmaps=[],
    )
    assert render.jobs == ["openzaak-config", "pabc-migrations-1"]


def test_unfinished_jobs_splits_running_and_failed():
    def job(name: str, condition: str | None) -> dict[str, object]:
        conditions = [{"type": condition, "status": "True"}] if condition else []
        return {"metadata": {"name": name}, "status": {"conditions": conditions}}

    jobs = [job("done", "Complete"), job("broken", "Failed"), job("busy", None)]
    names = ["done", "broken", "busy", "missing"]
    assert deploy.unfinished_jobs(jobs, names) == (["busy", "missing"], ["broken"])


def test_rolled_out_needs_current_generation_and_ready_replicas():
    def workload(generation: int, observed: int, updated: int, ready: int, pods: int = 1) -> dict[str, object]:
        status = {"observedGeneration": observed, "replicas": pods, "updatedReplicas": updated, "readyReplicas": ready}
        return {"metadata": {"generation": generation}, "spec": {"replicas": 1}, "status": status}

    assert deploy.rolled_out(workload(2, 2, 1, 1))
    assert not deploy.rolled_out(workload(2, 1, 1, 1))
    assert not deploy.rolled_out(workload(2, 2, 1, 0))
    # The old pod is ready, the new one not yet.
    assert not deploy.rolled_out(workload(2, 2, 1, 1, pods=2))


def test_rolled_out_reads_ecks_own_status_for_elasticsearch():
    def elasticsearch(observed: int, health: str, phase: str) -> dict[str, object]:
        status = {"observedGeneration": observed, "health": health, "phase": phase}
        return {"kind": "Elasticsearch", "metadata": {"generation": 2}, "status": status}

    assert deploy.rolled_out(elasticsearch(2, "yellow", "Ready"))
    assert not deploy.rolled_out(elasticsearch(2, "green", "ApplyingChanges"))
    assert not deploy.rolled_out(elasticsearch(2, "unknown", "Ready"))
    assert not deploy.rolled_out(elasticsearch(1, "green", "Ready"))


def _doc(kind: str, name: str) -> dict[str, Any]:
    return {"kind": kind, "metadata": {"name": name}}


def test_render_passes_helm_args_and_splits_out_crds(fake_run: FakeRun):
    crd, configmap = _doc("CustomResourceDefinition", "a.x"), _doc("ConfigMap", "c")
    fake_run.on("helm", "template", stdout=manifests.dump([crd, configmap]))
    render = deploy.Options(helm_args=["--set", "a=b"]).render("-s", "templates/x.yaml")
    assert fake_run.ran("helm", "template")[0][-6:] == [*deploy.LOOKUP_ARGS, "--set", "a=b", "-s", "templates/x.yaml"]
    assert ([manifests.name_of(doc) for doc in render.docs], render.crds[0]["kind"]) == (["c"], crd["kind"])


CRD = _doc("CustomResourceDefinition", "a.x")
RENDER = manifests.Render(
    docs=[
        {"apiVersion": "monitoring.coreos.com/v1", **_doc("ServiceMonitor", "m")},
        _doc("Deployment", "zac"),
        _doc("Job", "openzaak-config"),
    ],
    large_configmaps=[_doc("ConfigMap", "dashboards")],
    crds=[CRD],
)
OWN = manifests.Render(docs=[_doc("ConfigMap", "postgres-init")], large_configmaps=[])
RELEASES = [
    releases.Release(RELEASE_NAME, NAMESPACE, CHART_DIR, {}),
    releases.Release(releases.PODIUMD, NAMESPACE, CHART_DIR, {}),
]
STEPS = [
    (kube, "require_minikube_context"),
    (disk, "check"),
    (memory, "check"),
    (dependency, "sync"),
    (pki, "apply_trust"),
    (postgres, "migrate_major"),
    (postgres, "rename_databases"),
    (postgres, "create_missing_databases"),
    (postgres, "align_with_azure"),
    (postgres, "rename_rows"),
    (gateway, "apply"),
    (dns, "apply_hosts"),
    (deployed, "record"),
    (keycloak, "repair_default_client_scopes"),
    (openbao, "activate"),
    (tunnel, "setup_tunnel"),
]


def test_apply_crds_gives_templated_crds_the_releases_ownership(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(crds, "keycloak_version", lambda: None)
    definitions = [CRD, _doc("CustomResourceDefinition", "b.x")]
    render = manifests.Render(docs=[], large_configmaps=[], crds=definitions, templated_crds=["b.x"])
    deploy.apply_crds(render, releases.PODIUMD)
    applied = {manifests.name_of(doc): doc["metadata"] for doc in manifests.documents(fake_run.stdins[0] or "")}
    assert "labels" not in applied["a.x"]
    assert applied["b.x"]["labels"] == {"app.kubernetes.io/managed-by": "Helm"}
    assert applied["b.x"]["annotations"] == {
        "meta.helm.sh/release-name": releases.PODIUMD,
        "meta.helm.sh/release-namespace": NAMESPACE,
    }


def _job_status(condition: str) -> dict[str, Any]:
    return {**_doc("Job", "openzaak-config"), "status": {"conditions": [{"type": condition, "status": "True"}]}}


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """`kubectl get -o json` answers by resource: zac rolled out, the Job complete."""
    status = {"observedGeneration": 1, "replicas": 1, "updatedReplicas": 1, "readyReplicas": 1}
    answers: dict[str, Any] = {
        "deployment": {
            "items": [{"kind": "Deployment", "metadata": {"name": "zac", "generation": 1}, "status": status}]
        },
        "job": {"items": [_job_status("Complete")]},
    }
    monkeypatch.setattr(kube, "get_json", lambda resource, *_args: answers[resource])
    return answers


@pytest.fixture
def renders(monkeypatch: pytest.MonkeyPatch) -> dict[str, manifests.Render]:
    """What Options.render_release and releases.installed return per release; a test replaces podiumd's."""
    found = {RELEASE_NAME: OWN, releases.PODIUMD: RENDER}
    monkeypatch.setattr(releases, "releases", lambda _args: RELEASES)
    monkeypatch.setattr(deploy.Options, "render_release", lambda _self, release, **_kwargs: found[release.name])
    monkeypatch.setattr(releases, "installed", lambda release: found[release.name])
    return found


@pytest.fixture
def called(
    fake_run: FakeRun,
    monkeypatch: pytest.MonkeyPatch,
    fixed_sets: None,
    tmp_path: Path,
    live: dict[str, Any],
    renders: dict[str, manifests.Render],
) -> list[str]:
    """deploy()'s steps outside this module, its shown kubectl commands and its upgrades, in order; the CA exists."""
    steps: list[str] = []
    for module, name in STEPS:
        monkeypatch.setattr(module, name, lambda *_a, _name=name, **_k: steps.append(_name))
    monkeypatch.setattr(kube, "kubectl_shown", lambda *args, stdin=None: steps.append(" ".join(args)))
    monkeypatch.setattr(releases, "upgrade", lambda release, post: steps.append(f"upgrade {release.name} {post[-1]}"))
    monkeypatch.setattr(hosts, "chart_hosts", lambda: ["zac.local"])
    monkeypatch.setattr(pki, "CA_CERT", tmp_path / "ca.crt")
    monkeypatch.setattr(crds, "keycloak_version", lambda: None)
    monkeypatch.setattr(deployed, "last_digests", dict)
    monkeypatch.setattr(releases, "is_deployed", lambda _release: True)
    monkeypatch.setattr(openbao, "unsealed", lambda: False)
    pki.CA_CERT.write_text("")
    fake_run.on("kubectl", "get", f"namespace/{OLD_NAMESPACE}", returncode=1)
    fake_run.on("kubectl", "create", "namespace", stdout="kind: Namespace\n")
    return steps


def _deploy() -> None:
    deploy.deploy(full=False, extra=[])


def test_deploy_runs_every_step_in_order(fake_run: FakeRun, called: list[str]):
    _deploy()
    assert called == [
        "require_minikube_context",
        "check",
        "check",
        "sync",
        "apply_trust",
        "migrate_major",
        "rename_databases",
        "create_missing_databases",
        "align_with_azure",
        "rename_rows",
        "apply",
        "apply_hosts",
        "apply --server-side --force-conflicts -f -",
        "wait --for=condition=Established crd/a.x --timeout=60s",
        f"delete job openzaak-config -n {NAMESPACE} --ignore-not-found --wait=false",
        f"upgrade {RELEASE_NAME} --ca-trust",
        f"upgrade {releases.PODIUMD} --ca-trust",
        "record",
        "repair_default_client_scopes",
        "activate",
        "setup_tunnel",
    ]
    assert fake_run.calls == [
        ["kubectl", "get", f"namespace/{OLD_NAMESPACE}", "-n", NAMESPACE],
        ["kubectl", "create", "namespace", NAMESPACE, "--dry-run=client", "-o", "yaml"],
        ["kubectl", "apply", "-f", "-"],
        ["kubectl", "get", "deployment/zac", "-n", NAMESPACE],
    ]
    assert fake_run.stdins[2] == "kind: Namespace\n"


def test_deploy_refuses_the_namespace_of_the_kubectl_apply_deploys(fake_run: FakeRun, called: list[str]):
    fake_run.on("kubectl", "get", f"namespace/{OLD_NAMESPACE}")
    with pytest.raises(process.UserError, match=r"reset-namespace"):
        _deploy()
    assert "apply_trust" not in called


def test_deploy_fails_on_a_failed_job(called: list[str], live: dict[str, Any]):
    live["job"]["items"] = [_job_status("Failed")]
    with pytest.raises(process.UserError, match=r"^Job\(s\) failed: openzaak-config: "):
        _deploy()


def test_deploy_names_stuck_workloads_and_running_jobs_on_timeout(
    called: list[str], live: dict[str, Any], poll_once: None
):
    live["job"]["items"] = []
    live["deployment"]["items"][0]["status"]["readyReplicas"] = 0
    with pytest.raises(process.UserError, match=r"not rolled out: deployment/zac; running: openzaak-config$"):
        _deploy()


def test_deploy_names_a_held_zac_on_timeout(
    fake_run: FakeRun, called: list[str], live: dict[str, Any], poll_once: None, renders: dict[str, manifests.Render]
):
    zac = {**_doc("Deployment", "zac"), "spec": {"replicas": 1}}
    renders[releases.PODIUMD] = manifests.Render(docs=[zac, _doc("Job", "openzaak-config")], large_configmaps=[])
    fake_run.on("kubectl", "get", "deployment/zac", returncode=1)
    live["job"]["items"] = []
    with pytest.raises(process.UserError, match=r"running: openzaak-config; deployment/zac still at 0 replicas"):
        _deploy()


def test_hold_zac_leaves_an_existing_or_missing_zac_alone(fake_run: FakeRun):
    render = manifests.Render(docs=[_doc("Job", "openzaak-config")], large_configmaps=[])
    fake_run.on("kubectl", "get", "deployment/zac", returncode=1)
    assert deploy.hold_zac(render) == []
    zac = {**_doc("Deployment", "zac"), "spec": {"replicas": 1}}
    render = manifests.Render(docs=[zac, _doc("Job", "openzaak-config")], large_configmaps=[])
    fake_run.on("kubectl", "get", "deployment/zac")
    assert (deploy.hold_zac(render), zac["spec"]["replicas"]) == ([], 1)


def test_deploy_needs_the_local_ca(called: list[str]):
    pki.CA_CERT.unlink()
    with pytest.raises(process.UserError, match=r"^no local CA"):
        _deploy()
    assert "apply_trust" not in called


def test_render_falls_back_to_plain_helm_template_without_the_crds(fake_run: FakeRun):
    configmap = _doc("ConfigMap", "c")
    fake_run.on("helm", "template", stdout=manifests.dump([configmap]))
    server = ("helm", "template", RELEASE_NAME, str(CHART_DIR), "-n", NAMESPACE, "--include-crds", *deploy.LOOKUP_ARGS)
    fake_run.on(*server, returncode=1, stderr="no matches for kind; ensure CRDs are installed first")
    assert [manifests.name_of(doc) for doc in deploy.Options().render().docs] == [manifests.name_of(configmap)]
    calls = fake_run.ran("helm", "template")
    assert "--dry-run=server" in calls[0]
    assert "--dry-run=server" not in calls[-1]


def test_render_without_lookup_runs_one_plain_helm_template(fake_run: FakeRun):
    fake_run.on("helm", "template", stdout=manifests.dump([_doc("ConfigMap", "c")]))
    deploy.Options().render("-s", "templates/x.yaml", lookup=False)
    calls = fake_run.ran("helm", "template")
    assert len(calls) == 1
    assert "--dry-run=server" not in calls[0]


def test_deploy_names_a_workload_not_created_yet_as_stuck(called: list[str], live: dict[str, Any], poll_once: None):
    live["deployment"]["items"] = []
    with pytest.raises(process.UserError, match=r"not rolled out: deployment/zac;"):
        _deploy()


@pytest.mark.parametrize(
    ("ready", "log", "state"),
    [(1, "", "ready"), (0, "WFLYCTL0080: Failed services", "failed"), (0, "WFLYSRV0049: starting", None)],
)
def test_zac_state(fake_run: FakeRun, ready: int, log: str, state: str | None):
    status = {"observedGeneration": 1, "replicas": 1, "updatedReplicas": 1, "readyReplicas": ready}
    zac = {"kind": "Deployment", "metadata": {"name": "zac", "generation": 1}, "status": status}
    fake_run.on("kubectl", "get", "deployment/zac", stdout=json.dumps(zac))
    fake_run.on("kubectl", "logs", stdout=log)
    assert deploy.zac_state() == state


def test_deploy_restarts_zac_once_when_its_boot_failed(
    called: list[str], live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    zac = live["deployment"]["items"][0]
    zac["status"]["readyReplicas"] = 0
    restarts: list[str] = []

    def restart_zac() -> None:
        restarts.append("zac")
        zac["status"]["readyReplicas"] = 1

    monkeypatch.setattr(deploy, "zac_boot_failed", lambda: True)
    monkeypatch.setattr(deploy, "restart_zac", restart_zac)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    _deploy()
    assert restarts == ["zac"]


@pytest.mark.parametrize("name", deploy.ZAC_JOBS)
def test_deploy_restarts_zac_only_after_its_jobs_completed(
    called: list[str],
    live: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    renders: dict[str, manifests.Render],
    name: str,
):
    zac = live["deployment"]["items"][0]
    zac["status"]["readyReplicas"] = 0
    jobs = {manifests.name_of(item): item for item in live["job"]["items"]}
    if name not in jobs:
        jobs[name] = {**_job_status("Complete"), "metadata": {"name": name}}
        live["job"]["items"].append(jobs[name])
        renders[releases.PODIUMD] = manifests.Render(docs=[*RENDER.docs, _doc("Job", name)], large_configmaps=[])
    job = jobs[name]
    complete, job["status"] = job["status"], {}
    events: list[str] = []
    answer = kube.get_json

    def get_json(resource: str, *args: str) -> Any:
        if resource == "job":
            events.append("poll")
            if events.count("poll") == 3:
                job["status"] = complete
        return answer(resource, *args)

    def restart_zac() -> None:
        events.append("restart")
        zac["status"]["readyReplicas"] = 1

    monkeypatch.setattr(kube, "get_json", get_json)
    monkeypatch.setattr(deploy, "zac_boot_failed", lambda: True)
    monkeypatch.setattr(deploy, "restart_zac", restart_zac)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    _deploy()
    assert events.index("restart") == 3


def test_deploy_starts_zac_on_a_fresh_install_only_after_its_jobs_finished(
    fake_run: FakeRun,
    called: list[str],
    live: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    renders: dict[str, manifests.Render],
):
    zac = {**_doc("Deployment", "zac"), "spec": {"replicas": 1}}
    renders[releases.PODIUMD] = manifests.Render(docs=[zac, _doc("Job", "openzaak-config")], large_configmaps=[])
    fake_run.on("kubectl", "get", "deployment/zac", returncode=1)
    job = live["job"]["items"][0]
    complete, job["status"] = job["status"], {}
    answer = kube.get_json

    def get_json(resource: str, *args: str) -> Any:
        if resource == "job":
            called.append("poll")
            if called.count("poll") == 2:
                job["status"] = complete
        return answer(resource, *args)

    monkeypatch.setattr(kube, "get_json", get_json)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    _deploy()
    assert f"upgrade {releases.PODIUMD} --hold-zac" in called
    scale = f"scale deployment/zac --replicas=1 -n {NAMESPACE}"
    assert called[called.index("activate") + 1 :][:3] == ["poll", "poll", scale]


def test_deploy_upgrades_only_releases_whose_render_changed(called: list[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(releases, "digest", lambda render, _post: "own" if render is OWN else "podiumd-new")
    monkeypatch.setattr(deployed, "last_digests", lambda: {RELEASE_NAME: "own", releases.PODIUMD: "podiumd-old"})
    _deploy()
    assert [step for step in called if step.startswith("upgrade")] == [f"upgrade {releases.PODIUMD} --ca-trust"]
    assert f"delete job openzaak-config -n {NAMESPACE} --ignore-not-found --wait=false" in called


def test_deploy_skips_unchanged_releases_and_an_unsealed_vaults_activation(
    called: list[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(releases, "digest", lambda _render, _post: "same")
    monkeypatch.setattr(deployed, "last_digests", lambda: {RELEASE_NAME: "same", releases.PODIUMD: "same"})
    monkeypatch.setattr(openbao, "unsealed", lambda: True)
    _deploy()
    assert not [step for step in called if step.startswith(("upgrade", "delete", "apply --server-side"))]
    assert "activate" not in called
    assert "record" in called


def test_deploy_every_release_upgrades_unchanged_ones(called: list[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(releases, "digest", lambda _render, _post: "same")
    monkeypatch.setattr(deployed, "last_digests", lambda: {RELEASE_NAME: "same", releases.PODIUMD: "same"})
    deploy.deploy(full=False, extra=[], every=True)
    assert [step for step in called if step.startswith("upgrade")] == [
        f"upgrade {RELEASE_NAME} --ca-trust",
        f"upgrade {releases.PODIUMD} --ca-trust",
    ]


def test_deploy_upgrades_a_release_without_a_deployed_revision(called: list[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(releases, "digest", lambda _render, _post: "same")
    monkeypatch.setattr(deployed, "last_digests", lambda: {RELEASE_NAME: "same", releases.PODIUMD: "same"})
    monkeypatch.setattr(releases, "is_deployed", lambda release: release.name == RELEASE_NAME)
    _deploy()
    assert [step for step in called if step.startswith("upgrade")] == [f"upgrade {releases.PODIUMD} --ca-trust"]
