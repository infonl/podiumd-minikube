"""podiumd_minikube.openbao: the seal key, which openbao-activate.sh runs and how, and the outway's keys."""

import base64
import json
import os

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import dependency
from podiumd_minikube import keyvault
from podiumd_minikube import manifests
from podiumd_minikube import openbao
from podiumd_minikube import process
from podiumd_minikube import values
from podiumd_minikube.paths import NAMESPACE

SCRIPT = 'read -r BAO_TOKEN; [ -n "$BAO_TOKEN" ] && export BAO_TOKEN; exec bao "$@"'


def _bao(addr: str = openbao.ADDR) -> tuple[str, ...]:
    return ("kubectl", "exec", "-i", "-n", NAMESPACE, openbao.POD, "--", "env", f"BAO_ADDR={addr}", "sh", "-c", SCRIPT, "sh")  # fmt: skip


BAO, LOCAL = _bao(), _bao(openbao.LOCAL_ADDR)
ROOT = "root-token"  # nosec B105 - a fake token
RENDER = manifests.Render(docs=[{"kind": "Job", "metadata": {"name": openbao.CONFIG_JOB}}], large_configmaps=[])


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / ".openbao"
    monkeypatch.setattr(openbao, "OPENBAO_DIR", directory)
    monkeypatch.setattr(openbao, "SEAL_KEY", directory / "seal-key")
    monkeypatch.setattr(keyvault, "KEYVAULT_DIR", directory / "keyvault")
    return directory


@pytest.fixture
def chart_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A podiumd checkout with scripts/openbao-activate.sh, selected by path."""
    chart = tmp_path / "podiumd"
    (chart / "scripts").mkdir(parents=True)
    (chart / "scripts" / openbao.SCRIPT).write_text("", encoding="utf-8")
    monkeypatch.setattr(dependency, "selection", lambda _key: dependency.Selection("path", str(chart)))
    return chart


def _generate_root(cluster: FakeRun) -> None:
    """generate-root answers with root token ROOT encoded against its otp, as OpenBao does."""
    token = ROOT
    otp = "o" * len(token)
    encoded = base64.b64encode(bytes(a ^ b for a, b in zip(token.encode(), otp.encode(), strict=True))).decode()
    cluster.on(*LOCAL, "read", stdout=json.dumps({"data": {"started": False}}))
    cluster.on(*LOCAL, "write", "-f", stdout=json.dumps({"data": {"nonce": "n", "otp": otp}}))
    cluster.on(*LOCAL, "write", "-format=json", stdout=json.dumps({"data": {"encoded_token": encoded.rstrip("=")}}))


@pytest.fixture
def cluster(
    fake_run: FakeRun, vault_dir: Path, chart_dir: Path, poll_once: None, monkeypatch: pytest.MonkeyPatch
) -> FakeRun:
    """fake_run with an answering OpenBao and the outway; polling asks once."""
    monkeypatch.setattr(values, "kvk_test_api_key", lambda: "kvk-key")
    fake_run.on(*BAO, "status", stdout=json.dumps({"initialized": True}))
    _generate_root(fake_run)
    return fake_run


def test_forget_vault_moves_the_key_item_aside_and_keeps_the_seal_key(vault_dir: Path):
    keyvault.write(openbao.KEY_ITEM, "key")
    openbao.seal_key_args()
    openbao.forget_vault()
    assert not keyvault.item(openbao.KEY_ITEM).exists()
    assert len(list((vault_dir / "keyvault").glob(f"{openbao.KEY_ITEM}.*"))) == 1
    assert openbao.SEAL_KEY.exists()


def test_seal_key_args_create_a_private_key_once(vault_dir: Path):
    args = openbao.seal_key_args()
    key = openbao.SEAL_KEY.read_text(encoding="utf-8")
    assert args == ["--set-file", f"podiumd.openbao.seal.static.key={openbao.SEAL_KEY}"]
    assert len(key) == 32
    assert openbao.SEAL_KEY.stat().st_mode & 0o777 == 0o600
    assert vault_dir.stat().st_mode & 0o777 == 0o700
    openbao.seal_key_args()
    assert openbao.SEAL_KEY.read_text(encoding="utf-8") == key


def test_activate_skips_a_render_without_openbao_while_its_statefulset_terminates(fake_run: FakeRun):
    openbao.activate(manifests.Render(docs=[{"kind": "Job", "metadata": {"name": "x"}}], large_configmaps=[]))
    assert fake_run.calls == []


def test_activate_runs_the_charts_script_with_the_keyvault_stand_in(cluster: FakeRun, chart_dir: Path):
    openbao.activate(RENDER)
    assert keyvault.item(openbao.KEY_ITEM).read_text(encoding="utf-8") == openbao.PLACEHOLDER
    (env,) = cluster.ran("env")
    assert env[-2:] == ["bash", str(chart_dir / "scripts" / openbao.SCRIPT)]
    settings = dict(arg.split("=", 1) for arg in env[1:-2])
    assert (settings["KUBE_CONTEXT"], settings["NAMESPACE"], settings["RELEASE"]) == ("minikube", NAMESPACE, "podiumd")
    # The keyvault stand-in's `az` first.
    assert settings["PATH"].split(os.pathsep, 1)[1] == os.environ["PATH"]


def test_activate_keeps_a_stored_key(cluster: FakeRun):
    keyvault.write(openbao.KEY_ITEM, "recovery")
    openbao.activate(RENDER)
    assert keyvault.item(openbao.KEY_ITEM).read_text(encoding="utf-8") == "recovery"


def test_activate_stores_the_outway_keys_with_a_temporary_root_token(cluster: FakeRun):
    keyvault.write(openbao.KEY_ITEM, "recovery")
    openbao.activate(RENDER)
    stdins = dict(zip(map(tuple, cluster.calls), cluster.stdins, strict=True))
    assert stdins[(*LOCAL, "write", "-format=json", "sys/generate-root/update", "nonce=n", "key=-")] == "\nrecovery"
    assert stdins[(*BAO, "kv", "put", "-mount=secret", "frankgateway", "kvk_api_key=-")] == f"{ROOT}\nkvk-key"
    assert stdins[(*BAO, "token", "revoke", "-self")] == f"{ROOT}\n"
    assert not [call for call in cluster.calls if ROOT in call or "recovery" in call]


def test_activate_without_the_outway_writes_no_keys(cluster: FakeRun):
    cluster.on("kubectl", "get", f"deployment/{openbao.OUTWAY}", returncode=1)
    openbao.activate(RENDER)
    assert not cluster.ran(*LOCAL)


def test_activate_refuses_a_running_generate_root_attempt(cluster: FakeRun):
    keyvault.write(openbao.KEY_ITEM, "recovery")
    cluster.on(*LOCAL, "read", stdout=json.dumps({"data": {"started": True}}))
    with pytest.raises(process.UserError, match="generate-root attempt runs"):
        openbao.activate(RENDER)
    assert not cluster.ran(*BAO, "kv")


def test_activate_stops_when_openbao_does_not_answer(cluster: FakeRun):
    cluster.on(*BAO, "status", stdout="")
    with pytest.raises(process.UserError, match="did not answer within"):
        openbao.activate(RENDER)
    assert not cluster.ran("env")


def test_script_of_a_version_comes_from_its_helm_charts_tag(
    fake_run: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(dependency, "selection", lambda _key: dependency.Selection("version", "4.10.0"))
    found = tmp_path / "charts" / "podiumd" / "scripts" / openbao.SCRIPT
    found.parent.mkdir(parents=True)
    found.write_text("", encoding="utf-8")
    assert openbao.activate_script(tmp_path) == found
    (clone,) = fake_run.ran("git", "clone")
    assert clone[-3:] == ["podiumd-4.10.0", openbao.HELM_CHARTS, str(tmp_path)]
    assert fake_run.ran("git", "-C", str(tmp_path), "sparse-checkout", "set", "charts/podiumd/scripts")


def test_script_refuses_a_podiumd_without_it(chart_dir: Path):
    (chart_dir / "scripts" / openbao.SCRIPT).unlink()
    with pytest.raises(process.UserError, match="turn off profile frankgateway"):
        openbao.activate_script(chart_dir)
