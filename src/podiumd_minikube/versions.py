"""Selecting and showing the podiumd and monitoring-logging dependency versions."""

from pathlib import Path

from podiumd_minikube import dependency
from podiumd_minikube.dependency import DEPENDENCIES
from podiumd_minikube.paths import CHARTS_DIR
from podiumd_minikube.paths import VERSIONS_FILE
from podiumd_minikube.process import UserError


def _selection(argument: str) -> dependency.Selection:
    """A local chart checkout when argument is a directory or contains a slash, else a published version."""
    checkout = Path(argument)
    if "/" not in argument and not checkout.is_dir():
        return dependency.Selection("version", argument)
    if not (checkout / "Chart.yaml").is_file():
        msg = f"no Chart.yaml in {checkout}: give a published version or a chart checkout"
        raise UserError(msg)
    return dependency.Selection("path", str(checkout.resolve()))


def select(podiumd: str, monitoring_logging: str | None = None, *, versions_file: Path = VERSIONS_FILE) -> None:
    """Selects each dependency as a published version or a local chart checkout and fetches them.

    Without monitoring_logging: a podiumd checkout's sibling monitoring-logging/, else the recorded selection.
    """
    chosen = {"podiumd": _selection(podiumd)}
    sibling = Path(chosen["podiumd"].value).parent / "monitoring-logging"
    if monitoring_logging:
        chosen["monitoringLogging"] = _selection(monitoring_logging)
    elif chosen["podiumd"].mode == "path" and (sibling / "Chart.yaml").is_file():
        chosen["monitoringLogging"] = dependency.Selection("path", str(sibling))
    elif dependency.selection("monitoringLogging", versions_file) is None:
        msg = f"no monitoring-logging selection recorded: {dependency.USAGE_HINT}"
        raise UserError(msg)
    for key, selected in chosen.items():
        dependency.write(key, selected.mode, selected.value, versions_file)
    dependency.sync(versions_file)
    for key, selected in chosen.items():
        print(f"{DEPENDENCIES[key]}: {'local checkout ' if selected.mode == 'path' else ''}{selected.value}")
    if "monitoringLogging" not in chosen:
        print("monitoring-logging: recorded selection kept")
    print("charts/*.tgz synced.")


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
