"""Free space on Docker's data directory: the minikube node's disk with the docker driver.

A full disk breaks pods silently (ENOSPC, Postgres at risk). The thresholds
come from this stack's footprint: about 30 GiB of images in the node, and
provision-cluster pulls every image on the host and saves it as a tarball
before loading it, so it briefly needs several times an image's size.
"""

import shutil
import sys

from dataclasses import dataclass

from lib import process
from lib.process import UserError

GIB = 1024**3
WARN_USED_FRACTION = 0.85


@dataclass(frozen=True)
class Need:
    """Free GiB below which to stop, and below which to warn."""

    fail_gib: int
    warn_gib: int


PROVISION = Need(fail_gib=15, warn_gib=25)
DEPLOY = Need(fail_gib=3, warn_gib=10)


def docker_root() -> str:
    """Docker's data directory on this host."""
    return process.output(["docker", "info", "--format", "{{.DockerRootDir}}"]).strip()


def verdict(total: int, free: int, need: Need) -> tuple[str, str]:
    """("fail" | "warn" | "ok", message) for a disk with total and free bytes."""
    used = 1 - free / total
    summary = f"{free / GIB:.1f} GiB free of {total / GIB:.1f} GiB ({used:.0%} used)"
    if free < need.fail_gib * GIB:
        return "fail", f"{summary}; need at least {need.fail_gib} GiB: extend the disk first"
    if free < need.warn_gib * GIB or used > WARN_USED_FRACTION:
        return "warn", f"{summary}; getting tight, extend the disk soon"
    return "ok", summary


def check(need: Need) -> None:
    """Stops when the disk cannot fit need; warns when it is getting tight."""
    root = docker_root()
    usage = shutil.disk_usage(root)
    level, message = verdict(usage.total, usage.free, need)
    if level == "fail":
        msg = f"disk {root}: {message}"
        raise UserError(msg)
    if level == "warn":
        print(f"WARNING: disk {root}: {message}", file=sys.stderr)
    else:
        print(f"Disk {root}: {message}.")
