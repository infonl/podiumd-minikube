"""Memory of the minikube node and its containers, against the laptop budget and a committed baseline.

Refresh the baseline on a settled cluster, after podiumd-tests' full tier:
pytest tests/test_memory.py --update-memory-baseline
"""

import json
import sys

from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from lib import process
from lib import status


@pytest.fixture(scope="module")
def measured(edge_ip, request):
    """Node and per-container memory now; written to the baseline with --update-memory-baseline."""
    try:
        items = status.pods()
        node = status.node_usage().mib
        containers = {key: used.mib for key, used in status.container_usage(items, cpu=False).items()}
    except (process.ProcessError, FileNotFoundError) as exc:
        pytest.skip(f"could not measure the minikube node: {exc}")
    snapshot = {"node_mib": node, "containers_mib": sum(containers.values()), "containers": containers}
    if request.config.getoption("--update-memory-baseline"):
        age = status.youngest_container_seconds(items)
        if age < status.SETTLE_SECONDS:
            pytest.fail(f"a container started {age:.0f}s ago; refresh the baseline after {status.SETTLE_SECONDS}s")
        dated = {"measured": datetime.now(UTC).isoformat(timespec="seconds"), **snapshot}
        status.BASELINE.write_text(json.dumps(dated, indent=2) + "\n", encoding="utf-8")
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
        if key in baseline and status.grown(used, baseline[key])
    }
    assert not grown, f"above {status.BASELINE.name}; refresh it with --update-memory-baseline if intended: {grown}"
