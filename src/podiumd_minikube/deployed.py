"""What the last deploy applied (its command, the chart selections, which chart renders each object) and what runs.

deploy keeps it in a ConfigMap without this chart's labels, so prune leaves it alone.
"""

import json

from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path

from podiumd_minikube import dependency
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube.objects import Doc
from podiumd_minikube.paths import MONITORING_NAMESPACE
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME

CONFIGMAP = "podiumd-minikube-deployed"
NAMESPACES = (NAMESPACE, MONITORING_NAMESPACE)
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


def record(command: str, render: manifests.Render, digests: dict[str, str]) -> None:
    """Write the deploy's command, the selections, render.charts and each release's digest to the cluster."""
    data = {
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        **{chart: selected(key) for key, chart in dependency.DEPENDENCIES.items()},
        "charts": json.dumps(render.charts, sort_keys=True),
        "digests": json.dumps(digests, sort_keys=True),
    }
    doc = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": CONFIGMAP}, "data": data}
    kube.apply(manifests.dump([doc]), "-n", NAMESPACE)


def read() -> dict[str, str]:
    """Return the last deploy's record; {} before a deploy wrote one."""
    if not kube.is_present(f"configmap/{CONFIGMAP}"):
        return {}
    return kube.get_json(f"configmap/{CONFIGMAP}", "-n", NAMESPACE).get("data") or {}


def last_digests() -> dict[str, str]:
    """Each release's digest (releases.digest) at the last deploy; {} before one recorded them."""
    return json.loads(read().get("digests") or "{}")


def _running(pod: Doc) -> int:
    statuses: list[Doc] = pod.get("status", {}).get("containerStatuses") or []
    return sum(1 for container in statuses if "running" in container.get("state", {}))


def running_containers(items: list[Doc], charts: dict[str, str]) -> Counter[str]:
    """Return the running containers per chart in NAMESPACES: a pod's nearest rendered owner's chart, else OTHER."""
    owners = _Owners(charts)
    counts: Counter[str] = Counter()
    for pod in items:
        running = _running(pod)
        if pod["metadata"]["namespace"] in NAMESPACES and running:
            counts[owners.chart_of(pod["metadata"])] += running
    return counts


@dataclass
class _Owners:
    """The live owners of pods in NAMESPACES, fetched once per kind; charts: "Kind/name" -> chart."""

    charts: dict[str, str]
    live: dict[str, Doc] = field(default_factory=dict[str, Doc])

    def owner(self, reference: dict[str, str]) -> Doc | None:
        """Return the metadata of the object reference names."""
        kinds = kube.reference(reference["apiVersion"], reference["kind"])
        if kinds not in self.live:
            found = [item for ns in NAMESPACES for item in kube.get_json(kinds, "-n", ns)["items"]]
            self.live[kinds] = {item["metadata"]["name"]: item["metadata"] for item in found}
        return self.live[kinds].get(reference["name"])

    def chart_of(self, metadata: Doc | None) -> str:
        """Return the chart of metadata's nearest rendered owner, else OTHER."""
        while metadata and metadata.get("ownerReferences"):
            reference = metadata["ownerReferences"][0]
            chart = self.charts.get(f"{reference['kind']}/{reference['name']}")
            if chart:
                return chart
            metadata = self.owner(reference)
        return OTHER


def lines(items: list[Doc]) -> list[str]:
    """show-cluster-status' lines on the last deploy and the running containers per chart."""
    deployed = read()
    if not deployed:
        total = sum(_running(pod) for pod in items if pod["metadata"]["namespace"] in NAMESPACES)
        return [
            f"Deployed    no configmap/{CONFIGMAP}: the next deploy writes it",
            f"Running     containers: {total}",
        ]
    when = datetime.fromisoformat(deployed["time"]).astimezone()
    result = [f"Deployed    {when:%Y-%m-%d %H:%M %Z}: {deployed['command']}"]
    for key, chart in dependency.DEPENDENCIES.items():
        now = selected(key)
        differs = "" if now == deployed.get(chart) else f"; selected now: {now}"
        result.append(f"            {chart} {deployed.get(chart, '?')}{differs}")
    counts = running_containers(items, json.loads(deployed["charts"]))
    shown = [
        *dependency.DEPENDENCIES.values(),
        RELEASE_NAME,
        *sorted(set(counts) - {*dependency.DEPENDENCIES.values(), RELEASE_NAME}),
    ]
    result.append(f"Running     containers: {', '.join(f'{chart} {counts[chart]}' for chart in shown)}")
    return result
