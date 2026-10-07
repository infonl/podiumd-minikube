"""kubectl access to this project's minikube cluster."""

import json

from typing import Any

from lib import process
from lib.paths import NAMESPACE
from lib.paths import PROFILE
from lib.paths import TRAEFIK_NAMESPACE
from lib.paths import TRAEFIK_SERVICE


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


def get_json(*args: str) -> Any:
    """Parsed `kubectl get <args> -o json`."""
    return json.loads(kubectl("get", *args, "-o", "json"))


def exists(resource: str, namespace: str = NAMESPACE) -> bool:
    """Whether resource ("deployment/x", "job/y") exists in namespace."""
    return kubectl_ok("get", resource, "-n", namespace)


def traefik_ip() -> str:
    """Traefik's LoadBalancer IP, or "" when minikube tunnel has not assigned one."""
    result = process.run(
        [
            "kubectl",
            "get",
            "svc",
            TRAEFIK_SERVICE,
            "-n",
            TRAEFIK_NAMESPACE,
            "-o",
            "jsonpath={.status.loadBalancer.ingress[0].ip}",
        ],
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def first_pod(app_name: str) -> str:
    """Name of the first pod labelled app.kubernetes.io/name=app_name."""
    return kubectl(
        "get",
        "pod",
        "-n",
        NAMESPACE,
        "-l",
        f"app.kubernetes.io/name={app_name}",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )


def django_shell(pod: str, code: str) -> str:
    """Stdout of `manage.py shell -c code` in pod."""
    return kubectl("exec", "-n", NAMESPACE, pod, "--", "python", "/app/src/manage.py", "shell", "-c", code)
