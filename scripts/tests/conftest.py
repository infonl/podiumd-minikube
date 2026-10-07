"""Fakes for the external processes lib.process starts."""

import subprocess
import tarfile

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from io import BytesIO
from pathlib import Path

import pytest

from lib import process


@dataclass
class FakeRun:
    """Stands in for lib.process.run: records argv lists, answers by argv prefix."""

    answers: list[tuple[tuple[str, ...], int, str, str]] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)
    stdins: list[str | None] = field(default_factory=list)

    def on(self, *prefix: str, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        """Answers commands starting with prefix (latest registration wins)."""
        self.answers.insert(0, (prefix, returncode, stdout, stderr))

    def __call__(
        self,
        args: Sequence[str],
        *,
        stdin: str | None = None,
        check: bool = True,
        **_output_options: bool,
    ) -> subprocess.CompletedProcess[str]:
        argv = list(args)
        self.calls.append(argv)
        self.stdins.append(stdin)
        returncode, stdout, stderr = 0, "", ""
        for prefix, code, out, err in self.answers:
            if tuple(argv[: len(prefix)]) == prefix:
                returncode, stdout, stderr = code, out, err
                break
        if check and returncode != 0:
            raise process.ProcessError(argv, returncode, stderr)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    def ran(self, *prefix: str) -> list[list[str]]:
        """Recorded calls starting with prefix."""
        return [call for call in self.calls if tuple(call[: len(prefix)]) == prefix]


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """A FakeRun installed as lib.process.run."""
    fake = FakeRun()
    monkeypatch.setattr(process, "run", fake)
    return fake


def make_tgz(path: Path, members: dict[str, str]) -> Path:
    """Writes a .tgz with the given member names and contents."""
    with tarfile.open(path, "w:gz") as archive:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    return path
