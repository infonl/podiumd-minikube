"""lib.openbao: the local vault files."""

from pathlib import Path

import pytest

from lib import openbao


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
