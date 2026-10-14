"""podiumd_minikube.deploy: helm options, apply error counting and argument parsing."""

import argparse

import pytest

from podiumd_minikube import chart
from podiumd_minikube import deploy
from podiumd_minikube import manifests
from podiumd_minikube import openbao


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
def seal_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openbao, "seal_key_args", lambda: SEAL)


def test_options_full_adds_profiles_shape_and_extra_args(monkeypatch: pytest.MonkeyPatch, seal_key: None):
    monkeypatch.setattr(chart, "objecten_shape", lambda: chart.ObjectenShape(merged=True, sets=["--set", "shape=1"]))
    selected = deploy.options(full=True, extra=["--set", "x=y"])
    assert selected.objecten_merged
    assert selected.helm_args == [*chart.FULL_PROFILE_SETS, "--set", "shape=1", *SEAL, "--set", "x=y"]


def test_options_core_has_no_profile_sets(seal_key: None):
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
    assert deploy.jobs(render) == ["openzaak-config", "pabc-migrations-1"]


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
