"""Creates the minikube cluster this chart deploys to.

Starts minikube, installs NGINX Gateway Fabric (the edge) and cert-manager
(with the local CA issuer), fetches the dependencies and loads every image the chart can
reference: minikube's inner Docker has no internet access.
"""

import os
import sys
import tempfile

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lib import chart
from lib import dependency
from lib import disk
from lib import gateway
from lib import kube
from lib import manifests
from lib import memory
from lib import pki
from lib import process
from lib.paths import PROFILE

# Fully parallel loads once filled /tmp on the host.
BATCH_SIZE = 6
# Images are amd64-only; on Apple Silicon only the workload images are forced to it,
# never the node (minikube copies host-arch binaries into it).
PLATFORM = "linux/amd64"


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def start_minikube(cpus: int, memory_mb: int) -> None:
    """Starts the profile with the docker driver, or checks the running one's memory."""
    if process.succeeds(["minikube", "status", "-p", PROFILE]):
        print(f"minikube profile '{PROFILE}' is already running - leaving it as-is.")
        print("(delete it first with scripts/teardown-cluster if you want a genuinely fresh start)")
        memory.check(full=True)
        return
    print(f"Starting minikube (cpus={cpus}, memory={memory_mb}MB)...")
    # Without --driver=docker minikube silently falls back to qemu2.
    process.run(
        ["minikube", "start", "-p", PROFILE, "--driver=docker", f"--cpus={cpus}", f"--memory={memory_mb}"],
        capture=False,
    )


def start_node() -> None:
    """start_minikube sized from MINIKUBE_CPUS (default 6) and lib.memory, then requires its kubectl context."""
    start_minikube(_env_int("MINIKUBE_CPUS", 6), memory.wanted())
    kube.require_minikube_context()


def chart_images() -> list[str]:
    """Images of a render with every profile and monitoring-logging on, digests stripped."""
    text = manifests.helm_template(*chart.everything_args())
    return manifests.images(manifests.strip_image_digests(text))


def missing_images(images: list[str], loaded: list[str]) -> list[str]:
    """images that no `minikube image ls` line ends with."""
    return [image for image in images if not any(line.endswith(image) for line in loaded)]


def _pull(image: str) -> process.ProcessError | None:
    result = process.run(["docker", "pull", "--platform", PLATFORM, image], check=False, capture=False)
    return None if result.returncode == 0 else process.ProcessError(["docker", "pull", image], result.returncode, "")


def _load(image: str) -> process.ProcessError | None:
    """Saves image to a tarball and loads that: loading by reference looks up the node's arch and fails."""
    with tempfile.TemporaryDirectory() as directory:
        tar = str(Path(directory) / "image.tar")
        try:
            process.run(["docker", "save", "--platform", PLATFORM, image, "-o", tar])
            process.run(["minikube", "image", "load", "-p", PROFILE, tar])
        except process.ProcessError as error:
            return error
    return None


def _in_batches(
    images: list[str], label: str, action: Callable[[str], process.ProcessError | None], workers: int = BATCH_SIZE
) -> None:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for image, error in zip(images, pool.map(action, images), strict=True):
            if error:
                print(f"WARNING: {label} {image} failed: {error}", file=sys.stderr)


def load_images(images: list[str]) -> None:
    """Pulls images on the host and loads them into minikube, BATCH_SIZE at a time."""
    loaded = process.output(["minikube", "image", "ls", "-p", PROFILE]).splitlines()
    to_fetch = missing_images(images, loaded)
    if not to_fetch:
        print("All images already loaded - nothing to pull.")
        return
    print(f"{len(to_fetch)} image(s) need pulling + loading: {' '.join(to_fetch)}")
    for start in range(0, len(to_fetch), BATCH_SIZE):
        batch = to_fetch[start : start + BATCH_SIZE]
        print(f"Pulling batch: {' '.join(batch)}")
        _in_batches(batch, "pulling", _pull)
        print(f"Loading batch into minikube: {' '.join(batch)}")
        # One at a time: parallel loads into containerd lose images without an error.
        _in_batches(batch, "loading", _load, workers=1)
    still_missing = missing_images(images, process.output(["minikube", "image", "ls", "-p", PROFILE]).splitlines())
    if still_missing:
        print(f"WARNING: not in minikube after loading: {' '.join(still_missing)}", file=sys.stderr)


def provision() -> None:
    """Runs every provisioning step; each skips what is already done."""
    disk.check(disk.PROVISION)
    start_node()
    # monitoring-logging's alloy DaemonSet hardcodes this AKS nodeSelector; values cannot clear it.
    kube.kubectl("label", "node", PROFILE, "kubernetes.azure.com/agentpool=userpool", "--overwrite")
    gateway.install()
    pki.install_cert_manager()
    pki.install_issuer()
    print("Running helm dependency update...")
    dependency.sync()
    print("Deriving the image list from the currently-selected podiumd version...")
    images = chart_images()
    print(f"{len(images)} image(s) referenced by this chart's fully-enabled render.")
    print("Checking which are already loaded in minikube...")
    load_images(images)
    print("\nCluster provisioned. Next steps:")
    print("  1. ./scripts/deploy (or ./scripts/deploy --full for every optional profile).")
    print("  2. ./scripts/setup-tunnel for external reachability.")
