"""podiumd_minikube.process: running commands and the entry point's exit codes."""

import subprocess
import sys

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import process
from podiumd_minikube.process import ProcessError
from podiumd_minikube.process import UserError

PYTHON = [sys.executable, "-c"]


def test_run_captures_text_and_merges_stderr_in_order():
    script = "import sys; print('out', flush=True); print('err', file=sys.stderr)"
    assert process.run([*PYTHON, script]).stdout == "out\n"
    assert process.run([*PYTHON, script], merge_stderr=True).stdout == "out\nerr\n"
    assert process.output([*PYTHON, "print(input())"], stdin="hi\n") == "hi\n"


def test_run_raises_on_a_non_zero_exit_unless_unchecked():
    script = "import sys; sys.exit('broken')"
    with pytest.raises(ProcessError, match=r"failed \(1\): broken$") as raised:
        process.run([*PYTHON, script])
    assert raised.value.returncode == 1
    assert process.run([*PYTHON, script], check=False).returncode == 1
    assert not process.succeeds([*PYTHON, script])
    assert process.succeeds([*PYTHON, "pass"])


@pytest.mark.parametrize(("returncode", "stdout", "pid"), [(0, "12\n34\n", "12"), (0, "", ""), (1, "", "")])
def test_running_is_the_first_matching_pid(returncode: int, stdout: str, pid: str, fake_run: FakeRun):
    fake_run.on("pgrep", returncode=returncode, stdout=stdout)
    assert process.running("kubectl port-forward") == pid
    assert fake_run.calls == [["pgrep", "-f", "kubectl port-forward"]]


def test_spawn_detaches_with_output_to_the_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **options: started.append((args, options)))
    log = tmp_path / "spawn.log"
    process.spawn(("minikube", "tunnel"), log)
    assert len(started) == 1
    args, options = started[0]
    assert args == ["minikube", "tunnel"]
    assert options["stdout"].name == str(log)
    assert options["stderr"] == subprocess.STDOUT
    assert options["stdin"] == subprocess.DEVNULL
    assert options["start_new_session"]


def _raise(error: BaseException) -> None:
    raise error


@pytest.mark.parametrize(
    ("entry", "code", "stderr"),
    [
        (lambda: None, 0, ""),
        (lambda: 3, 3, ""),
        (lambda: _raise(UserError("no cluster")), 1, "ERROR: no cluster\n"),
        (lambda: _raise(KeyboardInterrupt()), 130, ""),
    ],
)
def test_main_exits_with_the_entrys_code(entry, code: int, stderr: str, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exited:
        process.main(entry)
    assert exited.value.code == code
    assert capsys.readouterr().err == stderr


def test_fetch_retries_a_network_error_until_it_answers(monkeypatch: pytest.MonkeyPatch):
    answers = iter([ProcessError(["helm"], 1, "lookup helm.cilium.io: i/o timeout"), "index"])

    def run(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        answer = next(answers)
        if isinstance(answer, ProcessError):
            raise answer
        return subprocess.CompletedProcess(args, 0, answer, "")

    monkeypatch.setattr(process, "run", run)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    assert process.fetch(["helm"]) == "index"


def test_fetch_raises_other_errors_at_once_and_the_last_network_error_at_the_deadline(
    fake_run: FakeRun, poll_once: None
):
    fake_run.on("helm", "template", returncode=1, stderr="Error: chart not found")
    with pytest.raises(ProcessError, match="chart not found"):
        process.fetch(["helm", "template"])
    fake_run.on("helm", "show", returncode=1, stderr="dial tcp: no such host")
    with pytest.raises(ProcessError, match="no such host"):
        process.fetch(["helm", "show"])
    assert fake_run.calls == [["helm", "template"], ["helm", "show"]]


def test_fetch_shown_prints_its_output_after_success(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]):
    fake_run.on("helm", "upgrade", stdout="deployed\n", stderr="WARNING: x\n")
    assert process.fetch(["helm", "upgrade"], shown=True) == "deployed\n"
    assert capsys.readouterr() == ("deployed\n", "WARNING: x\n")
