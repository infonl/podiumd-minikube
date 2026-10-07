"""Memory of the minikube node: the docker-driver container's cap, sized from the host.

The node reports the host's memory as allocatable whatever the cap, so the
scheduler overcommits a too small cap; the node then swaps until the API
server stops answering (see plan.md).
"""

import os
import sys

from lib import process
from lib.paths import PROFILE

MIB = 1024**2
# Share of the memory Docker sees (the host, or Docker Desktop's VM).
HOST_FRACTION = 0.5
# A deploy --full used 18.4 GiB once settled; startup (Elasticsearch
# pre-touching its heap, Java apps) needs more, and 16 GiB thrashed.
FULL_STACK_MB = 24 * 1024


def wanted_mb(host_total_mb: int, override: str | None) -> int:
    """The node's memory: MINIKUBE_MEMORY (MB) when set, else HOST_FRACTION of host_total_mb."""
    return int(override) if override else int(host_total_mb * HOST_FRACTION)


def shortfalls(cap_mb: int, target_mb: int, *, full: bool) -> list[str]:
    """Why a node capped at cap_mb is too small; [] when it fits."""
    result: list[str] = []
    if cap_mb < target_mb:
        result.append(f"below the {target_mb // 1024} GiB wanted")
    if full and cap_mb < FULL_STACK_MB:
        result.append(f"below the ~{FULL_STACK_MB // 1024} GiB a deploy --full needs")
    return result


def host_mb() -> int:
    """Memory Docker sees, in MB."""
    return int(process.output(["docker", "info", "--format", "{{.MemTotal}}"]).strip()) // MIB


def node_mb() -> int:
    """The running profile's memory cap in MB; 0 when it has none or is not running."""
    result = process.run(["docker", "inspect", PROFILE, "--format", "{{.HostConfig.Memory}}"], check=False)
    return int(result.stdout.strip() or 0) // MIB if result.returncode == 0 else 0


def wanted() -> int:
    """wanted_mb for this host."""
    return wanted_mb(host_mb(), os.environ.get("MINIKUBE_MEMORY"))


def check(*, full: bool) -> None:
    """Warns when the running node's cap is below what is wanted; never changes it."""
    cap = node_mb()
    if not cap:
        return
    target = wanted()
    reasons = shortfalls(cap, target, full=full)
    if reasons:
        raise_to = max(target, FULL_STACK_MB if full else 0) // 1024
        print(
            f"WARNING: minikube profile '{PROFILE}' has {cap // 1024} GiB, {' and '.join(reasons)}. "
            f"Raise it live: docker update --memory={raise_to}g --memory-swap=-1 {PROFILE} "
            "(lost on `minikube delete`).",
            file=sys.stderr,
        )
