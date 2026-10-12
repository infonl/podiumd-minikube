"""lib.restart: telling a failed ZAC boot from one still running."""

import json

from datetime import datetime
from typing import Any

import pytest

from conftest import FakeRun

from lib import restart


def _zac(ready: int) -> str:
    status = {"observedGeneration": 1, "replicas": 1, "updatedReplicas": 1, "readyReplicas": ready}
    return json.dumps(
        {"items": [{"kind": "Deployment", "metadata": {"name": "zac", "generation": 1}, "status": status}]}
    )


@pytest.mark.parametrize(
    ("ready", "log", "state"),
    [(1, "", "ready"), (0, "WFLYCTL0080: Failed services", "failed"), (0, "WFLYSRV0049: starting", None)],
)
def testzac_state(fake_run: FakeRun, ready: int, log: str, state: str | None):
    fake_run.on("kubectl", "get", "deployment,statefulset", stdout=_zac(ready))
    fake_run.on("kubectl", "logs", stdout=log)
    assert restart.zac_state() == state


def _pod(name: str, ready_since: str, owner: str = "ReplicaSet") -> dict[str, Any]:
    return {
        "metadata": {"name": name, "ownerReferences": [{"kind": owner}]},
        "status": {"conditions": [{"type": "Ready", "status": "True", "lastTransitionTime": ready_since}]},
    }


def test_stale_pods_are_those_ready_since_before_the_node_started():
    boot = datetime.fromisoformat("2026-10-08T15:56:15.304156778Z")
    pods = [
        _pod("zac", "2026-10-08T14:05:00Z"),
        _pod("openzaak", "2026-10-08T15:57:00Z"),
        _pod("seed", "2026-10-08T14:05:00Z", owner="Job"),
    ]
    assert restart.stale_pods(pods, boot) == ["zac"]
