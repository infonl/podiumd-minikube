"""lib.pki: the local CA and its cert-manager issuer."""

from pathlib import Path

import pytest
import yaml

from conftest import FakeRun

from lib import pki


@pytest.fixture
def pki_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pki, "PKI_DIR", tmp_path / ".pki")
    monkeypatch.setattr(pki, "CA_CERT", tmp_path / ".pki" / "ca.crt")
    monkeypatch.setattr(pki, "CA_KEY", tmp_path / ".pki" / "ca.key")
    return tmp_path / ".pki"


def test_ensure_ca_creates_a_ca_once_with_a_private_key(pki_dir: Path):
    assert pki.ensure_ca()
    assert (pki_dir / "ca.key").stat().st_mode & 0o777 == 0o600
    assert "BEGIN CERTIFICATE" in (pki_dir / "ca.crt").read_text(encoding="utf-8")
    assert not pki.ensure_ca()


def test_issuer_manifests_carry_the_ca(pki_dir: Path, fake_run: FakeRun):
    pki_dir.mkdir()
    (pki_dir / "ca.crt").write_text("CERT", encoding="utf-8")
    (pki_dir / "ca.key").write_text("KEY", encoding="utf-8")
    secret, issuer = yaml.safe_load_all(pki.issuer_manifests())
    assert secret["stringData"] == {"tls.crt": "CERT", "tls.key": "KEY"}
    assert issuer["spec"]["ca"]["secretName"] == secret["metadata"]["name"] == pki.ISSUER
