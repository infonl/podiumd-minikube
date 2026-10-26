"""podiumd_minikube.dependency: .podiumd-versions.yaml and the Chart.yaml edit around helm dependency update."""

import subprocess

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import dependency
from podiumd_minikube import process
from podiumd_minikube.process import UserError

CHART_YAML = """\
dependencies:
  - name: podiumd
    version: "0.0.0"
    repository: "@dimpact"
  - name: monitoring-logging
    version: "0.0.0"
    repository: "@dimpact"
"""

MISSING = "NAME\tVERSION\tREPOSITORY\tSTATUS\nloki\t6.55.0\t@grafana\tmissing\n"


def _chart_dir(tmp_path: Path) -> Path:
    (tmp_path / "charts").mkdir()
    (tmp_path / "Chart.yaml").write_text(CHART_YAML, encoding="utf-8")
    (tmp_path / "Chart.lock").write_text("lock\n", encoding="utf-8")
    return tmp_path


def test_selection_prefers_path_and_keeps_the_other_entry(tmp_path: Path):
    versions = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.9.3", versions)
    dependency.write("monitoringLogging", "version", "1.0.14", versions)
    dependency.write("podiumd", "path", "/checkout", versions)
    assert dependency.selection("podiumd", versions) == dependency.Selection("path", "/checkout")
    assert dependency.selection("monitoringLogging", versions) == dependency.Selection("version", "1.0.14")
    assert dependency.selection("podiumd", tmp_path / "absent.yaml") is None


def test_set_chart_dependency_touches_only_the_named_block():
    expected = CHART_YAML.replace(
        '- name: monitoring-logging\n    version: "0.0.0"\n    repository: "@dimpact"',
        '- name: monitoring-logging\n    version: "*"\n    repository: "file:///x"',
    )
    assert dependency.set_chart_dependency(CHART_YAML, "monitoring-logging", "file:///x", "*") == expected


def test_sync_refuses_without_selections(tmp_path: Path):
    with pytest.raises(UserError, match="podiumd, monitoring-logging"):
        dependency.sync(tmp_path / "versions.yaml", _chart_dir(tmp_path))


def test_sync_skips_helm_when_every_tarball_is_fetched(tmp_path: Path, fake_run: FakeRun):
    chart_dir = _chart_dir(tmp_path)
    versions = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.9.3", versions)
    dependency.write("monitoringLogging", "version", "1.0.14", versions)
    (chart_dir / "charts" / "podiumd-4.9.3.tgz").touch()
    (chart_dir / "charts" / "monitoring-logging-1.0.14.tgz").touch()
    dependency.sync(versions, chart_dir)
    assert fake_run.calls == []


def test_sync_updates_and_restores_chart_files_even_when_helm_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    chart_dir = _chart_dir(tmp_path)
    versions = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.9.3", versions)
    dependency.write("monitoringLogging", "path", str(tmp_path), versions)
    during_update: list[str] = []

    def failing_helm(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str] | None:
        if args[:3] == ["helm", "dependency", "list"]:
            return subprocess.CompletedProcess(args, 0, MISSING, "")
        if args[:3] == ["helm", "dependency", "build"]:
            return None
        during_update.append((chart_dir / "Chart.yaml").read_text(encoding="utf-8"))
        (chart_dir / "Chart.lock").write_text("changed\n", encoding="utf-8")
        raise process.ProcessError(args, 1, "fetch failed")

    monkeypatch.setattr(process, "run", failing_helm)
    with pytest.raises(process.ProcessError):
        dependency.sync(versions, chart_dir)
    assert 'version: "4.9.3"' in during_update[0]
    assert f'repository: "file://{tmp_path.resolve()}"' in during_update[0]
    assert (chart_dir / "Chart.yaml").read_text(encoding="utf-8") == CHART_YAML
    assert (chart_dir / "Chart.lock").read_text(encoding="utf-8") == "lock\n"


def test_sync_builds_a_path_checkouts_missing_subcharts_before_packaging_it(tmp_path: Path, fake_run: FakeRun):
    chart_dir = _chart_dir(tmp_path)
    versions = tmp_path / "versions.yaml"
    dependency.write("podiumd", "path", "/checkout", versions)
    dependency.write("monitoringLogging", "version", "1.0.14", versions)
    fake_run.on("helm", "dependency", "list", stdout=MISSING)
    dependency.sync(versions, chart_dir)
    assert [call[2:4] for call in fake_run.calls] == [
        ["list", "/checkout"],
        ["build", "/checkout"],
        ["update", str(chart_dir)],
    ]


def test_sync_packages_a_checkout_directly_when_the_published_charts_are_fetched(tmp_path: Path, fake_run: FakeRun):
    chart_dir = _chart_dir(tmp_path)
    versions = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.10.0", versions)
    dependency.write("monitoringLogging", "path", "/checkout", versions)
    (chart_dir / "charts" / "podiumd-4.10.0.tgz").touch()
    (chart_dir / "charts" / "monitoring-logging-1.0.18.tgz").touch()
    fake_run.on(
        "helm", "dependency", "list", stdout="NAME\tVERSION\tREPOSITORY\tSTATUS\nloki\t6.55.0\t@grafana\tok    \n"
    )
    dependency.sync(versions, chart_dir)
    assert fake_run.calls == [
        ["helm", "dependency", "list", "/checkout"],
        ["helm", "package", "/checkout", "-d", str(chart_dir / "charts")],
    ]
    assert not (chart_dir / "charts" / "monitoring-logging-1.0.18.tgz").exists()
    assert (chart_dir / "charts" / "podiumd-4.10.0.tgz").exists()


def test_sync_updates_a_path_checkouts_stale_chart_lock(tmp_path: Path, fake_run: FakeRun):
    chart_dir = _chart_dir(tmp_path)
    versions = tmp_path / "versions.yaml"
    dependency.write("podiumd", "path", "/checkout", versions)
    dependency.write("monitoringLogging", "version", "1.0.14", versions)
    fake_run.on("helm", "dependency", "list", stdout=MISSING)
    fake_run.on("helm", "dependency", "build", returncode=1, stderr="Error: the lock file (Chart.lock) is out of sync")
    dependency.sync(versions, chart_dir)
    assert [call[2:] for call in fake_run.calls[1:3]] == [["build", "/checkout"], ["update", "/checkout"]]


def test_sync_raises_other_build_errors(tmp_path: Path, fake_run: FakeRun):
    chart_dir = _chart_dir(tmp_path)
    versions = tmp_path / "versions.yaml"
    dependency.write("podiumd", "path", "/checkout", versions)
    dependency.write("monitoringLogging", "version", "1.0.14", versions)
    fake_run.on("helm", "dependency", "list", stdout=MISSING)
    fake_run.on("helm", "dependency", "build", returncode=1, stderr="Error: no repository definition")
    with pytest.raises(process.ProcessError):
        dependency.sync(versions, chart_dir)
