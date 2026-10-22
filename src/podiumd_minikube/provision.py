"""Creates the minikube cluster this chart deploys to.

Starts minikube with Cilium, installs NGINX Gateway Fabric (the edge) and cert-manager
(with the local CA issuer), fetches the dependencies and loads every image the chart can
reference: minikube's inner Docker has no internet access.
"""

import functools
import os
import sys
import tempfile

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml

from podiumd_minikube import chart
from podiumd_minikube import cni
from podiumd_minikube import dependency
from podiumd_minikube import disk
from podiumd_minikube import gateway
from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import manifests
from podiumd_minikube import memory
from podiumd_minikube import pki
from podiumd_minikube import polling
from podiumd_minikube import process
from podiumd_minikube.paths import PROFILE

# Fully parallel loads once filled /tmp on the host.
BATCH_SIZE = 6
# Images are amd64-only; on Apple Silicon only the workload images are forced to it,
# never the node (minikube copies host-arch binaries into it).
PLATFORM = "linux/amd64"
# Laptop budget per static pod. GOGC=50 collects at 1.5x the live heap instead of 2x,
# kube-apiserver's 25 at 1.25x. kube-apiserver has no container limit for
# limit_runtimes' GOMEMLIMIT: its live heap grows from about 450 to 760 MiB over a
# day of deploys, and without a limit the Go runtime keeps the freed heap (RSS up to
# 1.5 GiB). The limit must stay above 1.5x that heap: at 700MiB it collected six
# times a second (3 cores).
CONTROL_PLANE_ENV = {
    "kube-apiserver": {"GOGC": "25", "GOMEMLIMIT": "1200MiB", **memory.GO_NO_HUGE_PAGES},
    "etcd": {"GOGC": "50", **memory.GO_NO_HUGE_PAGES},
    "kube-controller-manager": {"GOGC": "50", **memory.GO_NO_HUGE_PAGES},
    "kube-scheduler": memory.GO_NO_HUGE_PAGES,
}
# The node's Go daemons are systemd services, not static pods; a drop-in sets their env.
NODE_SERVICES = ("containerd", "kubelet")
NODE_SERVICE_ENV = {"GOGC": "50", **memory.GO_NO_HUGE_PAGES}
NODE_SERVICE_DROP_IN = "/etc/systemd/system/{}.service.d/20-podiumd-minikube.conf"
# The node keeps its journal in /run, a tmpfs counted as node memory (90 MiB seen).
JOURNALD_DROP_IN = "/etc/systemd/journald.conf.d/20-podiumd-minikube.conf"
JOURNALD_CONFIG = "[Journal]\nRuntimeMaxUse=16M\n"
STATIC_PODS = "/etc/kubernetes/manifests"
CONTROL_PLANE_TIMEOUT = 300
KUBELET_CONFIG = "/var/lib/kubelet/config.yaml"
# The node's resolv.conf without its search domains: pods would inherit the laptop's
# (DHCP, VPN), and musl stops at a "no data" answer for <name>.<that domain>.
POD_RESOLV_CONF = "/etc/kubernetes/pod-resolv.conf"


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def start_minikube(cpus: int, memory_mb: int) -> None:
    """Starts the profile with the docker driver and Cilium, or checks the running one's memory."""
    cni_manifest = cni.write_manifest()
    if process.succeeds(["minikube", "status", "-p", PROFILE]):
        print(f"minikube profile '{PROFILE}' is already running - leaving it as-is.")
        print("(delete it first with podiumd-minikube teardown-cluster if you want a genuinely fresh start)")
        memory.check(full=True)
        return
    print(f"Starting minikube (cpus={cpus}, memory={memory_mb}MB)...")
    # Without --driver=docker minikube silently falls back to qemu2.
    process.run(
        ["minikube", "start", "-p", PROFILE, "--driver=docker", f"--cpus={cpus}", f"--memory={memory_mb}",
         f"--cni={cni_manifest}", *cni.START_ARGS],
        capture=False,
    )  # fmt: skip


def cpuset_args(cpus: int, host_cpus: int) -> list[str]:
    """`--cpuset-cpus` for the first cpus host CPUs, or nothing when the host has no more."""
    return [f"--cpuset-cpus=0-{cpus - 1}"] if cpus < host_cpus else []


def start_node() -> None:
    """start_minikube sized from MINIKUBE_CPUS (default 6) and memory.py, then requires its kubectl context."""
    cpus = _env_int("MINIKUBE_CPUS", 6)
    start_minikube(cpus, memory.wanted())
    # The docker driver applies --memory but not --cpus on Linux (seen: NanoCpus 0). A CPU
    # quota alone still shows every host CPU to nproc, nginx's worker_processes auto and
    # GOMAXPROCS; the cpuset makes them see `cpus` (seen: 24 nginx workers).
    process.run(["docker", "update", f"--cpus={cpus}", *cpuset_args(cpus, os.cpu_count() or cpus), PROFILE])
    kube.require_minikube_context()
    cni.apply()
    tune_control_plane()
    tune_node_services()
    isolate_pod_dns()


def with_control_plane_env(manifest: str) -> str | None:
    """The static pod manifest with its CONTROL_PLANE_ENV values on its container; None when it has them already."""
    pod = yaml.safe_load(manifest)
    wanted = CONTROL_PLANE_ENV[pod["metadata"]["name"]]
    container = pod["spec"]["containers"][0]
    before: list[dict[str, Any]] = list(container.get("env") or [])
    kept = [item for item in before if item.get("name") not in wanted]
    container["env"] = [*kept, *({"name": name, "value": value} for name, value in wanted.items())]
    unchanged = {item["name"]: item.get("value") for item in before if item.get("name") in wanted} == wanted
    return None if unchanged else yaml.safe_dump(pod, sort_keys=False)


def _tuned_and_ready(name: str) -> bool:
    try:
        pod = kube.get_json("pod", "-n", "kube-system", f"{name}-{PROFILE}")
    except process.ProcessError:
        return False
    env_items: list[dict[str, Any]] = pod["spec"]["containers"][0].get("env") or []
    env = {item["name"]: item.get("value") for item in env_items}
    statuses: list[dict[str, Any]] = pod["status"].get("containerStatuses") or []
    tuned = all(env.get(name) == value for name, value in CONTROL_PLANE_ENV[name].items())
    return tuned and bool(statuses) and all(c.get("ready") for c in statuses)


def _write_node_file(path: str, content: str, mode: str) -> None:
    """Writes content to path on the node, staged and renamed: the kubelet would read a half-written file."""
    name = Path(path).name
    staged = f"/etc/kubernetes/.{name}"
    with tempfile.TemporaryDirectory() as directory:
        local = Path(directory) / name
        local.write_text(content, encoding="utf-8")
        process.run(["minikube", "cp", "-p", PROFILE, str(local), staged])
    directory = str(Path(path).parent)
    kube.node(
        f"sudo chown root:root {staged} && sudo chmod {mode} {staged}"
        f" && sudo mkdir -p {directory} && sudo mv {staged} {path}"
    )


def nameservers_only(resolv_conf: str) -> str:
    """resolv_conf's nameserver lines."""
    lines = (line.strip() for line in resolv_conf.splitlines())
    return "".join(f"{line}\n" for line in lines if line.startswith("nameserver "))


def with_pod_resolv_conf(config: str) -> str | None:
    """The kubelet configuration with resolvConf POD_RESOLV_CONF; None when it has it already."""
    parsed = yaml.safe_load(config)
    if parsed.get("resolvConf") == POD_RESOLV_CONF:
        return None
    parsed["resolvConf"] = POD_RESOLV_CONF
    return yaml.safe_dump(parsed, sort_keys=False)


def isolate_pod_dns() -> None:
    """Points the kubelet at POD_RESOLV_CONF; minikube start rewrites its configuration, so every start does this.

    Pods started before keep their search domains until they restart.
    """
    wanted = nameservers_only(kube.node("cat /etc/resolv.conf"))
    if kube.node(f"cat {POD_RESOLV_CONF} 2>/dev/null || true").strip() != wanted.strip():
        _write_node_file(POD_RESOLV_CONF, wanted, mode="644")
    config = with_pod_resolv_conf(kube.node(f"sudo cat {KUBELET_CONFIG}"))
    if config is None:
        return
    _write_node_file(KUBELET_CONFIG, config, mode="644")
    print(f"Restarting the kubelet with resolvConf {POD_RESOLV_CONF} (no search domains of the laptop)...")
    kube.node("sudo systemctl restart kubelet")


def service_drop_in(env: dict[str, str]) -> str:
    """A systemd drop-in that adds env to its service."""
    return "[Service]\n" + "".join(f"Environment={name}={value}\n" for name, value in env.items())


def node_drop_ins() -> dict[str, tuple[str, str]]:
    """Service -> (drop-in path, content) that tune_node_services writes."""
    env = service_drop_in(NODE_SERVICE_ENV)
    return {
        **{service: (NODE_SERVICE_DROP_IN.format(service), env) for service in NODE_SERVICES},
        "systemd-journald": (JOURNALD_DROP_IN, JOURNALD_CONFIG),
    }


def tune_node_services() -> None:
    """Writes node_drop_ins(); restarts the services it changed (containers outlive containerd)."""
    changed: list[str] = []
    for service, (path, wanted) in node_drop_ins().items():
        if kube.node(f"cat {path} 2>/dev/null || true").strip() == wanted.strip():
            continue
        _write_node_file(path, wanted, mode="644")
        changed.append(service)
    if not changed:
        return
    print(f"Restarting {', '.join(changed)}...")
    kube.node(f"sudo systemctl daemon-reload && sudo systemctl restart {' '.join(changed)}")


def tune_control_plane() -> None:
    """Adds CONTROL_PLANE_ENV to the static pods; minikube start rewrites their files, so every start does this."""
    changed: list[str] = []
    for name in CONTROL_PLANE_ENV:
        path = f"{STATIC_PODS}/{name}.yaml"
        tuned = with_control_plane_env(kube.node(f"sudo cat {path}"))
        if tuned is None:
            continue
        _write_node_file(path, tuned, mode="600")
        changed.append(name)
    if not changed:
        return
    print(f"Restarting {', '.join(changed)} with {[CONTROL_PLANE_ENV[name] for name in changed]}...")
    for name in changed:
        if not polling.wait_until(functools.partial(_tuned_and_ready, name), timeout=CONTROL_PLANE_TIMEOUT, interval=5):
            msg = f"{name} not ready {CONTROL_PLANE_TIMEOUT}s after its env change: see `minikube logs -p {PROFILE}`"
            raise process.UserError(msg)


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


@lock.holding("provision-cluster")
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
    print("  1. podiumd-minikube deploy (or podiumd-minikube deploy --full for every optional profile).")
    print("  2. podiumd-minikube setup-tunnel for external reachability.")
