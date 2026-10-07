"""The podiumd and monitoring-logging dependency selection in .podiumd-versions.yaml.

That gitignored file is the only place either version is recorded; Chart.yaml
holds placeholders so a personal version choice never becomes a shared diff.
sync() points Chart.yaml at the selection for one `helm dependency update` and
restores Chart.yaml and Chart.lock afterwards: `helm template` does not check
a fetched subchart against Chart.yaml's version constraint.
"""

import re
import shutil
import tempfile

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from lib import process
from lib.paths import CHART_DIR
from lib.paths import CHART_LOCK
from lib.paths import CHART_YAML
from lib.paths import CHARTS_DIR
from lib.paths import VERSIONS_FILE
from lib.process import UserError

Mode = Literal["version", "path"]

# Key in .podiumd-versions.yaml -> dependency name in Chart.yaml and charts/<name>-<version>.tgz.
DEPENDENCIES = {"podiumd": "podiumd", "monitoringLogging": "monitoring-logging"}

USAGE_HINT = (
    "run ./scripts/set-podiumd-version <version> <monitoring-logging-version> "
    "(or <version> --disable-monitoring-logging, or --path <dir> [--disable-monitoring-logging])"
)


@dataclass(frozen=True)
class Selection:
    """One dependency's selection: a published version or a local chart checkout."""

    mode: Mode
    value: str

    def tarball(self, chart: str, charts_dir: Path = CHARTS_DIR) -> Path | None:
        """The fetched charts/<chart>-<version>.tgz; None in path mode or when not fetched."""
        tgz = charts_dir / f"{chart}-{self.value}.tgz"
        return tgz if self.mode == "version" and tgz.is_file() else None


def _load(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write(key: str, mode: Mode, value: str, path: Path = VERSIONS_FILE) -> None:
    """Sets key's selection, keeping the other dependency's entry."""
    data = _load(path)
    data[key] = {mode: value}
    path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")


def selection(key: str, path: Path = VERSIONS_FILE) -> Selection | None:
    """key's selection; path wins over version; None when not set."""
    entry = _load(path).get(key) or {}
    if entry.get("path"):
        return Selection("path", str(entry["path"]))
    if entry.get("version"):
        return Selection("version", str(entry["version"]))
    return None


def missing_error(names: list[str]) -> UserError:
    """The error for dependencies without a selection."""
    return UserError(f"no .podiumd-versions.yaml entry for {', '.join(names)}: {USAGE_HINT}")


def set_chart_dependency(text: str, name: str, repository: str, version: str) -> str:
    """Chart.yaml text with dependency name's repository and version replaced.

    Scoped to name's own block: podiumd and monitoring-logging share the
    "@dimpact" repository string.
    """
    lines = text.splitlines(keepends=True)
    in_block = False
    for index, line in enumerate(lines):
        if re.search(rf"- name: {re.escape(name)}$", line.rstrip("\n")):
            in_block = True
        elif line.startswith("  - name: "):
            in_block = False
        elif in_block and "repository:" in line:
            lines[index] = re.sub(r'repository: "[^"]*"', f'repository: "{repository}"', line)
        elif in_block:
            lines[index] = re.sub(r'version:\s*"[^"]*"', f'version: "{version}"', line)
    return "".join(lines)


def _chart_reference(selected: Selection) -> tuple[str, str]:
    """(repository, version) for Chart.yaml."""
    if selected.mode == "path":
        return f"file://{Path(selected.value).resolve()}", "*"
    return "@dimpact", selected.value


def sync(versions_file: Path = VERSIONS_FILE, chart_dir: Path = CHART_DIR) -> None:
    """Makes charts/*.tgz match .podiumd-versions.yaml.

    Skips `helm dependency update` when every dependency is a published
    version that is already fetched; path mode always re-packages the
    checkout. Both dependencies are edited before one update, because the
    update re-resolves every dependency at once.
    """
    selections = {key: selection(key, versions_file) for key in DEPENDENCIES}
    missing = [DEPENDENCIES[key] for key, selected in selections.items() if selected is None]
    if missing:
        raise missing_error(missing)
    resolved = {DEPENDENCIES[key]: selected for key, selected in selections.items() if selected}
    if all(selected.tarball(chart, chart_dir / "charts") for chart, selected in resolved.items()):
        return

    chart_yaml = chart_dir / CHART_YAML.name
    chart_lock = chart_dir / CHART_LOCK.name
    with tempfile.TemporaryDirectory() as backup_dir:
        backups = [(original, Path(backup_dir) / original.name) for original in (chart_yaml, chart_lock)]
        for original, backup in backups:
            if original.is_file():
                shutil.copy2(original, backup)
        try:
            text = chart_yaml.read_text(encoding="utf-8")
            for chart, selected in resolved.items():
                text = set_chart_dependency(text, chart, *_chart_reference(selected))
            chart_yaml.write_text(text, encoding="utf-8")
            process.run(["helm", "dependency", "update", str(chart_dir)], capture=False)
        finally:
            for original, backup in backups:
                if backup.is_file():
                    shutil.copy2(backup, original)
