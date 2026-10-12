"""Selecting and showing the podiumd and monitoring-logging dependency versions."""

from pathlib import Path

from podiumd_minikube import dependency
from podiumd_minikube.dependency import DEPENDENCIES
from podiumd_minikube.paths import CHARTS_DIR
from podiumd_minikube.paths import VERSIONS_FILE
from podiumd_minikube.process import UserError


def set_version(podiumd_version: str, monitoring_logging: str, *, versions_file: Path = VERSIONS_FILE) -> None:
    """Selects published podiumd and monitoring-logging versions and fetches them."""
    dependency.write("podiumd", "version", podiumd_version, versions_file)
    dependency.write("monitoringLogging", "version", monitoring_logging, versions_file)
    dependency.sync(versions_file)
    print(f"podiumd {podiumd_version} and monitoring-logging {monitoring_logging} selected; charts/*.tgz synced.")


def set_path(checkout: Path, *, versions_file: Path = VERSIONS_FILE) -> None:
    """Selects a local podiumd checkout; monitoring-logging follows its sibling directory when there is one."""
    if not checkout.is_dir():
        msg = f"not a directory: {checkout}"
        raise UserError(msg)
    if not (checkout / "Chart.yaml").is_file():
        msg = f"no Chart.yaml in {checkout}"
        raise UserError(msg)
    absolute = checkout.resolve()
    sibling = absolute.parent / "monitoring-logging"
    has_sibling = (sibling / "Chart.yaml").is_file()
    if not has_sibling and dependency.selection("monitoringLogging", versions_file) is None:
        msg = (
            f"no monitoring-logging/ next to {absolute} and no monitoring-logging version recorded: "
            "run podiumd-minikube set-podiumd-version <version> <monitoring-logging-version> once first"
        )
        raise UserError(msg)
    dependency.write("podiumd", "path", str(absolute), versions_file)
    if has_sibling:
        dependency.write("monitoringLogging", "path", str(sibling), versions_file)
    dependency.sync(versions_file)
    kept = f"local path {sibling}" if has_sibling else "its recorded selection"
    print(f"podiumd set to local path {absolute}, monitoring-logging to {kept}; charts/*.tgz synced.")


def describe(key: str, charts_dir: Path = CHARTS_DIR, versions_file: Path = VERSIONS_FILE) -> list[str]:
    """Lines describing key's selection and whether its tarball is fetched; [] when not set."""
    chart = DEPENDENCIES[key]
    selected = dependency.selection(key, versions_file)
    if selected is None:
        return []
    if selected.mode == "path":
        chart_yaml = Path(selected.value) / "Chart.yaml"
        lines = [f"  Local checkout: {selected.value}"]
        if chart_yaml.is_file():
            declared = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in chart_yaml.read_text(encoding="utf-8").splitlines()
                    if line.startswith("version:")
                ),
                "?",
            )
            lines.append(
                f"    (its Chart.yaml declares version={declared}; re-packaged on every deploy/provision-cluster run)"
            )
        else:
            lines.append("    WARNING: no Chart.yaml there - the checkout moved or was removed.")
        return lines
    state = (
        "already fetched"
        if selected.tarball(chart, charts_dir)
        else "not yet fetched; the next deploy/provision-cluster run fetches it (needs network)"
    )
    return [f"  Version: {selected.value}", f"  charts/{chart}-{selected.value}.tgz {state}."]


def show(charts_dir: Path = CHARTS_DIR, versions_file: Path = VERSIONS_FILE) -> None:
    """Prints each dependency's selection; raises for missing selections."""
    missing: list[str] = []
    for key, chart in DEPENDENCIES.items():
        print(f"{chart}:")
        lines = describe(key, charts_dir, versions_file)
        if not lines:
            lines = ["  Not configured - run podiumd-minikube set-podiumd-version."]
            missing.append(chart)
        print("\n".join(lines))
        print()
    if missing:
        raise dependency.missing_error(missing)
