"""Selecting and showing the podiumd and monitoring-logging dependency versions."""

from pathlib import Path

from lib import dependency
from lib import values
from lib.dependency import DEPENDENCIES
from lib.paths import CHARTS_DIR
from lib.paths import VALUES_YAML
from lib.paths import VERSIONS_FILE
from lib.process import UserError


def _require_monitoring_logging_entry(example: str, versions_file: Path) -> None:
    """Helm fetches monitoring-logging even when disabled, so a disable needs an earlier selection."""
    if dependency.selection("monitoringLogging", versions_file) is None:
        msg = (
            "no monitoring-logging version recorded yet, and Helm still fetches it when disabled: "
            f"run ./scripts/set-podiumd-version {example} <monitoring-logging-version> once first"
        )
        raise UserError(msg)


def set_version(
    podiumd_version: str,
    monitoring_logging: str | None,
    *,
    versions_file: Path = VERSIONS_FILE,
    values_yaml: Path = VALUES_YAML,
) -> None:
    """Selects a published podiumd version; monitoring_logging None disables it, a version enables it."""
    if monitoring_logging is None:
        _require_monitoring_logging_entry(podiumd_version, versions_file)
    dependency.write("podiumd", "version", podiumd_version, versions_file)
    if monitoring_logging is None:
        values.set_flag("monitoringLogging", "enabled", value=False, path=values_yaml)
    else:
        dependency.write("monitoringLogging", "version", monitoring_logging, versions_file)
        values.set_flag("monitoringLogging", "enabled", value=True, path=values_yaml)
    dependency.sync(versions_file)
    print(f"podiumd dependency set to {podiumd_version} (.podiumd-versions.yaml); charts/*.tgz synced.")
    if monitoring_logging is None:
        print("monitoring-logging disabled (values.yaml); its .podiumd-versions.yaml entry is kept and still fetched.")
    else:
        print(f"monitoring-logging dependency set to {monitoring_logging}; monitoringLogging.enabled set to true.")


def set_path(
    checkout: Path,
    *,
    disable_monitoring_logging: bool,
    versions_file: Path = VERSIONS_FILE,
    values_yaml: Path = VALUES_YAML,
) -> None:
    """Selects a local podiumd checkout; monitoring-logging follows its sibling directory."""
    if not checkout.is_dir():
        msg = f"not a directory: {checkout}"
        raise UserError(msg)
    if not (checkout / "Chart.yaml").is_file():
        msg = f"no Chart.yaml in {checkout}"
        raise UserError(msg)
    absolute = checkout.resolve()
    sibling = absolute.parent / "monitoring-logging"
    use_sibling = not disable_monitoring_logging and (sibling / "Chart.yaml").is_file()
    if not use_sibling:
        _require_monitoring_logging_entry(f"--path {absolute}", versions_file)
    dependency.write("podiumd", "path", str(absolute), versions_file)
    if use_sibling:
        dependency.write("monitoringLogging", "path", str(sibling), versions_file)
        print(f"monitoring-logging dependency set to local path {sibling}; monitoringLogging.enabled set to true.")
    elif disable_monitoring_logging:
        print("monitoring-logging disabled (values.yaml); its .podiumd-versions.yaml entry is kept and still fetched.")
    else:
        print(f"WARNING: no monitoring-logging/ next to {absolute}: kept its entry, disabled it in values.yaml.")
    values.set_flag("monitoringLogging", "enabled", value=use_sibling, path=values_yaml)
    dependency.sync(versions_file)
    print(f"podiumd dependency set to local path {absolute} (.podiumd-versions.yaml); charts/*.tgz synced.")


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


def show(charts_dir: Path = CHARTS_DIR, versions_file: Path = VERSIONS_FILE, values_yaml: Path = VALUES_YAML) -> None:
    """Prints each dependency's selection, then monitoringLogging.enabled; raises for missing selections."""
    missing: list[str] = []
    for key, chart in DEPENDENCIES.items():
        print(f"{chart}:")
        lines = describe(key, charts_dir, versions_file)
        if not lines:
            lines = ["  Not configured - run ./scripts/set-podiumd-version."]
            missing.append(chart)
        print("\n".join(lines))
        print()
    if values.monitoring_logging_enabled(values_yaml):
        print("monitoringLogging.enabled: true -> ENABLED, backing the metrics profile (values.yaml)")
    else:
        print("monitoringLogging.enabled: false -> DISABLED - not rendered, even when fetched (values.yaml)")
    if missing:
        raise dependency.missing_error(missing)
