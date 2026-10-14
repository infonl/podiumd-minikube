"""podiumd_minikube.openbao: the local vault files and when bootstrap runs."""

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import manifests
from podiumd_minikube import openbao


def test_forget_vault_moves_token_and_key_aside_and_keeps_the_seal_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("ROOT_TOKEN", "RECOVERY_KEY", "SEAL_KEY"):
        monkeypatch.setattr(openbao, name, tmp_path / getattr(openbao, name).name)
    for path in (openbao.ROOT_TOKEN, openbao.RECOVERY_KEY, openbao.SEAL_KEY):
        path.write_text("x", encoding="utf-8")
    openbao.forget_vault()
    assert not openbao.ROOT_TOKEN.exists()
    assert not openbao.RECOVERY_KEY.exists()
    assert openbao.SEAL_KEY.exists()
    assert len(list(tmp_path.glob("root-token.*"))) == 1


def test_bootstrap_skips_a_render_without_openbao_while_its_statefulset_terminates(fake_run: FakeRun):
    openbao.bootstrap(
        manifests.Render(docs=[{"kind": "Job", "metadata": {"name": "openzaak-config"}}], large_configmaps=[])
    )
    assert fake_run.calls == []
