"""Creates the minikube cluster this chart deploys to.

Starts minikube, installs Traefik, fetches the dependencies and loads every
image the chart can reference: minikube's inner Docker has no internet access.
"""

import os
import sys
import tempfile

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lib import chart
from lib import dependency
from lib import kube
from lib import manifests
from lib import process
from lib.paths import PROFILE
from lib.paths import TRAEFIK_NAMESPACE

# Newer chart versions use template features older helm binaries cannot parse.
TRAEFIK_CHART_VERSION = "34.4.0"
# Fully parallel loads once filled /tmp on the host.
BATCH_SIZE = 6
# Images are amd64-only; on Apple Silicon only the workload images are forced to it,
# never the node (minikube copies host-arch binaries into it).
PLATFORM = "linux/amd64"


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def warn_if_undersized(memory_mb: int) -> None:
    """Warns when the running docker-driver node has less memory than memory_mb.

    MINIKUBE_MEMORY only applies to `minikube start`; an undersized node
    thrashed until the API server stopped answering (see plan.md).
    """
    result = process.run(["docker", "inspect", PROFILE, "--format", "{{.HostConfig.Memory}}"], check=False)
    current = int(result.stdout.strip() or 0) if result.returncode == 0 else 0
    requested = memory_mb * 1024 * 1024
    if 0 < current < requested:
        gib = memory_mb // 1024
        print(
            f"\nWARNING: profile '{PROFILE}' has {current // 1024**3}GiB, below the {gib}GiB requested. "
            f"Raise it live: docker update --memory={gib}g --memory-swap=-1 {PROFILE} "
            "(lost on `minikube delete`).",
            file=sys.stderr,
        )


def start_minikube(cpus: int, memory_mb: int) -> None:
    """Starts the profile with the docker driver, or checks the running one's memory."""
    if process.succeeds(["minikube", "status", "-p", PROFILE]):
        print(f"minikube profile '{PROFILE}' is already running - leaving it as-is.")
        print("(delete it first with scripts/teardown-cluster if you want a genuinely fresh start)")
        warn_if_undersized(memory_mb)
        return
    print(f"Starting minikube (cpus={cpus}, memory={memory_mb}MB)...")
    # Without --driver=docker minikube silently falls back to qemu2.
    process.run(
        ["minikube", "start", "-p", PROFILE, "--driver=docker", f"--cpus={cpus}", f"--memory={memory_mb}"],
        capture=False,
    )


def install_traefik() -> None:
    """Installs the pinned Traefik chart unless it is there."""
    if kube.exists("deployment/traefik", TRAEFIK_NAMESPACE):
        print(f"Traefik already installed in namespace '{TRAEFIK_NAMESPACE}' - skipping.")
        return
    print(f"Installing Traefik {TRAEFIK_CHART_VERSION}...")
    process.run(["helm", "repo", "add", "traefik", "https://traefik.github.io/charts"], check=False)
    process.output(["helm", "repo", "update", "traefik"])
    process.run(
        ["helm", "upgrade", "--install", "traefik", "traefik/traefik", "--version", TRAEFIK_CHART_VERSION,
         "-n", TRAEFIK_NAMESPACE, "--create-namespace"],
        capture=False,
    )  # fmt: skip


def chart_images() -> list[str]:
    """Images of a render with every profile and monitoring-logging on, digests stripped."""
    shape = chart.objecten_shape()
    pkce = chart.zac_pkce()
    text = manifests.helm_template(
        *chart.FULL_PROFILE_SETS, *shape.sets, "--set", "monitoringLogging.enabled=true", *pkce.sets
    )
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


def _in_batches(images: list[str], label: str, action: Callable[[str], process.ProcessError | None]) -> None:
    with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
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
        _in_batches(batch, "loading", _load)


def provision() -> None:
    """Runs every provisioning step; each skips what is already done."""
    cpus = _env_int("MINIKUBE_CPUS", 6)
    memory_mb = _env_int("MINIKUBE_MEMORY", 16384)
    start_minikube(cpus, memory_mb)
    kube.require_minikube_context()
    # monitoring-logging's alloy DaemonSet hardcodes this AKS nodeSelector; values cannot clear it.
    kube.kubectl("label", "node", PROFILE, "kubernetes.azure.com/agentpool=userpool", "--overwrite")
    install_traefik()
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
