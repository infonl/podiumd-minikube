"""Memory of the minikube node and its containers, against the laptop budget and a committed baseline.

Refresh the baseline on a settled cluster: pytest tests/test_memory.py --update-memory-baseline
"""

import json
import subprocess

from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

MIB = 1024**2
GIB_IN_MIB = 1024
BASELINE = Path(__file__).with_name("memory-baseline.json")
# .claude/memory/laptop-resources.md: the default profile within ~16 GiB, --full within ~20 GiB.
DEFAULT_BUDGET_MIB = 16 * GIB_IN_MIB
FULL_BUDGET_MIB = 20 * GIB_IN_MIB
# Settled usage drifts by tens of MiB; a regression is more than this above the baseline.
GROWTH_FACTOR = 1.2
GROWTH_SLACK_MIB = 64
_UNITS = {"B": 1, "KiB": 1024, "MiB": MIB, "GiB": 1024 * MIB}


def _output(*args):
    return subprocess.run(list(args), capture_output=True, text=True, timeout=60, check=True).stdout


def _mib(size):
    """MiB of a docker stats size such as '22.11GiB'."""
    unit = size.lstrip("0123456789.")
    return round(float(size.removesuffix(unit)) * _UNITS[unit] / MIB)


def node_mib():
    """The minikube container's memory use in MiB, as docker stats reports it (inactive page cache excluded)."""
    return _mib(_output("docker", "stats", "minikube", "--no-stream", "--format", "{{.MemUsage}}").split(" / ")[0])


def workloads():
    """Workload name per 'namespace/pod': a ReplicaSet's Deployment, else the pod's owner, else the pod."""
    names = {}
    for pod in json.loads(_output("kubectl", "get", "pods", "-A", "-o", "json"))["items"]:
        meta = pod["metadata"]
        owner = next(iter(meta.get("ownerReferences", [])), {"kind": "", "name": meta["name"]})
        name = owner["name"].rsplit("-", 1)[0] if owner["kind"] == "ReplicaSet" else owner["name"]
        names[f"{meta['namespace']}/{meta['name']}"] = name
    return names


def container_mib():
    """Working set in MiB per running container, keyed 'namespace/workload/container'."""
    stats = json.loads(_output("minikube", "ssh", "-p", "minikube", "--", "sudo", "crictl", "stats", "-o", "json"))
    containers = json.loads(_output("minikube", "ssh", "-p", "minikube", "--", "sudo", "crictl", "ps", "-o", "json"))
    labels = {c["id"]: c["labels"] for c in containers["containers"]}
    names = workloads()
    usage = {}
    for stat in stats["stats"]:
        label = labels.get(stat["attributes"]["id"])
        if label is None:
            continue
        namespace, pod = label["io.kubernetes.pod.namespace"], label["io.kubernetes.pod.name"]
        key = f"{namespace}/{names.get(f'{namespace}/{pod}', pod)}/{label['io.kubernetes.container.name']}"
        # Replicas of one workload add up.
        usage[key] = usage.get(key, 0) + round(int(stat["memory"]["workingSetBytes"]["value"]) / MIB)
    return dict(sorted(usage.items()))


@pytest.fixture(scope="module")
def measured(edge_ip, request):
    """Node and per-container memory now; written to the baseline with --update-memory-baseline."""
    try:
        node, containers = node_mib(), container_mib()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"could not measure the minikube node: {exc}")
    snapshot = {"node_mib": node, "containers_mib": sum(containers.values()), "containers": containers}
    if request.config.getoption("--update-memory-baseline"):
        dated = {"measured": datetime.now(UTC).isoformat(timespec="seconds"), **snapshot}
        BASELINE.write_text(json.dumps(dated, indent=2) + "\n", encoding="utf-8")
    return snapshot


def test_node_stays_within_the_laptop_budget(measured, enabled_profiles):
    """Any optional profile counts as --full's budget."""
    budget = FULL_BUDGET_MIB if any(enabled_profiles.values()) else DEFAULT_BUDGET_MIB
    assert measured["node_mib"] <= budget, (
        f"minikube node uses {measured['node_mib']} MiB, over the {budget} MiB budget; "
        "see .claude/memory/laptop-resources.md"
    )


def test_no_container_grew_beyond_the_baseline(measured):
    """Containers missing from the baseline (another profile) are not compared."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["containers"]
    grown = {
        key: f"{baseline[key]} -> {used} MiB"
        for key, used in measured["containers"].items()
        if key in baseline and used > baseline[key] * GROWTH_FACTOR + GROWTH_SLACK_MIB
    }
    assert not grown, f"above {BASELINE.name}; refresh it with --update-memory-baseline if intended: {grown}"
