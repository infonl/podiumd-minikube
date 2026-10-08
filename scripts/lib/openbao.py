"""OpenBao for the frankgateway profile: its seal key, one-time init, config token and the outway's keys.

The chart's static seal unseals OpenBao on every start, but the vault still
has to be initialised once, and the chart's openbao-config Job needs a token
that only exists after that. ExternalsPodiumD does this with
pipelines/scripts/openbao_bootstrap.py and the chart's
scripts/openbao-mint-config-token.sh; this is the same, unattended. The seal
key, root token and recovery key stay in .openbao/ (gitignored), like .pki/.
"""

import json
import secrets
import subprocess  # nosec B404 - only for the CompletedProcess type
import time

from typing import Any

from lib import kube
from lib import manifests
from lib import polling
from lib import process
from lib import values
from lib.paths import CHART_DIR
from lib.paths import NAMESPACE
from lib.paths import RELEASE_NAME

OPENBAO_DIR = CHART_DIR / ".openbao"
SEAL_KEY = OPENBAO_DIR / "seal-key"
ROOT_TOKEN = OPENBAO_DIR / "root-token"
RECOVERY_KEY = OPENBAO_DIR / "recovery-key"
POD = f"{RELEASE_NAME}-openbao-0"
# Pod 0's own listener: the -active Service has no endpoints until OpenBao is unsealed.
ADDR = "http://127.0.0.1:8200"
# podiumd.openbao.configuration.bootstrapTokenSecret and its Job, in the chart.
BOOTSTRAP_SECRET = "openbao-bootstrap-token"  # nosec B105  # noqa: S105 - a Secret name
CONFIG_JOB = "openbao-config"
POLICY = "podiumd-config-job"
# The chart's scripts/openbao-mint-config-token.sh, for kvPath "secret".
POLICY_HCL = """\
path "sys/mounts"                { capabilities = ["read"] }
path "sys/mounts/secret"         { capabilities = ["create", "read", "update", "sudo"] }
path "sys/policies/acl/uploader" { capabilities = ["create", "read", "update"] }
path "sys/auth"                  { capabilities = ["read"] }
path "sys/auth/oidc"             { capabilities = ["create", "update", "sudo"] }
path "auth/oidc/config"          { capabilities = ["create", "read", "update"] }
path "auth/oidc/role/uploader"   { capabilities = ["create", "read", "update"] }
path "identity/group"            { capabilities = ["create", "update"] }
path "identity/group/name/*"     { capabilities = ["read"] }
path "identity/group-alias"      { capabilities = ["create", "update"] }
path "identity/group-alias/id/*" { capabilities = ["read", "update"] }
"""
STARTUP_TIMEOUT = 300
# The Frank!Gateway outway reads its API keys at request time from KV_MOUNT/OUTWAY_KV_PATH
# (files/frankgateway/openbao-secret-header.lua) with the token in READER_SECRET
# (podiumd.frankgateway.openbao.tokenSecret, which the chart never creates).
OUTWAY = "frankgateway-outway"
KV_MOUNT = "secret"
OUTWAY_KV_PATH = "frankgateway"
READER_SECRET = "frankgateway-openbao-token"  # nosec B105  # noqa: S105 - a Secret name
READER_POLICY = "frankgateway-reader"
# frankgateway-openbao.md: the path itself and everything below it.
READER_POLICY_HCL = f"""\
path "{KV_MOUNT}/data/{OUTWAY_KV_PATH}"   {{ capabilities = ["read"] }}
path "{KV_MOUNT}/data/{OUTWAY_KV_PATH}/*" {{ capabilities = ["read"] }}
"""


def _write_private(path_name: str, value: str) -> None:
    OPENBAO_DIR.mkdir(mode=0o700, exist_ok=True)
    path = OPENBAO_DIR / path_name
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def forget_vault() -> None:
    """Moves the root token and recovery key aside once the vault's database is gone; keeps the seal key."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path in (ROOT_TOKEN, RECOVERY_KEY):
        if path.is_file():
            path.rename(path.with_name(f"{path.name}.{stamp}"))
            print(f"Moved {path.name} aside ({path.name}.{stamp}): its vault was deleted with Postgres.")


def seal_key_args() -> list[str]:
    """helm args setting the static seal key from .openbao/seal-key, created once (32 characters)."""
    if not SEAL_KEY.is_file():
        _write_private(SEAL_KEY.name, secrets.token_hex(16))
    return ["--set-file", f"podiumd.openbao.seal.static.key={SEAL_KEY}"]


def _bao(*args: str, token: str = "", stdin: str = "", check: bool = True) -> subprocess.CompletedProcess[str]:
    """`bao args` in pod 0; the token travels as stdin's first line, never on a command line or in an error."""
    script = 'read -r BAO_TOKEN; [ -n "$BAO_TOKEN" ] && export BAO_TOKEN; exec bao "$@"'
    command = ["kubectl", "exec", "-i", "-n", NAMESPACE, POD, "--", "env", f"BAO_ADDR={ADDR}", "sh", "-c", script]
    return process.run([*command, "sh", *args], stdin=f"{token}\n{stdin}", check=check)


def _status() -> dict[str, Any] | None:
    """`bao status` of pod 0; None while it does not answer."""
    result = _bao("status", "-format=json", check=False)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _initialise() -> None:
    print("Initialising OpenBao (once; recovery key and root token go to .openbao/)...")
    out = json.loads(_bao("operator", "init", "-recovery-shares=1", "-recovery-threshold=1", "-format=json").stdout)
    _write_private(ROOT_TOKEN.name, out["root_token"])
    _write_private(RECOVERY_KEY.name, out["recovery_keys_b64"][0])


def _mint_token(root: str, policy: str, hcl: str, secret_name: str) -> None:
    """Writes policy, creates an orphan periodic token with it and stores it as key `token` of Secret secret_name."""
    _bao("policy", "write", policy, "-", token=root, stdin=hcl)
    token = _bao(
        "token", "create", "-orphan", f"-policy={policy}", "-period=768h", f"-display-name={policy}", "-field=token",
        token=root,
    ).stdout.strip()  # fmt: skip
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret_name, "namespace": NAMESPACE},
        "stringData": {"token": token},
    }
    kube.kubectl("apply", "-f", "-", stdin=json.dumps(secret))
    print(f"  minted a {policy} token into Secret {secret_name}")


def _wire_outway(root: str) -> None:
    """Stores the outway's API keys in OpenBao and gives the outway its reader token (restarting it once)."""
    _bao(
        "kv", "put", f"-mount={KV_MOUNT}", OUTWAY_KV_PATH, "kvk_api_key=-", token=root, stdin=values.kvk_test_api_key()
    )
    if kube.exists(f"secret/{READER_SECRET}"):
        return
    _mint_token(root, READER_POLICY, READER_POLICY_HCL, READER_SECRET)
    # OPENBAO_TOKEN comes from that Secret as an env var, read at start only.
    kube.kubectl_shown("rollout", "restart", f"deployment/{OUTWAY}", "-n", NAMESPACE)


def bootstrap(render: manifests.Render) -> None:
    """Initialises OpenBao once, mints the config token once, re-runs the openbao-config Job, wires the outway."""
    if not kube.exists(f"statefulset/{RELEASE_NAME}-openbao"):
        return
    print("Bootstrapping OpenBao (see scripts/lib/openbao.py)...")
    status = polling.wait_until(_status, timeout=STARTUP_TIMEOUT, interval=5)
    if status is None:
        msg = f"OpenBao pod {POD} did not answer within {STARTUP_TIMEOUT}s: check `kubectl logs {POD}`"
        raise process.UserError(msg)
    if not status["initialized"]:
        if ROOT_TOKEN.is_file():
            msg = f"OpenBao is not initialised but {ROOT_TOKEN} exists: an old vault's; move it away and redeploy"
            raise process.UserError(msg)
        _initialise()
    # With HA storage an unsealed node only accepts writes once it holds the active lock.
    if polling.wait_until(lambda: (s := _status()) is not None and s.get("is_self"), timeout=120, interval=3) is None:
        msg = f"OpenBao did not become active: check the seal key in {SEAL_KEY} and `kubectl logs {POD}`"
        raise process.UserError(msg)
    root = ROOT_TOKEN.read_text(encoding="utf-8").strip()
    if not kube.exists(f"secret/{BOOTSTRAP_SECRET}"):
        _mint_token(root, POLICY, POLICY_HCL, BOOTSTRAP_SECRET)
    jobs = [doc for doc in render.docs if doc.get("kind") == "Job" and manifests.name_of(doc) == CONFIG_JOB]
    kube.kubectl_shown("delete", "job", CONFIG_JOB, "-n", NAMESPACE, "--ignore-not-found")
    kube.kubectl_shown("apply", "-n", NAMESPACE, "-f", "-", stdin=manifests.dump(jobs))
    kube.kubectl_shown("wait", "--for=condition=complete", f"job/{CONFIG_JOB}", "-n", NAMESPACE, "--timeout=180s")
    if kube.exists(f"deployment/{OUTWAY}"):
        _wire_outway(root)
