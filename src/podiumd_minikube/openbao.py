"""OpenBao for the frankgateway profile: its seal key, podiumd's openbao-activate.sh, and the outway's keys.

The chart's static seal unseals OpenBao on every start. podiumd's scripts/openbao-activate.sh does the rest after a
deploy (initialise, tokens, the openbao-config Job), as podiumd-infra runs it; its Azure Key Vault item lives in
the keyvault stand-in. The outway's API keys follow with a temporary root token from that item, as podiumd-infra's
write_outway_keys. The seal key stays in .openbao/ (gitignored), like .pki/.
"""

import base64
import json
import os
import secrets
import subprocess  # nosec B404 - only for the CompletedProcess type
import tempfile
import time

from pathlib import Path
from typing import Any

from podiumd_minikube import dependency
from podiumd_minikube import keyvault
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import polling
from podiumd_minikube import process
from podiumd_minikube import values
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import OPENBAO_DIR
from podiumd_minikube.paths import PODIUMD_RELEASE
from podiumd_minikube.paths import PROFILE

SEAL_KEY = OPENBAO_DIR / "seal-key"
POD = f"{PODIUMD_RELEASE}-openbao-0"
# Pod 0's own listener: the -active Service has no endpoints until OpenBao is unsealed.
ADDR = "http://127.0.0.1:8200"
# The loopback listener of the chart's server.ha.config: only there is generate-root allowed without a token.
LOCAL_ADDR = "http://127.0.0.1:8210"
CONFIG_JOB = "openbao-config"
STARTUP_TIMEOUT = 300
SCRIPT = "openbao-activate.sh"
HELM_CHARTS = "https://github.com/Dimpact-Samenwerking/helm-charts.git"
# The script's KEYVAULT_ITEM default, and its value before the first init.
KEY_ITEM = "openbao-unseal-key"
PLACEHOLDER = "pending-init"
# The Frank!Gateway outway reads its API keys at request time from KV_MOUNT/OUTWAY_KV_PATH
# (files/frankgateway/openbao-secret-header.lua) with the token the script mints.
OUTWAY = "frankgateway-outway"
KV_MOUNT = "secret"
OUTWAY_KV_PATH = "frankgateway"


def _write_private(path: Path, value: str) -> None:
    OPENBAO_DIR.mkdir(mode=0o700, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def forget_vault() -> None:
    """Moves the recovery key aside once the vault's database is gone; keeps the seal key."""
    path = keyvault.item(KEY_ITEM)
    if path.is_file():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path.rename(path.with_name(f"{path.name}.{stamp}"))
        print(f"Moved {path.name} aside ({path.name}.{stamp}): its vault was deleted with Postgres.")


def seal_key_args() -> list[str]:
    """helm args setting the static seal key from .openbao/seal-key, created once (32 characters)."""
    if not SEAL_KEY.is_file():
        _write_private(SEAL_KEY, secrets.token_hex(16))
    return ["--set-file", f"podiumd.openbao.seal.static.key={SEAL_KEY}"]


def _bao(
    *args: str, token: str = "", stdin: str = "", addr: str = ADDR, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """`bao args` in pod 0; the token travels as stdin's first line, never on a command line or in an error."""
    script = 'read -r BAO_TOKEN; [ -n "$BAO_TOKEN" ] && export BAO_TOKEN; exec bao "$@"'
    command = ["kubectl", "exec", "-i", "-n", NAMESPACE, POD, "--", "env", f"BAO_ADDR={addr}", "sh", "-c", script]
    return process.run([*command, "sh", *args], stdin=f"{token}\n{stdin}", check=check)


def _status() -> dict[str, Any] | None:
    """`bao status` of pod 0; None while it does not answer."""
    result = _bao("status", "-format=json", check=False)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def unsealed() -> bool:
    """Whether pod 0 answers initialised and unsealed."""
    status = _status()
    return bool(status) and status.get("initialized") is True and status.get("sealed") is False


def activate_script(work: Path) -> Path:
    """The selected podiumd chart's openbao-activate.sh: in its directory, else from helm-charts tag podiumd-<version>.

    A published chart leaves scripts/ out (its .helmignore).
    """
    selected = dependency.selection("podiumd")
    if selected is not None and selected.mode == "path":
        found = Path(selected.value) / "scripts" / SCRIPT
    elif selected is not None:
        clone = ["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse"]
        process.fetch([*clone, "--branch", f"podiumd-{selected.value}", HELM_CHARTS, str(work)])
        process.run(["git", "-C", str(work), "sparse-checkout", "set", "charts/podiumd/scripts"])
        found = work / "charts" / "podiumd" / "scripts" / SCRIPT
    else:
        raise dependency.missing_error(["podiumd"])
    if not found.is_file():
        msg = (
            f"podiumd {selected.value} has no scripts/{SCRIPT} (from podiumd 4.10.0 on): turn off profile frankgateway"
        )
        raise process.UserError(msg)
    return found


def _root_token() -> str:
    """A temporary root token from the recovery key (generate-root); the caller revokes it."""
    attempt = json.loads(_bao("read", "-format=json", "sys/generate-root/attempt", addr=LOCAL_ADDR).stdout)
    if attempt["data"].get("started"):
        msg = f"a generate-root attempt runs on {POD}: cancel it with `bao operator generate-root -cancel` there"
        raise process.UserError(msg)
    started = json.loads(_bao("write", "-f", "-format=json", "sys/generate-root/attempt", addr=LOCAL_ADDR).stdout)
    nonce, otp = started["data"]["nonce"], started["data"]["otp"]
    key = keyvault.item(KEY_ITEM).read_text(encoding="utf-8")
    update = _bao(
        "write", "-format=json", "sys/generate-root/update", f"nonce={nonce}", "key=-", stdin=key, addr=LOCAL_ADDR
    )
    encoded = json.loads(update.stdout)["data"]["encoded_token"]
    raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4))
    return bytes(a ^ b for a, b in zip(raw, otp.encode(), strict=False)).decode()


def _write_outway_keys() -> None:
    """Stores the outway's API keys in OpenBao with a temporary root token, revoked afterwards."""
    root = _root_token()
    try:
        _bao(
            "kv",
            "put",
            f"-mount={KV_MOUNT}",
            OUTWAY_KV_PATH,
            "kvk_api_key=-",
            token=root,
            stdin=values.kvk_test_api_key(),
        )
    finally:
        _bao("token", "revoke", "-self", token=root)
    print(f"  stored the outway's API keys in {KV_MOUNT}/{OUTWAY_KV_PATH}")


def activate(render: manifests.Render) -> None:
    """Runs podiumd's openbao-activate.sh against the keyvault stand-in, then stores the outway's API keys."""
    # The render, not the cluster: a pruned StatefulSet still exists while it terminates.
    if not any(doc.get("kind") == "Job" and manifests.name_of(doc) == CONFIG_JOB for doc in render.docs):
        return
    print(f"Activating OpenBao (podiumd's {SCRIPT}, Key Vault item {KEY_ITEM} in {keyvault.KEYVAULT_DIR})...")
    if polling.wait_until(_status, timeout=STARTUP_TIMEOUT, interval=5) is None:
        msg = f"OpenBao pod {POD} did not answer within {STARTUP_TIMEOUT}s: check `kubectl logs {POD}`"
        raise process.UserError(msg)
    if not keyvault.item(KEY_ITEM).is_file():
        keyvault.write(KEY_ITEM, PLACEHOLDER)
    with tempfile.TemporaryDirectory() as work:
        found = activate_script(Path(work) / "helm-charts")
        settings = {"KUBE_CONTEXT": PROFILE, "NAMESPACE": NAMESPACE, "RELEASE": PODIUMD_RELEASE, "KEYVAULT": PROFILE}
        path = f"{keyvault.shim_dir(Path(work))}{os.pathsep}{os.environ['PATH']}"
        assignments = [f"{key}={value}" for key, value in {**settings, "PATH": path}.items()]
        process.run(["env", *assignments, "bash", str(found)], capture=False)
    if kube.exists(f"deployment/{OUTWAY}"):
        _write_outway_keys()
