"""The podiumd-minikube command and bin/'s stubs: --help works offline and arguments are validated."""

import os
import runpy
import subprocess
import sys

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import chart
from podiumd_minikube import cli
from podiumd_minikube import deploy
from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import ports
from podiumd_minikube import provision
from podiumd_minikube import restart
from podiumd_minikube import services
from podiumd_minikube import status
from podiumd_minikube import teardown
from podiumd_minikube import tunnel
from podiumd_minikube import versions

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
    [[], ["4.9.3", "1.2.0", "extra"]],
)
def test_set_podiumd_version_rejects_ambiguous_arguments(args: list[str], capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exited:
        cli.run(["set-podiumd-version", *args])
    assert exited.value.code == 2
    assert "error:" in capsys.readouterr().err


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


@pytest.mark.parametrize(
    ("argv", "module", "function", "expected"),
    [
        (["deploy", "--full", "--set", "a=b"], deploy, "deploy", ((), {"full": True, "force_prune": False, "extra": ["--set", "a=b"]})),
        (["deploy", "--skip-roles"], deploy, "deploy", ((), {"full": False, "force_prune": False, "extra": chart.SKIP_ROLES_SETS})),
        (["deploy", "--full", "--otel"], deploy, "deploy", ((), {"full": True, "force_prune": False, "extra": chart.OTEL_SETS})),
        (["deploy-extended", "--force-prune"], deploy, "deploy", ((), {"full": True, "force_prune": True, "extra": cli.EXTENDED[1:]})),
        (["expose-postgres"], services, "expose_postgres", ((5432,), {})),
        (["expose-postgres", "15432"], services, "expose_postgres", ((15432,), {})),
        (["flush-redis"], services, "flush_redis", ((None,), {})),
        (["flush-redis", "--db", "3"], services, "flush_redis", ((3,), {})),
        (["provision-cluster"], provision, "provision", ((), {})),
        (["reset-namespace", "--yes"], teardown, "reset_namespace", ((), {"yes": True})),
        (["teardown-cluster"], teardown, "teardown_cluster", ((), {"yes": False})),
        (["set-podiumd-version", "4.9.3", "1.2.0"], versions, "select", (("4.9.3", "1.2.0"), {})),
        (["set-podiumd-version", "/x"], versions, "select", (("/x", None), {})),
        (["setup-tunnel"], tunnel, "setup_tunnel", ((), {})),
        (["setup-tunnel", "stop"], tunnel, "stop_tunnel", ((), {})),
        (["show-podiumd-version"], versions, "show", ((), {})),
        (["show-port-mappings"], ports, "show_port_mappings", ((), {})),
        (["start-cluster"], restart, "start", ((), {})),
        (["update-hosts"], tunnel, "update_hosts", ((), {})),
    ],
)  # fmt: skip
def test_command_passes_its_arguments_on(
    argv: list[str],
    module: object,
    function: str,
    expected: tuple[tuple[object, ...], dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
):
    calls = []
    monkeypatch.setattr(module, function, lambda *args, **kwargs: calls.append((args, kwargs)) or 7)
    result = cli.run(argv)
    assert calls == [expected]
    assert result == (7 if function in {"reset_namespace", "teardown_cluster"} else None)


@pytest.mark.parametrize(("args", "top"), [([], 20), (["--top", "5"], 5), (["--all"], 0)])
def test_show_cluster_status_checks_the_context_first(args: list[str], top: int, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(kube, "require_minikube_context", lambda: calls.append("context"))
    monkeypatch.setattr(status, "report", calls.append)
    cli.run(["show-cluster-status", *args])
    assert calls == ["context", top]


def test_cluster_lock_take_status_release(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    assert cli.run(["cluster-lock", "status"]) == 0
    assert capsys.readouterr().out == f"free ({lock.LOCK_FILE})\n"
    cli.run(["cluster-lock", "--who", "kees", "take", "upgrade"])
    assert capsys.readouterr().out == "taken; release with: podiumd-minikube cluster-lock --who kees release\n"
    cli.run(["cluster-lock", "status"])
    assert capsys.readouterr().out.startswith("held by kees (upgrade) for 0m")
    cli.run(["cluster-lock", "--who", "other", "release"])
    assert capsys.readouterr().out.startswith("not held by other: kees (upgrade)")
    cli.run(["cluster-lock", "--who", "kees", "release"])
    assert capsys.readouterr().out == "released\n"
    cli.run(["cluster-lock", "--who", "kees", "release"])
    assert capsys.readouterr().out == "not held by kees: free\n"


def test_cluster_lock_take_by_the_current_user_needs_no_who(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    cli.run(["cluster-lock", "take", "upgrade"])
    assert capsys.readouterr().out == "taken; release with: podiumd-minikube cluster-lock release\n"
    hold = lock.current()
    assert hold is not None
    assert hold.who == lock.person()


@pytest.mark.parametrize(("answer", "last_line"), [("yes", "removed"), ("no", "Aborted - nothing was deleted.")])
def test_cluster_lock_break_asks_first(
    answer: str, last_line: str, private_lock: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    cli.run(["cluster-lock", "break"])
    assert capsys.readouterr().out == "free\n"
    private_lock.write_text("kees upgrade\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    cli.run(["cluster-lock", "break"])
    assert private_lock.exists() is (answer != "yes")
    assert capsys.readouterr().out.endswith(f"{last_line}\n")


def test_cluster_lock_break_yes_skips_the_question(private_lock: Path):
    private_lock.write_text("kees upgrade\n", encoding="utf-8")
    cli.run(["cluster-lock", "break", "--yes"])
    assert not private_lock.exists()


@pytest.mark.parametrize("separator", [["--"], []])
def test_cluster_lock_run_returns_the_commands_exit_code(separator: list[str], fake_run: FakeRun):
    fake_run.on("make", returncode=3)
    assert cli.run(["cluster-lock", "run", "build", *separator, "make", "all"]) == 3
    assert fake_run.calls == [["make", "all"]]


def test_cluster_lock_run_needs_a_command(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit):
        cli.run(["cluster-lock", "run", "build", "--"])
    assert "run needs a command after --" in capsys.readouterr().err


@pytest.mark.parametrize("entry", [cli.main, lambda: runpy.run_module("podiumd_minikube", run_name="__main__")])
def test_main_runs_the_command_named_in_argv(entry: Callable[[], object], monkeypatch: pytest.MonkeyPatch):
    shown = []
    monkeypatch.setattr(sys, "argv", ["podiumd-minikube", "show-podiumd-version"])
    monkeypatch.setattr(versions, "show", lambda: shown.append(True))
    with pytest.raises(SystemExit) as exited:
        entry()
    assert exited.value.code == 0
    assert shown == [True]
