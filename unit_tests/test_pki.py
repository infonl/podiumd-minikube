"""podiumd_minikube.pki: the local CA and its cert-manager issuer."""

import base64
import json

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import certifi
import pytest
import yaml

from conftest import FakeRun

from podiumd_minikube import pki
from podiumd_minikube import process
from podiumd_minikube.paths import EDGE_NAMESPACE


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


@pytest.fixture
def openssl(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """fake_run that writes openssl's -keyout and -out files with their flag as content."""

    def run(args: Sequence[str], **options: Any):
        for flag in ("-keyout", "-out"):
            if args[0] == "openssl" and flag in args:
                Path(args[list(args).index(flag) + 1]).write_text(flag, encoding="utf-8")
        return fake_run(args, **options)

    monkeypatch.setattr(process, "run", run)
    return fake_run


@pytest.mark.parametrize("old_release", ["", "cert-manager\n"])
def test_install_cert_manager_skips_the_running_version_and_moves_an_old_release(fake_run: FakeRun, old_release: str):
    fake_run.on("helm", "list", "-n", pki.OLD_CERT_MANAGER_NAMESPACE, stdout=old_release)
    fake_run.on(
        "helm", "list", "-n", EDGE_NAMESPACE, stdout=json.dumps([{"chart": f"cert-manager-{pki.CERT_MANAGER_VERSION}"}])
    )
    pki.install_cert_manager()
    moved = [
        ["helm", "uninstall", "cert-manager", "-n", pki.OLD_CERT_MANAGER_NAMESPACE, "--wait"],
        ["kubectl", "delete", "namespace", pki.OLD_CERT_MANAGER_NAMESPACE, "--wait=true"],
    ]
    assert [call for call in moved if call in fake_run.calls] == (moved if old_release else [])
    assert not fake_run.ran("helm", "upgrade")


def test_install_cert_manager_installs_another_version_with_its_values(fake_run: FakeRun):
    fake_run.on("helm", "list", "-n", EDGE_NAMESPACE, stdout=json.dumps([{"chart": "cert-manager-v1.0.0"}]))
    pki.install_cert_manager()
    (upgrade,) = fake_run.ran("helm", "upgrade", "--install", "cert-manager", "jetstack/cert-manager")
    assert upgrade[upgrade.index("--version") + 1] == pki.CERT_MANAGER_VERSION
    installed = yaml.safe_load(fake_run.stdins[fake_run.calls.index(upgrade)] or "")
    assert installed == pki.cert_manager_values()
    assert installed["webhook"]["image"]["tag"] == pki.CERT_MANAGER_IMAGE_TAG


@pytest.mark.parametrize("returncode", [0, 1])
def test_install_issuer_creates_the_ca_and_applies_until_the_webhook_answers(
    pki_dir: Path, openssl: FakeRun, poll_once: None, returncode: int
):
    openssl.on("kubectl", "apply", returncode=returncode)
    if returncode == 0:
        pki.install_issuer()
        assert openssl.ran("kubectl", "wait", "--for=condition=Ready", f"clusterissuer/{pki.ISSUER}")
    else:
        with pytest.raises(process.ProcessError):
            pki.install_issuer()
    assert (pki_dir / "ca.key").stat().st_mode & 0o777 == 0o600
    applied = [stdin for call, stdin in zip(openssl.calls, openssl.stdins, strict=True) if call[1] == "apply"]
    assert applied
    assert set(applied) == {pki.issuer_manifests()}


def test_apply_trust_applies_the_ca_bundle_and_truststore(pki_dir: Path, openssl: FakeRun):
    pki_dir.mkdir()
    (pki_dir / "ca.crt").write_text("CERT", encoding="utf-8")
    pki.apply_trust("ns")
    (export,) = openssl.ran("openssl", "pkcs12", "-export")
    assert "-jdktrust" in export
    assert f"pass:{pki.TRUSTSTORE_PASSWORD}" in export
    assert openssl.calls[-1] == ["kubectl", "apply", "--server-side", "--force-conflicts", "-n", "ns", "-f", "-"]
    configmap = yaml.safe_load(openssl.stdins[-1] or "")
    assert configmap["metadata"]["namespace"] == "ns"
    assert configmap["data"]["ca.crt"] == "CERT"
    assert configmap["data"]["ca-bundle.pem"] == Path(certifi.where()).read_text(encoding="utf-8") + "CERT"
    assert base64.b64decode(configmap["binaryData"]["truststore.p12"]) == b"-out"
