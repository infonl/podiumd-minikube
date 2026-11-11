"""podiumd_minikube.timing: phase durations in the log."""

import time

import pytest

from podiumd_minikube import timing


def test_a_timed_block_prints_its_label_and_seconds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    clock = iter([100.0, 142.4])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    with timing.timed("helm upgrade podiumd"):
        print("upgrading")
    assert capsys.readouterr().out == "upgrading\n==== helm upgrade podiumd: 42 s ====\n"


def test_a_failed_block_prints_no_duration(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(RuntimeError), timing.timed("helm upgrade podiumd"):
        raise RuntimeError
    assert not capsys.readouterr().out
