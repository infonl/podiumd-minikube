"""podiumd_minikube.objecten: the objecttypes import before podiumd's upgrade to Open Object."""

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import dependency
from podiumd_minikube import manifests
from podiumd_minikube import objecten
from podiumd_minikube import process


def _render(*names: str) -> manifests.Render:
    return manifests.Render(
        docs=[{"kind": "Deployment", "metadata": {"name": name}} for name in names], large_configmaps=[]
    )


def test_merging_is_an_upgrade_that_drops_objecttypen():
    classic = _render("objecten", "objecttypen")
    assert objecten.is_merging(classic, _render("objecten"))
    assert not objecten.is_merging(classic, classic)
    assert not objecten.is_merging(_render("objecten"), _render("objecten"))


def test_import_runs_the_snippet_then_the_tokens_script(
    fake_run: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    script = tmp_path / objecten.TOKENS
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(dependency, "podiumd_script", lambda _name, _work: script)
    objecten.import_objecttypes()
    (shell,) = fake_run.ran("kubectl", "exec", "deploy/objecten")
    assert shell[-1] == objecten.IMPORT
    assert fake_run.ran("bash", str(script), "--context", "minikube", "--namespace", "podiumd", "--apply")


def test_import_refuses_a_podiumd_without_the_tokens_script(
    fake_run: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(dependency, "podiumd_script", lambda _name, _work: tmp_path / "missing")
    with pytest.raises(process.UserError, match=r"select podiumd 4\.10\.0 or later"):
        objecten.import_objecttypes()
    assert fake_run.ran("kubectl", "exec")
