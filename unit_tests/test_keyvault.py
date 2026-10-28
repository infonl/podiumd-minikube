"""podiumd_minikube.keyvault: the `az keyvault secret` calls of podiumd's openbao-activate.sh."""

import subprocess

from pathlib import Path

import pytest

from podiumd_minikube import keyvault

SECRET = ["keyvault", "secret"]
ITEM = ["--vault-name", "minikube", "--name", "openbao-unseal-key"]


@pytest.fixture(autouse=True)
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "keyvault"
    monkeypatch.setattr(keyvault, "KEYVAULT_DIR", directory)
    return directory


def test_show_prints_a_stored_item(capsys: pytest.CaptureFixture[str]):
    keyvault.write("openbao-unseal-key", "pending-init")
    assert keyvault.main([*SECRET, "show", *ITEM, "--query", "value", "-o", "tsv"]) == 0
    assert capsys.readouterr().out == "pending-init\n"


def test_set_stores_the_file_privately(vault: Path, tmp_path: Path):
    source = tmp_path / "key"
    source.write_text("recovery", encoding="utf-8")
    args = [*SECRET, "set", *ITEM, "--file", str(source), "--encoding", "utf-8", "--tags", "openbao=recovery-key"]
    assert keyvault.main([*args, "-o", "none"]) == 0
    assert keyvault.item("openbao-unseal-key").read_text(encoding="utf-8") == "recovery"
    assert keyvault.item("openbao-unseal-key").stat().st_mode & 0o777 == 0o600
    assert vault.stat().st_mode & 0o777 == 0o700


def test_set_attributes_needs_the_item():
    args = [*SECRET, "set-attributes", *ITEM, "--tags", "openbao=pending-init", "-o", "none"]
    assert keyvault.main(args) == 1
    keyvault.write("openbao-unseal-key", "pending-init")
    assert keyvault.main(args) == 0


@pytest.mark.parametrize("argv", [["account", "show"], [*SECRET, "delete", *ITEM], [*SECRET, "show", "--name", "x"]])
def test_other_calls_fail(argv: list[str], capsys: pytest.CaptureFixture[str]):
    assert keyvault.main(argv) == 1
    assert "does not support" in capsys.readouterr().err


def test_shim_dir_runs_this_module(tmp_path: Path):
    keyvault.write("openbao-unseal-key", "x")
    az = keyvault.shim_dir(tmp_path) / "az"
    result = subprocess.run([str(az), "account", "show"], capture_output=True, text=True, check=False)
    assert (result.returncode, "does not support" in result.stderr) == (1, True)
