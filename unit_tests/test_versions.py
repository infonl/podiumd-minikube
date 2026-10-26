"""podiumd_minikube.versions: selecting and describing dependency versions."""

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


def test_select_takes_published_versions(versions_file: Path):
    versions.select("4.9.4", "1.0.15", versions_file=versions_file)
    assert dependency.selection("podiumd", versions_file) == dependency.Selection("version", "4.9.4")
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection("version", "1.0.15")


def _chart(directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / "Chart.yaml").write_text("version: 9.9.9\n", encoding="utf-8")
    return directory


def test_select_mixes_a_published_version_and_a_checkout(versions_file: Path, tmp_path: Path):
    checkout = _chart(tmp_path / "monitoring-logging")
    versions.select("4.10.0", str(checkout), versions_file=versions_file)
    assert dependency.selection("podiumd", versions_file) == dependency.Selection("version", "4.10.0")
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection(
        "path", str(checkout.resolve())
    )


def test_select_follows_the_monitoring_logging_sibling(versions_file: Path, tmp_path: Path):
    _chart(tmp_path / "charts" / "monitoring-logging")
    versions.select(str(_chart(tmp_path / "charts" / "podiumd")), versions_file=versions_file)
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection(
        "path", str((tmp_path / "charts" / "monitoring-logging").resolve())
    )
    assert "declares version=9.9.9" in versions.describe("podiumd", tmp_path, versions_file)[1]


def test_select_keeps_the_recorded_monitoring_logging(versions_file: Path):
    dependency.write("monitoringLogging", "version", "1.0.18", versions_file)
    versions.select("4.10.0", versions_file=versions_file)
    assert dependency.selection("monitoringLogging", versions_file) == dependency.Selection("version", "1.0.18")


def test_select_without_monitoring_logging_needs_a_recorded_one(versions_file: Path, tmp_path: Path):
    with pytest.raises(UserError, match="no monitoring-logging selection recorded"):
        versions.select(str(_chart(tmp_path / "podiumd")), versions_file=versions_file)
    assert dependency.selection("podiumd", versions_file) is None


def test_describe_reports_whether_the_tarball_is_fetched(tmp_path: Path):
    versions_file = tmp_path / "versions.yaml"
    dependency.write("podiumd", "version", "4.9.3", versions_file)
    assert "not yet fetched" in versions.describe("podiumd", tmp_path, versions_file)[1]
    (tmp_path / "podiumd-4.9.3.tgz").touch()
    assert "already fetched" in versions.describe("podiumd", tmp_path, versions_file)[1]
    assert versions.describe("monitoringLogging", tmp_path, versions_file) == []


def test_select_needs_a_chart_in_a_checkout(versions_file: Path, tmp_path: Path):
    (tmp_path / "podiumd").mkdir()
    with pytest.raises(UserError, match=r"no Chart\.yaml in"):
        versions.select(str(tmp_path / "podiumd"), "1.0.18", versions_file=versions_file)


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
