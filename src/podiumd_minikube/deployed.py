"""What the last deploy applied (its command, the chart selections, which chart renders each object) and what runs.

deploy keeps it in a ConfigMap without this chart's labels, so prune leaves it alone.
"""

import json

from collections import Counter
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from podiumd_minikube import dependency
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube.dependency import DEPENDENCIES
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME

CONFIGMAP = "podiumd-minikube-deployed"
# Pods no rendered object owns: podiumd-tests' mock server, Jobs started by hand.
OTHER = "other"


def checkout(directory: str) -> str:
    """'<repository directory> (<branch> <commit>)' of the git checkout at directory, else directory."""
    git = ["git", "-C", directory]
    top = process.run([*git, "rev-parse", "--show-toplevel"], check=False)
    if top.returncode != 0:
        return directory.replace(str(Path.home()), "~", 1)
    branch = process.output([*git, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    commit = process.output([*git, "rev-parse", "--short", "HEAD"]).strip()
    changed = ", uncommitted changes" if process.output([*git, "status", "--porcelain"]).strip() else ""
    return f"{Path(top.stdout.strip()).name} ({branch} {commit}{changed})"


def selected(key: str) -> str:
    """key's selection: a version, or a checkout with its repository, branch and commit."""
    selection = dependency.selection(key)
    if selection is None:
        return "none"
    if selection.mode == "version":
        return selection.value
    return f"checkout {checkout(selection.value)}"


def record(command: str, render: manifests.Render) -> None:
    """Writes the deploy's command, the selections and render.charts to the cluster."""
    data = {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        **{chart: selected(key) for key, chart in DEPENDENCIES.items()},
        "charts": json.dumps(render.charts, sort_keys=True),
    }
    doc = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": CONFIGMAP}, "data": data}
    kube.kubectl("apply", "--server-side", "--force-conflicts", "-n", NAMESPACE, "-f", "-", stdin=manifests.dump([doc]))


def read() -> dict[str, str]:
    """The last deploy's record; {} before a deploy wrote one."""
    if not kube.exists(f"configmap/{CONFIGMAP}"):
        return {}
    return kube.get_json(f"configmap/{CONFIGMAP}", "-n", NAMESPACE).get("data") or {}


def _running(pod: dict[str, Any]) -> int:
    statuses: list[dict[str, Any]] = pod.get("status", {}).get("containerStatuses") or []
    return sum(1 for container in statuses if "running" in container.get("state", {}))


def running_containers(items: list[dict[str, Any]], charts: dict[str, str]) -> Counter[str]:
    """Running containers per chart in NAMESPACE: the chart of a pod's nearest rendered owner, else OTHER."""
    live: dict[str, dict[str, Any]] = {}

    def owner(reference: dict[str, str]) -> dict[str, Any] | None:
        kinds = kube.reference(reference["apiVersion"], reference["kind"])
        if kinds not in live:
            found = kube.get_json(kinds, "-n", NAMESPACE)["items"]
            live[kinds] = {item["metadata"]["name"]: item["metadata"] for item in found}
        return live[kinds].get(reference["name"])

    def chart_of(metadata: dict[str, Any] | None) -> str:
        while metadata and (references := metadata.get("ownerReferences")):
            reference = references[0]
            if chart := charts.get(f"{reference['kind']}/{reference['name']}"):
                return chart
            metadata = owner(reference)
        return OTHER

    counts: Counter[str] = Counter()
    for pod in items:
        if pod["metadata"]["namespace"] == NAMESPACE and (running := _running(pod)):
            counts[chart_of(pod["metadata"])] += running
    return counts


def lines(items: list[dict[str, Any]]) -> list[str]:
    """show-cluster-status' lines on the last deploy and the running containers per chart."""
    deployed = read()
    if not deployed:
        total = sum(_running(pod) for pod in items if pod["metadata"]["namespace"] == NAMESPACE)
        return [
            f"Deployed    no configmap/{CONFIGMAP}: the next deploy writes it",
            f"Running     containers: {total}",
        ]
    when = datetime.fromisoformat(deployed["time"]).astimezone()
    result = [f"Deployed    {when:%Y-%m-%d %H:%M %Z}: {deployed['command']}"]
    for key, chart in DEPENDENCIES.items():
        now = selected(key)
        differs = f"; selected now: {now}" if now != deployed.get(chart) else ""
        result.append(f"            {chart} {deployed.get(chart, '?')}{differs}")
    counts = running_containers(items, json.loads(deployed["charts"]))
    shown = [*DEPENDENCIES.values(), RELEASE_NAME, *sorted(set(counts) - {*DEPENDENCIES.values(), RELEASE_NAME})]
    result.append(f"Running     containers: {', '.join(f'{chart} {counts[chart]}' for chart in shown)}")
    return result
