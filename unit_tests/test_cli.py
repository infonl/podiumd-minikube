"""The podiumd-minikube command and bin/'s stubs: --help works offline and arguments are validated."""

import os
import subprocess
import sys

from pathlib import Path

import pytest

from podiumd_minikube import cli

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "podiumd_minikube", *args], capture_output=True, text=True, check=False, env=env
    )


def test_bin_has_a_stub_for_every_command():
    stubs = {path.name for path in BIN_DIR.iterdir() if path.is_symlink() and path.readlink() == Path(".stub")}
    assert stubs == set(cli.COMMANDS)
    assert {path.name for path in BIN_DIR.iterdir()} == {*stubs, ".stub"}


def test_help_lists_every_command():
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    assert all(f"  {name} " in result.stdout for name in cli.COMMANDS)


@pytest.mark.parametrize("command", cli.COMMANDS)
def test_help_runs_without_a_cluster(command: str):
    result = _run(command, "--help")
    assert result.returncode == 0, result.stderr
    assert f"usage: podiumd-minikube {command}" in result.stdout


def test_stub_runs_its_command():
    result = subprocess.run(
        [str(BIN_DIR / "show-podiumd-version"), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "usage: podiumd-minikube show-podiumd-version" in result.stdout


@pytest.mark.parametrize(
    "args",
    [[], ["4.9.3"], ["--path", "/x", "4.9.3"]],
)
def test_set_podiumd_version_rejects_ambiguous_arguments(args: list[str]):
    result = _run("set-podiumd-version", *args)
    assert result.returncode == 2
    assert "error:" in result.stderr


def test_cluster_lock_run_runs_its_command_under_the_lock(tmp_path: Path):
    lock_file = tmp_path / "lock"
    marker = tmp_path / "ran"
    env = {**os.environ, "MINIKUBE_LOCK_FILE": str(lock_file)}
    script = f"import pathlib; pathlib.Path({str(marker)!r}).write_text(pathlib.Path({str(lock_file)!r}).read_text())"
    result = _run("cluster-lock", "--who", "kees", "run", "test", "--", sys.executable, "-c", script, env=env)
    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").startswith("kees test ")
    assert not lock_file.exists()


def test_cluster_lock_run_keeps_the_commands_own_double_dash(tmp_path: Path):
    env = {**os.environ, "MINIKUBE_LOCK_FILE": str(tmp_path / "lock")}
    script = "import sys; print(sys.argv[1:])"
    result = _run("cluster-lock", "run", "test", "--", sys.executable, "-c", script, "--", "-U", env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['--', '-U']"
