"""podiumd_minikube.releases: which releases, with whose values and --set arguments, and the helm argv."""

from pathlib import Path

import pytest

from conftest import FakeRun
from conftest import make_tgz

from podiumd_minikube import chart
from podiumd_minikube import releases
from podiumd_minikube.paths import MONITORING_NAMESPACE
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME
from podiumd_minikube.process import UserError

ARGS = [
    "--set", "images.busybox=b:1",
    "--set", "podiumd.zac.enabled=true",
    "--set", "monitoring-logging.grafana.x=1",
    "--set", "metrics.enabled=true",
]  # fmt: skip


@pytest.fixture
def fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """charts/ with podiumd and monitoring-logging, podiumd with one subchart under an alias."""
    make_tgz(
        tmp_path / "podiumd-4.10.0.tgz",
        {
            "podiumd/values.yaml": "zac: {enabled: false, db: {host: x}}\n",
            "podiumd/Chart.yaml": "dependencies: [{name: zaakafhandelcomponent, alias: zac}]\n",
            "podiumd/charts/zaakafhandelcomponent/values.yaml": "image: {tag: '1'}\ndb: {port: 5432}\n",
        },
    )
    make_tgz(tmp_path / "monitoring-logging-1.0.19.tgz", {"monitoring-logging/values.yaml": ""})
    monkeypatch.setattr(chart, "CHARTS_DIR", tmp_path)
    monkeypatch.setattr(chart.tarball, "__defaults__", (tmp_path,))
    monkeypatch.setattr(chart.podiumd_defaults, "__defaults__", (tmp_path,))
    return tmp_path


def test_releases_split_the_values_and_sets(fetched: Path):
    found = releases.releases(ARGS)
    own, podiumd, monitoring = found[0], found[1], found[2]
    assert (own.name, own.namespace, podiumd.namespace, monitoring.namespace) == (
        RELEASE_NAME, NAMESPACE, NAMESPACE, MONITORING_NAMESPACE,
    )  # fmt: skip
    assert own.sets == [*ARGS, "--set", "podiumd.enabled=false", "--set", "metrics.enabled=false"]
    assert podiumd.sets == ["--set", "zac.enabled=true"]
    assert monitoring.sets == ["--set", "grafana.x=1"]
    assert podiumd.chart == fetched / "podiumd-4.10.0.tgz"
    # Under values.yaml's section: the subchart's defaults, then podiumd's, then values.yaml's (db.host).
    zac = own.values["podiumd"]["zac"]
    assert (zac["image"], zac["db"]["port"], zac["db"]["host"]) == ({"tag": "1"}, 5432, "postgres")


def test_monitoring_logging_only_with_the_metrics_profile(fetched: Path):
    assert [release.name for release in releases.releases(ARGS[:4])] == [RELEASE_NAME, releases.PODIUMD]


def test_merged_replaces_lists_and_scalars_and_merges_maps():
    base = {"a": {"b": 1, "c": [1, 2]}, "d": 1}
    assert chart.merged(base, {"a": {"c": [3]}, "e": None}) == {"a": {"b": 1, "c": [3]}, "d": 1, "e": None}


def test_post_renderer_args_pass_the_flags():
    assert releases.post_renderer_args(objecten_merged=True, hold_zac=True) == [
        "--post-renderer", releases.POST_RENDERER,
        "--post-renderer-args", "--ca-trust",
        "--post-renderer-args", "--objecten-merged",
        "--post-renderer-args", "--hold-zac",
    ]  # fmt: skip


def test_upgrade_runs_helm_with_the_repository_plugins(fake_run: FakeRun):
    release = releases.Release(releases.PODIUMD, NAMESPACE, Path("podiumd.tgz"), {"a": 1}, ["--set", "x=1"])
    releases.upgrade(release, ["--post-renderer", releases.POST_RENDERER])
    argv = fake_run.calls[0]
    assert argv[:3] == ["env", f"HELM_PLUGINS={releases.HELM_PLUGINS}", "helm"]
    assert argv[3:7] == ["upgrade", "--install", releases.PODIUMD, "podiumd.tgz"]
    assert argv[-len(releases.UPGRADE_ARGS) - 2 :] == [
        *releases.UPGRADE_ARGS,
        "--post-renderer",
        releases.POST_RENDERER,
    ]


def test_the_seal_key_stays_out_of_this_charts_release(fetched: Path):
    seal = ["--set-file", "podiumd.openbao.seal.static.key=/k"]
    own, podiumd = releases.releases([*ARGS[:4], *seal])
    assert seal[1] not in own.sets
    assert podiumd.sets[-1] == "openbao.seal.static.key=/k"


@pytest.mark.parametrize("args", [["--set=a=1"], ["--set", "a"], ["-f", "x.yaml"]])
def test_releases_refuse_arguments_they_cannot_route(fetched: Path, args: list[str]):
    with pytest.raises(UserError, match="pairs to helm"):
        releases.releases(args)
