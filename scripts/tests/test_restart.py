"""lib.restart: telling a failed ZAC boot from one still running."""

import json

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
