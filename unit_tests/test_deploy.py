"""podiumd_minikube.deploy: helm options, apply error counting and argument parsing."""

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
from podiumd_minikube import prune
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME


def test_apply_errors_counts_server_and_client_side_failures():
    output = """\
deployment.apps/zac configured
Error from server (Invalid): error when applying patch: PersistentVolume "x" is invalid
error: resource mapping not found ... ensure CRDs are installed first
service/zac unchanged
"""
    assert deploy.apply_errors(output) == 2


def test_expected_storage_errors_is_one_pv_and_one_pvc_per_app():
    storage = manifests.Render(
        docs=[{"kind": "PersistentVolume"}, {"kind": "PersistentVolumeClaim"}, {"kind": "PersistentVolume"}],
        large_configmaps=[],
    )
    assert deploy.expected_storage_errors(storage) == 4


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
    args, extra = deploy.add_arguments(argparse.ArgumentParser()).parse_known_args(
        ["--set", "a=b", "--full", "--force-prune"]
    )
    assert args.full
    assert args.force_prune
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
    render = deploy.Options(helm_args=["--set", "a=b"]).render("-s", deploy.STORAGE_HOOKS)
    assert fake_run.ran("helm", "template")[0][-6:] == [*deploy.LOOKUP_ARGS, "--set", "a=b", "-s", deploy.STORAGE_HOOKS]
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
STORAGE = manifests.Render(
    docs=[_doc("PersistentVolume", "pv"), _doc("PersistentVolumeClaim", "pvc")], large_configmaps=[]
)
IMMUTABLE = 'Error from server (Invalid): PersistentVolume "pv" is invalid: spec: Forbidden: spec is immutable\n'
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
    (crds, "apply_monitoring_logging_crds"),
    (deployed, "record"),
    (gateway, "apply"),
    (dns, "apply_hosts"),
    (keycloak, "repair_default_client_scopes"),
    (openbao, "bootstrap"),
    (prune, "prune"),
]


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
def called(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, fixed_sets: None, tmp_path: Path, live: dict[str, Any]
) -> list[str]:
    """deploy()'s steps outside this module and its shown kubectl commands, in order.

    The CA exists; the full apply hits the 2 expected storage errors.
    """
    steps: list[str] = []
    for module, name in STEPS:
        monkeypatch.setattr(module, name, lambda *_a, _name=name, **_k: steps.append(_name))
    monkeypatch.setattr(kube, "kubectl_shown", lambda *args, stdin=None: steps.append(" ".join(args)))
    monkeypatch.setattr(deploy.Options, "render", lambda _self, *args, **_kwargs: STORAGE if args else RENDER)
    monkeypatch.setattr(hosts, "chart_hosts", lambda: ["zac.local"])
    monkeypatch.setattr(pki, "CA_CERT", tmp_path / "ca.crt")
    monkeypatch.setattr(crds, "keycloak_version", lambda: None)
    pki.CA_CERT.write_text("")
    fake_run.on("kubectl", "create", "namespace", stdout="kind: Namespace\n")
    fake_run.on("kubectl", "apply", "-n", NAMESPACE, returncode=1, stdout=IMMUTABLE * 2)
    return steps


def _deploy() -> None:
    deploy.deploy(full=False, force_prune=False, extra=[])


def test_deploy_runs_every_step_in_order(fake_run: FakeRun, called: list[str]):
    _deploy()
    storage_job = deploy.STORAGE_PERMISSIONS_JOB
    assert called == [
        "require_minikube_context",
        "check",
        "check",
        "sync",
        "apply_trust",
        f"delete job {storage_job} -n {NAMESPACE} --ignore-not-found",
        f"apply -n {NAMESPACE} -f -",
        f"wait --for=condition=complete job/{storage_job} -n {NAMESPACE} --timeout=60s",
        "migrate_major",
        "rename_databases",
        "create_missing_databases",
        "align_with_azure",
        "rename_rows",
        "apply_monitoring_logging_crds",
        f"delete job openzaak-config -n {NAMESPACE} --ignore-not-found --wait=false",
        "apply --server-side --force-conflicts -f -",
        "wait --for=condition=Established crd/a.x --timeout=60s",
        f"apply --server-side -n {NAMESPACE} -f -",
        "record",
        "apply",
        "apply_hosts",
        "repair_default_client_scopes",
        "bootstrap",
        "prune",
    ]
    assert fake_run.calls == [
        ["kubectl", "create", "namespace", NAMESPACE, "--dry-run=client", "-o", "yaml"],
        ["kubectl", "apply", "-f", "-"],
        ["kubectl", "get", f"job/{storage_job}", "-n", NAMESPACE],
        ["kubectl", "apply", "-n", NAMESPACE, "-f", "-"],
    ]
    assert fake_run.stdins[1] == "kind: Namespace\n"
    assert fake_run.stdins[3] == RENDER.manifest


@pytest.mark.parametrize(("returncode", "said"), [(0, "Applied cleanly."), (1, "expected and HARMLESS")])
def test_deploy_reports_the_apply_outcome(
    fake_run: FakeRun, called: list[str], capsys: pytest.CaptureFixture[str], returncode: int, said: str
):
    fake_run.on("kubectl", "apply", "-n", NAMESPACE, returncode=returncode, stdout=IMMUTABLE * 2 * returncode)
    fake_run.on("kubectl", "get", f"job/{deploy.STORAGE_PERMISSIONS_JOB}", returncode=1)
    _deploy()
    assert said in capsys.readouterr().out
    assert not [step for step in called if step.startswith("wait --for=condition=complete")]


def test_deploy_fails_on_unexpected_apply_errors(fake_run: FakeRun, called: list[str]):
    fake_run.on("kubectl", "apply", "-n", NAMESPACE, returncode=1, stdout=IMMUTABLE * 3)
    with pytest.raises(process.UserError, match=r"^3 apply error\(s\), expected exactly 2 "):
        _deploy()
    assert called[-1] == "wait --for=condition=Established crd/a.x --timeout=60s"


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
    deploy.Options().render("-s", deploy.STORAGE_HOOKS, lookup=False)
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


def test_deploy_restarts_zac_only_after_openzaak_config_completed(
    called: list[str], live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    zac = live["deployment"]["items"][0]
    zac["status"]["readyReplicas"] = 0
    job = live["job"]["items"][0]
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


def test_deploy_wires_the_outway_once_after_openbao_config_completed(
    called: list[str], live: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    config = {"kind": "Job", "metadata": {"name": openbao.CONFIG_JOB}, "status": {}}
    live["job"]["items"].append(config)
    render = manifests.Render(docs=[*RENDER.docs, _doc("Job", openbao.CONFIG_JOB)], large_configmaps=[])
    monkeypatch.setattr(deploy.Options, "render", lambda _self, *args, **_kwargs: STORAGE if args else render)
    events: list[str] = []
    answer = kube.get_json

    def get_json(resource: str, *args: str) -> Any:
        if resource == "job":
            events.append("poll")
            if events.count("poll") == 2:
                config["status"] = _job_status("Complete")["status"]
        return answer(resource, *args)

    monkeypatch.setattr(kube, "get_json", get_json)
    monkeypatch.setattr(openbao, "wire_outway", lambda: events.append("wire"))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    _deploy()
    assert events == ["poll", "poll", "wire"]
