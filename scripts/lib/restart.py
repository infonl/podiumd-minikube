"""Brings a stopped cluster back (after a reboot or `minikube stop`): node, apps, ZAC and the tunnel."""

from typing import Any

from lib import deploy
from lib import kube
from lib import lock
from lib import polling
from lib import process
from lib import provision
from lib import tunnel
from lib.paths import NAMESPACE

ZAC = "zac"
# WildFly gives up on ZAC's deployment when Open Zaak answers 502 during its boot,
# and the liveness probe restarts it only after 16 x 30 s.
BOOT_FAILED = "WFLYCTL0080"


def _workloads() -> list[dict[str, Any]]:
    return kube.get_json("deployment,statefulset", "-n", NAMESPACE)["items"]


def zac_state() -> str | None:
    """ "ready", "failed" (its last boot failed) or None while it boots."""
    zac = next(item for item in _workloads() if item["metadata"]["name"] == ZAC)
    if deploy.rolled_out(zac):
        return "ready"
    result = process.run(["kubectl", "logs", "-n", NAMESPACE, f"deployment/{ZAC}", "-c", ZAC], check=False)
    return "failed" if BOOT_FAILED in result.stdout else None


@lock.holding("start-cluster")
def start() -> None:
    """Starts the node, waits for the apps, restarts ZAC once if its boot failed, then starts the tunnel."""
    provision.start_node()
    print("\nWaiting until every workload except ZAC is ready...")

    def others_ready() -> bool:
        return all(deploy.rolled_out(item) for item in _workloads() if item["metadata"]["name"] != ZAC)

    if not polling.wait_until(others_ready, timeout=deploy.READY_TIMEOUT, interval=10):
        stuck = [item["metadata"]["name"] for item in _workloads() if not deploy.rolled_out(item)]
        msg = f"not ready after {deploy.READY_TIMEOUT}s: {', '.join(stuck)}: check `kubectl get pods -n {NAMESPACE}`"
        raise process.UserError(msg)
    if polling.wait_until(zac_state, timeout=deploy.READY_TIMEOUT, interval=10) == "failed":
        print("ZAC's boot failed while Open Zaak was down; restarting it.")
        kube.kubectl_shown("rollout", "restart", f"deployment/{ZAC}", "-n", NAMESPACE)
        if polling.wait_until(lambda: zac_state() == "ready", timeout=deploy.READY_TIMEOUT, interval=10) is None:
            msg = f"ZAC not ready after {deploy.READY_TIMEOUT}s: check `kubectl logs deployment/{ZAC} -n {NAMESPACE}`"
            raise process.UserError(msg)
    print("All workloads ready.\n")
    tunnel.setup_tunnel()
