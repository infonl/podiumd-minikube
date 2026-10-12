"""lib.versions: selecting and describing dependency versions."""

from pathlib import Path

import pytest

from lib import dependency
from lib import values
from lib import versions
from lib.process import UserError


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """(versions file, values.yaml) in tmp_path; sync is a no-op."""
    monkeypatch.setattr(dependency, "sync", lambda *_args: None)
    values_yaml = tmp_path / "values.yaml"
    values_yaml.write_text("monitoringLogging:\n  enabled: false\n", encoding="utf-8")
    return tmp_path / "versions.yaml", values_yaml


def test_disabling_monitoring_logging_needs_an_earlier_version(files: tuple[Path, Path]):
    versions_file, values_yaml = files
    with pytest.raises(UserError, match="no monitoring-logging version recorded"):
        versions.set_version("4.9.3", None, versions_file=versions_file, values_yaml=values_yaml)
    assert dependency.selection("podiumd", versions_file) is None


def test_set_version_enables_and_then_disables_monitoring_logging(files: tuple[Path, Path]):
    versions_file, values_yaml = files
    versions.set_version("4.9.3", "1.0.14", versions_file=versions_file, values_yaml=values_yaml)
    assert values.monitoring_logging_enabled(values_yaml)
    versions.set_version("4.9.4", None, versions_file=versions_file, values_yaml=values_yaml)
    assert not values.monitoring_logging_enabled(values_yaml)
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection("version", "1.0.14")
    assert dependency.selection("podiumd", versions_file) == dependency.Selection("version", "4.9.4")


def test_set_path_follows_the_monitoring_logging_sibling(files: tuple[Path, Path], tmp_path: Path):
    versions_file, values_yaml = files
    for name in ("podiumd", "monitoring-logging"):
        (tmp_path / "charts" / name).mkdir(parents=True)
        (tmp_path / "charts" / name / "Chart.yaml").write_text("version: 9.9.9\n", encoding="utf-8")
    versions.set_path(
        tmp_path / "charts" / "podiumd",
        disable_monitoring_logging=False,
        versions_file=versions_file,
        values_yaml=values_yaml,
    )
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection(
        "path", str((tmp_path / "charts" / "monitoring-logging").resolve())
    )
    assert values.monitoring_logging_enabled(values_yaml)
    assert "declares version=9.9.9" in versions.describe("podiumd", tmp_path, versions_file)[1]


def test_describe_reports_whether_the_tarball_is_fetched(tmp_path: Path):
    versions_file = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.9.3", versions_file)
    assert "not yet fetched" in versions.describe("podiumd", tmp_path, versions_file)[1]
    (tmp_path / "podiumd-4.9.3.tgz").touch()
    assert "already fetched" in versions.describe("podiumd", tmp_path, versions_file)[1]
    assert versions.describe("monitoringLogging", tmp_path, versions_file) == []
