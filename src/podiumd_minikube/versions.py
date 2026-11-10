"""Selecting and showing the podiumd and monitoring-logging dependency versions."""

from pathlib import Path

from podiumd_minikube import dependency
from podiumd_minikube.paths import CHARTS_DIR
from podiumd_minikube.paths import VERSIONS_FILE
from podiumd_minikube.process import UserError


def _selection(argument: str) -> dependency.Selection:
    """Return a local chart checkout when argument is a directory or contains a slash, else a published version."""
    checkout = Path(argument)
    if "/" not in argument and not checkout.is_dir():
        return dependency.Selection("version", argument)
    if not (checkout / "Chart.yaml").is_file():
        msg = f"no Chart.yaml in {checkout}: give a published version or a chart checkout"
        raise UserError(msg)
    return dependency.Selection("path", str(checkout.resolve()))


def select(
    podiumd: str,
    monitoring_logging: str | None = None,
    *,
    versions_file: Path = VERSIONS_FILE,
    charts_dir: Path = CHARTS_DIR,
) -> None:
    """Select each dependency as a published version or a local chart checkout and fetch them.

    Without monitoring_logging: a podiumd checkout's sibling monitoring-logging/, else the recorded selection.
    """
    chosen = {"podiumd": _selection(podiumd)}
    monitoring = _monitoring_logging(chosen["podiumd"], monitoring_logging, versions_file)
    if monitoring:
        chosen["monitoringLogging"] = monitoring
    for key, selected in chosen.items():
        _drop_packaged_checkout(key, selected, versions_file, charts_dir)
        dependency.write(key, selected.mode, selected.value, versions_file)
    dependency.sync(versions_file)
    for key, selected in chosen.items():
        origin = "local checkout " if selected.mode == "path" else ""
        print(f"{dependency.DEPENDENCIES[key]}: {origin}{selected.value}")
    if "monitoringLogging" not in chosen:
        print("monitoring-logging: recorded selection kept")
    print("charts/*.tgz synced.")


def _monitoring_logging(
    podiumd: dependency.Selection, argument: str | None, versions_file: Path
) -> dependency.Selection | None:
    """argument's selection, else podiumd's sibling checkout; None to keep the recorded one."""
    sibling = Path(podiumd.value).parent / "monitoring-logging"
    if argument:
        return _selection(argument)
    if podiumd.mode == "path" and (sibling / "Chart.yaml").is_file():
        return dependency.Selection("path", str(sibling))
    if dependency.selection("monitoringLogging", versions_file) is None:
        msg = f"no monitoring-logging selection recorded: {dependency.USAGE_HINT}"
        raise UserError(msg)
    return None


def _drop_packaged_checkout(key: str, selected: dependency.Selection, versions_file: Path, charts_dir: Path) -> None:
    """Remove key's tarballs when selected changes it from a checkout to a published version.

    The checkout's package has the published tarball's name, so sync would take it as fetched.
    """
    recorded = dependency.selection(key, versions_file)
    if selected.mode == "version" and recorded and recorded.mode == "path":
        for tgz in charts_dir.glob(f"{dependency.DEPENDENCIES[key]}-*.tgz"):
            tgz.unlink()


def describe(key: str, charts_dir: Path = CHARTS_DIR, versions_file: Path = VERSIONS_FILE) -> list[str]:
    """Lines describing key's selection and whether its tarball is fetched; [] when not set."""
    chart = dependency.DEPENDENCIES[key]
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
    """Print each dependency's selection; raise for missing selections."""
    missing: list[str] = []
    for key, chart in dependency.DEPENDENCIES.items():
        print(f"{chart}:")
        lines = describe(key, charts_dir, versions_file)
        if not lines:
            lines = ["  Not configured - run podiumd-minikube set-podiumd-version."]
            missing.append(chart)
        print("\n".join(lines))
        print()
    if missing:
        raise dependency.missing_error(missing)
