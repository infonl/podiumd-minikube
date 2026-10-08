"""lib.deploy: helm options, apply error counting and argument parsing."""

import pytest

from lib import chart
from lib import deploy
from lib import manifests
from lib import openbao
from lib import values


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
def no_pkce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chart, "zac_pkce", lambda: chart.ZacPkce(enabled=False))
    monkeypatch.setattr(openbao, "seal_key_args", lambda: SEAL)


def test_options_full_adds_profiles_shape_and_extra_args(monkeypatch: pytest.MonkeyPatch, no_pkce: None):
    monkeypatch.setattr(chart, "objecten_shape", lambda: chart.ObjectenShape(merged=True, sets=["--set", "shape=1"]))
    monkeypatch.setattr(values, "monitoring_logging_enabled", lambda: False)
    selected = deploy.options(full=True, extra=["--set", "x=y"])
    assert selected.objecten_merged
    assert selected.helm_args == [*chart.FULL_PROFILE_SETS, "--set", "shape=1", *SEAL, "--set", "x=y"]


def test_options_points_zac_at_monitoring_logging_collector(monkeypatch: pytest.MonkeyPatch, no_pkce: None):
    monkeypatch.setattr(values, "monitoring_logging_enabled", lambda: True)
    selected = deploy.options(full=False, extra=[])
    assert not selected.objecten_merged
    assert selected.helm_args == [
        "--set",
        "podiumd.zac.opentelemetry_zaakafhandelcomponent.endpoint=http://podiumd-minikube-opentelemetry-collector:4317",
        *SEAL,
    ]


def test_parser_passes_unknown_arguments_to_helm():
    args, extra = deploy.parser("").parse_known_args(["--set", "a=b", "--full", "--force-prune"])
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
    assert deploy.unfinished_jobs(jobs, ["done", "broken", "busy", "missing"]) == (["busy", "missing"], ["broken"])


def test_rolled_out_needs_current_generation_and_ready_replicas():
    def workload(generation: int, observed: int, updated: int, ready: int) -> dict[str, object]:
        status = {"observedGeneration": observed, "updatedReplicas": updated, "readyReplicas": ready}
        return {"metadata": {"generation": generation}, "spec": {"replicas": 1}, "status": status}

    assert deploy.rolled_out(workload(2, 2, 1, 1))
    assert not deploy.rolled_out(workload(2, 1, 1, 1))
    assert not deploy.rolled_out(workload(2, 2, 1, 0))
