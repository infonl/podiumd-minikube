"""kubectl access to this project's minikube cluster."""

import json

from typing import Any

from lib import polling
from lib import process
from lib.paths import EDGE_NAMESPACE
from lib.paths import EDGE_SERVICE
from lib.paths import NAMESPACE
from lib.paths import PROFILE

# Waiting for a restarted pod; Postgres and Keycloak take up to a few minutes.
POD_TIMEOUT = 300


def require_minikube_context() -> None:
    """Refuses to continue unless kubectl's current context is minikube.

    The context has drifted to a real AKS cluster mid-session before (see
    plan.md), so every script that changes the cluster checks it itself.
    """
    result = process.run(["kubectl", "config", "current-context"], check=False)
    current = result.stdout.strip() if result.returncode == 0 else ""
    if current != PROFILE:
        msg = (
            f"kubectl context is '{current or '<none>'}', not '{PROFILE}': "
            f"run `kubectl config use-context {PROFILE}` and try again"
        )
        raise process.UserError(msg)


def kubectl(*args: str, stdin: str | None = None) -> str:
    """Stdout of kubectl args; raises ProcessError on failure."""
    return process.output(["kubectl", *args], stdin=stdin)


def kubectl_shown(*args: str, stdin: str | None = None) -> None:
    """Runs kubectl args with its output on the terminal; raises ProcessError on failure."""
    process.run(["kubectl", *args], stdin=stdin, capture=False)


def kubectl_ok(*args: str) -> bool:
    """Whether kubectl args succeeds; output is discarded."""
    return process.succeeds(["kubectl", *args])


def node(command: str) -> str:
    """Stdout of a shell command run on the minikube node; minikube ssh forwards no stdin."""
    return process.output(["minikube", "ssh", "-p", PROFILE, "--", command])


def get_json(*args: str) -> Any:
    """Parsed `kubectl get <args> -o json`."""
    return json.loads(kubectl("get", *args, "-o", "json"))


def exists(resource: str, namespace: str = NAMESPACE) -> bool:
    """Whether resource ("deployment/x", "job/y") exists in namespace."""
    return kubectl_ok("get", resource, "-n", namespace)


def edge_ip() -> str:
    """The edge's LoadBalancer IP, or "" when minikube tunnel has not assigned one."""
    result = process.run(
        [
            "kubectl",
            "get",
            "svc",
            EDGE_SERVICE,
            "-n",
            EDGE_NAMESPACE,
            "-o",
            "jsonpath={.status.loadBalancer.ingress[0].ip}",
        ],
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def serving_pod(pods: list[dict[str, Any]]) -> str:
    """Name of the first pod that is Ready and not terminating; "" when none is.

    Right after `rollout status` succeeds, the old pod is still listed while it
    terminates; exec into it fails once it is gone (the Keycloak sync timed out
    on it).
    """
    for pod in pods:
        conditions: list[dict[str, Any]] = pod.get("status", {}).get("conditions") or []
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
        if ready and not pod["metadata"].get("deletionTimestamp"):
            return str(pod["metadata"]["name"])
    return ""


def first_pod(selector: str) -> str:
    """Name of a serving pod matching the label selector; UserError when none is Ready within POD_TIMEOUT.

    A deploy can restart the pod (a changed pod template) just before it is used.
    """
    name = polling.wait_until(
        lambda: serving_pod(get_json("pod", "-n", NAMESPACE, "-l", selector)["items"]), timeout=POD_TIMEOUT, interval=2
    )
    if not name:
        msg = f"no Ready pod with labels {selector} in namespace {NAMESPACE}: check `kubectl get pod -l {selector}`"
        raise process.UserError(msg)
    return name
