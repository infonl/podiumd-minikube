"""podiumd_minikube.openbao: the local vault files and when bootstrap runs."""

import json

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import manifests
from podiumd_minikube import openbao
from podiumd_minikube import process
from podiumd_minikube import values
from podiumd_minikube.paths import NAMESPACE

SCRIPT = 'read -r BAO_TOKEN; [ -n "$BAO_TOKEN" ] && export BAO_TOKEN; exec bao "$@"'
BAO = (
    "kubectl",
    "exec",
    "-i",
    "-n",
    NAMESPACE,
    openbao.POD,
    "--",
    "env",
    f"BAO_ADDR={openbao.ADDR}",
    "sh",
    "-c",
    SCRIPT,
    "sh",
)
RENDER = manifests.Render(docs=[{"kind": "Job", "metadata": {"name": openbao.CONFIG_JOB}}], large_configmaps=[])


@pytest.fixture
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / ".openbao"
    monkeypatch.setattr(openbao, "OPENBAO_DIR", directory)
    for name in ("ROOT_TOKEN", "RECOVERY_KEY", "SEAL_KEY"):
        monkeypatch.setattr(openbao, name, directory / getattr(openbao, name).name)
    return directory


@pytest.fixture
def cluster(fake_run: FakeRun, vault_dir: Path, poll_once: None, monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    """fake_run with an uninitialised OpenBao that becomes active; polling asks once."""
    monkeypatch.setattr(values, "kvk_test_api_key", lambda: "kvk-key")
    fake_run.on(*BAO, "status", stdout=json.dumps({"initialized": False, "is_self": True}))
    fake_run.on(*BAO, "operator", "init", stdout=json.dumps({"root_token": "root", "recovery_keys_b64": ["recovery"]}))
    fake_run.on(*BAO, "token", "create", stdout="minted\n")
    return fake_run


def test_forget_vault_moves_token_and_key_aside_and_keeps_the_seal_key(vault_dir: Path):
    vault_dir.mkdir()
    for path in (openbao.ROOT_TOKEN, openbao.RECOVERY_KEY, openbao.SEAL_KEY):
        path.write_text("x", encoding="utf-8")
    openbao.forget_vault()
    assert not openbao.ROOT_TOKEN.exists()
    assert not openbao.RECOVERY_KEY.exists()
    assert openbao.SEAL_KEY.exists()
    assert len(list(vault_dir.glob("root-token.*"))) == 1


def test_seal_key_args_create_a_private_key_once(vault_dir: Path):
    args = openbao.seal_key_args()
    key = openbao.SEAL_KEY.read_text(encoding="utf-8")
    assert args == ["--set-file", f"podiumd.openbao.seal.static.key={openbao.SEAL_KEY}"]
    assert len(key) == 32
    assert openbao.SEAL_KEY.stat().st_mode & 0o777 == 0o600
    assert vault_dir.stat().st_mode & 0o777 == 0o700
    openbao.seal_key_args()
    assert openbao.SEAL_KEY.read_text(encoding="utf-8") == key


def test_bootstrap_skips_a_render_without_openbao_while_its_statefulset_terminates(fake_run: FakeRun):
    assert not openbao.bootstrap(
        manifests.Render(docs=[{"kind": "Job", "metadata": {"name": "openzaak-config"}}], large_configmaps=[])
    )
    assert fake_run.calls == []


def _secrets(cluster: FakeRun) -> list[tuple[str, str]]:
    applied = zip(cluster.calls, cluster.stdins, strict=True)
    secrets = [json.loads(stdin or "") for call, stdin in applied if call == ["kubectl", "apply", "-f", "-"]]
    return [(s["metadata"]["name"], s["stringData"]["token"]) for s in secrets]


def test_bootstrap_initialises_mints_and_reruns_the_job_without_waiting(cluster: FakeRun):
    cluster.on("kubectl", "get", f"secret/{openbao.BOOTSTRAP_SECRET}", returncode=1)
    assert openbao.bootstrap(RENDER)
    assert openbao.ROOT_TOKEN.read_text(encoding="utf-8") == "root"
    assert openbao.RECOVERY_KEY.read_text(encoding="utf-8") == "recovery"
    assert not [call for call in cluster.calls if "root" in call]
    stdins = dict(zip(map(tuple, cluster.calls), cluster.stdins, strict=True))
    assert stdins[(*BAO, "policy", "write", openbao.POLICY, "-")] == f"root\n{openbao.POLICY_HCL}"
    assert _secrets(cluster) == [(openbao.BOOTSTRAP_SECRET, "minted")]
    assert cluster.ran("kubectl", "delete", "job", openbao.CONFIG_JOB)
    assert cluster.ran("kubectl", "apply", "-n", NAMESPACE)
    assert not cluster.ran("kubectl", "wait")
    assert not cluster.ran(*BAO, "kv", "put")


def test_bootstrap_keeps_an_initialised_vault_and_existing_tokens(cluster: FakeRun):
    openbao.OPENBAO_DIR.mkdir()
    openbao.ROOT_TOKEN.write_text("root\n", encoding="utf-8")
    cluster.on(*BAO, "status", stdout=json.dumps({"initialized": True, "is_self": True}))
    openbao.bootstrap(RENDER)
    assert not cluster.ran(*BAO, "operator")
    assert not cluster.ran(*BAO, "token")
    assert cluster.ran("kubectl", "apply", "-n", NAMESPACE)


def test_wire_outway_stores_the_key_and_mints_the_reader_token_once(cluster: FakeRun):
    openbao.OPENBAO_DIR.mkdir()
    openbao.ROOT_TOKEN.write_text("root\n", encoding="utf-8")
    cluster.on("kubectl", "get", f"secret/{openbao.READER_SECRET}", returncode=1)
    openbao.wire_outway()
    stdins = dict(zip(map(tuple, cluster.calls), cluster.stdins, strict=True))
    assert stdins[(*BAO, "kv", "put", "-mount=secret", "frankgateway", "kvk_api_key=-")] == "root\nkvk-key"
    assert _secrets(cluster) == [(openbao.READER_SECRET, "minted")]
    assert cluster.ran("kubectl", "rollout", "restart", f"deployment/{openbao.OUTWAY}")


def test_wire_outway_keeps_an_existing_reader_token(cluster: FakeRun):
    openbao.OPENBAO_DIR.mkdir()
    openbao.ROOT_TOKEN.write_text("root\n", encoding="utf-8")
    openbao.wire_outway()
    assert cluster.ran(*BAO, "kv", "put")
    assert not cluster.ran(*BAO, "token")
    assert not cluster.ran("kubectl", "rollout")


def test_wire_outway_skips_a_render_without_the_outway(cluster: FakeRun):
    cluster.on("kubectl", "get", f"deployment/{openbao.OUTWAY}", returncode=1)
    openbao.wire_outway()
    assert not cluster.ran(*BAO)


@pytest.mark.parametrize(
    ("status", "old_root_token", "error"),
    [
        ("", "", "did not answer within"),
        (json.dumps({"initialized": False}), "old", "an old vault's"),
        (json.dumps({"initialized": True, "is_self": False}), "", "did not become active"),
    ],
)
def test_bootstrap_refuses(cluster: FakeRun, status: str, old_root_token: str, error: str):
    if old_root_token:
        openbao.OPENBAO_DIR.mkdir()
        openbao.ROOT_TOKEN.write_text(old_root_token, encoding="utf-8")
    cluster.on(*BAO, "status", stdout=status)
    with pytest.raises(process.UserError, match=error):
        openbao.bootstrap(RENDER)
    assert not cluster.ran("kubectl", "delete")
