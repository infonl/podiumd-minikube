"""podiumd_minikube.versions: selecting and describing dependency versions."""

from collections.abc import Callable
from pathlib import Path

import pytest

from podiumd_minikube import dependency
from podiumd_minikube import versions
from podiumd_minikube.process import UserError


@pytest.fixture
def versions_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A versions file in tmp_path; sync is a no-op."""
    monkeypatch.setattr(dependency, "sync", lambda *_args: None)
    return tmp_path / "versions.yaml"


def test_set_version_selects_both_dependencies(versions_file: Path):
    versions.set_version("4.9.4", "1.0.15", versions_file=versions_file)
    assert dependency.selection("podiumd", versions_file) == dependency.Selection("version", "4.9.4")
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection("version", "1.0.15")


def _chart(directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / "Chart.yaml").write_text("version: 9.9.9\n", encoding="utf-8")
    return directory


def test_set_path_follows_the_monitoring_logging_sibling(versions_file: Path, tmp_path: Path):
    _chart(tmp_path / "charts" / "monitoring-logging")
    versions.set_path(_chart(tmp_path / "charts" / "podiumd"), versions_file=versions_file)
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection(
        "path", str((tmp_path / "charts" / "monitoring-logging").resolve())
    )
    assert "declares version=9.9.9" in versions.describe("podiumd", tmp_path, versions_file)[1]


def test_set_path_without_sibling_needs_a_recorded_monitoring_logging(versions_file: Path, tmp_path: Path):
    with pytest.raises(UserError, match="no monitoring-logging version recorded"):
        versions.set_path(_chart(tmp_path / "podiumd"), versions_file=versions_file)
    assert dependency.selection("podiumd", versions_file) is None


def test_describe_reports_whether_the_tarball_is_fetched(tmp_path: Path):
    versions_file = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.9.3", versions_file)
    assert "not yet fetched" in versions.describe("podiumd", tmp_path, versions_file)[1]
    (tmp_path / "podiumd-4.9.3.tgz").touch()
    assert "already fetched" in versions.describe("podiumd", tmp_path, versions_file)[1]
    assert versions.describe("monitoringLogging", tmp_path, versions_file) == []


@pytest.mark.parametrize(
    ("make", "message"), [(lambda _path: None, "not a directory"), (Path.mkdir, "no Chart.yaml in")]
)
def test_set_path_needs_a_chart_directory(
    versions_file: Path, tmp_path: Path, make: Callable[[Path], object], message: str
):
    checkout = tmp_path / "podiumd"
    make(checkout)
    with pytest.raises(UserError, match=message):
        versions.set_path(checkout, versions_file=versions_file)


def test_describe_warns_about_a_moved_checkout(tmp_path: Path):
    versions_file = tmp_path / "versions.yaml"
    dependency.write("podiumd", "path", str(tmp_path / "gone"), versions_file)
    assert versions.describe("podiumd", tmp_path, versions_file) == [
        f"  Local checkout: {tmp_path / 'gone'}",
        "    WARNING: no Chart.yaml there - the checkout moved or was removed.",
    ]


def test_show_prints_each_selection_and_raises_for_missing_ones(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    versions_file = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.9.3", versions_file)
    with pytest.raises(UserError, match="entry for monitoring-logging"):
        versions.show(tmp_path, versions_file)
    out = capsys.readouterr().out
    assert "podiumd:\n  Version: 4.9.3\n" in out
    assert "monitoring-logging:\n  Not configured - run podiumd-minikube set-podiumd-version.\n" in out
    dependency.write("monitoringLogging", "version", "1.0.15", versions_file)
    versions.show(tmp_path, versions_file)
