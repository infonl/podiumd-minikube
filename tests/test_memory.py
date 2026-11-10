"""Memory of the minikube node and its containers, against the laptop budget and a committed baseline.

Refresh the baseline on a settled cluster, after podiumd-tests' full tier:
pytest tests/test_memory.py --update-memory-baseline
"""

import json

from datetime import UTC
from datetime import datetime

import pytest

from podiumd_minikube import process
from podiumd_minikube import status


def _measure():
    """(pods, node MiB, MiB per container) of the minikube node now."""
    items = status.pods()
    usage = status.container_usage(items, cpu=False)
    return items, status.node_usage().mib, {key: used.mib for key, used in usage.items()}


@pytest.fixture(scope="module")
def measured(request):
    """Node and per-container memory now; written to the baseline with --update-memory-baseline."""
    request.getfixturevalue("edge_ip")
    try:
        items, node, containers = _measure()
    except (process.ProcessError, FileNotFoundError) as exc:
        pytest.skip(f"could not measure the minikube node: {exc}")
    snapshot = {"node_mib": node, "containers_mib": sum(containers.values()), "containers": containers}
    if request.config.getoption("--update-memory-baseline"):
        age = status.youngest_container_seconds(items)
        if age < status.SETTLE_SECONDS:
            pytest.fail(f"a container started {age:.0f}s ago; refresh the baseline after {status.SETTLE_SECONDS}s")
        dated = {"measured": datetime.now(UTC).isoformat(timespec="seconds"), **snapshot}
        status.BASELINE.write_text(f"{json.dumps(dated, indent=2)}\n", encoding="utf-8")
    return snapshot


def test_node_stays_within_the_laptop_budget(measured, enabled_profiles):
    """Any optional profile counts as --full's budget."""
    budget = status.budget_mib(enabled_profiles)
    assert measured["node_mib"] <= budget, (
        f"minikube node uses {measured['node_mib']} MiB, over the {budget} MiB budget; "
        "see .claude/memory/laptop-resources.md"
    )


def test_no_container_grew_beyond_the_baseline(measured):
    """Containers missing from the baseline (another profile) are not compared."""
    baseline = json.loads(status.BASELINE.read_text(encoding="utf-8"))["containers"]
    grown = {
        key: f"{baseline[key]} -> {used} MiB"
        for key, used in measured["containers"].items()
        if key in baseline and status.has_grown(used, baseline[key])
    }
    assert not grown, f"above {status.BASELINE.name}; refresh it with --update-memory-baseline if intended: {grown}"
