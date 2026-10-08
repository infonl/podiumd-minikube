"""The extensionless scripts: --help works offline and arguments are validated."""

import subprocess
import sys

from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(
    path.name
    for path in SCRIPTS_DIR.iterdir()
    if path.is_file() and path.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, check=False)


def test_every_shell_script_was_converted():
    assert len(SCRIPTS) == 15
    assert not list(SCRIPTS_DIR.rglob("*.sh"))


@pytest.mark.parametrize("script", SCRIPTS)
def test_help_runs_without_a_cluster(script: str):
    result = _run(str(SCRIPTS_DIR / script), "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize(
    "args",
    [[], ["4.9.3"], ["4.9.3", "1.0.14", "--disable-monitoring-logging"], ["--path", "/x", "4.9.3"]],
)
def test_set_podiumd_version_rejects_ambiguous_arguments(args: list[str]):
    result = _run(str(SCRIPTS_DIR / "set-podiumd-version"), *args)
    assert result.returncode == 2
    assert "error:" in result.stderr
