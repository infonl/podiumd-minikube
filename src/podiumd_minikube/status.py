"""The cluster's state at a glance: node and container usage, profiles, problems, access, lock and disk.

Also the measuring that tests/test_memory.py checks against tests/memory-baseline.json.
"""

import json
import shutil
import time

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime

from podiumd_minikube import deployed
from podiumd_minikube import disk
from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import memory
from podiumd_minikube import process
from podiumd_minikube import tunnel
from podiumd_minikube.manifests import section
from podiumd_minikube.objects import Doc
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import MONITORING_NAMESPACE
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import PROFILE

MIB = 1024**2
BASELINE = CHART_DIR / "tests" / "memory-baseline.json"
# Settled usage drifts by tens of MiB; a regression is more than this above the baseline.
GROWTH_FACTOR = 1.2
GROWTH_SLACK_MIB = 64
# A container this young has not settled: Celery forks its children, JVMs grow their heap.
SETTLE_SECONDS = 300
# Restarts older than this (a reboot, a deploy) are history, not a problem.
RECENT_RESTART_SECONDS = 3600
# What the kubelet records for a container that a node stop or host reboot ended.
NODE_STOPPED = ("Unknown", 255)
# Seconds between the two CPU samples (crictl reports cumulative CPU time).
CPU_SAMPLE_SECONDS = 1.0
_UNITS = {"B": 1, "KiB": 1024, "MiB": MIB, "GiB": 1024 * MIB}
# Optional profile -> the pod-name prefix that shows it runs.
PROFILE_PODS = {
    "objecten": "objecten",
    "objecttypen": "objecttypen",
    "opennotificaties": "opennotificaties",
    "openarchiefbeheer": "openarchiefbeheer",
    "openformulieren": "openformulieren",
    "openinwoner": "openinwoner",
    "ita": "ita-web",
    "kiss": "contact-web",
    "omc": "omc",
    "referentielijsten": "referentielijsten",
    "openbeheer": "openbeheer",
    "openvtb": "openvtb",
    "zaakbrug": "zaakbrug",
    "zgw-office-addin": "zgw-office-addin-backend",
    "frankgateway": "frankgateway-outway",
    "clamav": "clamav",
    "metrics": "monitoring-logging-grafana",
    "replicas": "redis-ha-1",
}


@dataclass(frozen=True)
class Usage:
    """A container's working set (MiB) and CPU (cores)."""

    mib: int
    cpu: float


def enabled_profiles(pod_names: list[str]) -> dict[str, bool]:
    """Which optional profiles run, from the names of the running pods."""
    return {
        profile: any(name == prefix or name.startswith(f"{prefix}-") for name in pod_names)
        for profile, prefix in PROFILE_PODS.items()
    }


def pods() -> list[Doc]:
    """Every pod in every namespace."""
    return kube.get_json("pods", "-A")["items"]


def _mib(size: str) -> int:
    """MiB of a docker stats size such as '22.11GiB'."""
    unit = size.lstrip("0123456789.")
    return round(float(size.removesuffix(unit)) * _UNITS[unit] / MIB)


def node_usage() -> Usage:
    """The minikube container's memory (inactive page cache excluded) and CPU, as docker stats reports them."""
    line = process.output(["docker", "stats", PROFILE, "--no-stream", "--format", "{{.MemUsage}}|{{.CPUPerc}}"])
    used, cpu = line.strip().split("|")
    cores = float(cpu.rstrip("%")) / 100
    return Usage(mib=_mib(used.split(" / ")[0]), cpu=cores)


def reclaimable_kernel_mib() -> int:
    """MiB of the node's kernel caches the kernel frees under memory pressure (counted in node_usage)."""
    stat = kube.node("cat /sys/fs/cgroup/memory.stat")
    lines = [line for line in stat.splitlines() if line.startswith("slab_reclaimable ")]
    found = lines[0].split()[1] if lines else "0"
    return round(int(found) / MIB)


def workloads(items: list[Doc]) -> dict[str, str]:
    """Workload name per 'namespace/pod': a ReplicaSet's Deployment, else the pod's owner, else the pod."""
    names: dict[str, str] = {}
    for pod in items:
        meta = pod["metadata"]
        owner = next(iter(meta.get("ownerReferences", [])), {"kind": "", "name": meta["name"]})
        name = str(owner["name"])
        if owner["kind"] == "ReplicaSet":
            name = name.rsplit("-", 1)[0]
        names[f"{meta['namespace']}/{meta['name']}"] = name
    return names


def _crictl(*args: str) -> Doc:
    return json.loads(kube.node(" ".join(["sudo", "crictl", *args, "-o", "json"])))


def _key(label: dict[str, str], names: dict[str, str]) -> str:
    namespace, pod = label["io.kubernetes.pod.namespace"], label["io.kubernetes.pod.name"]
    return f"{namespace}/{names.get(f'{namespace}/{pod}', pod)}/{label['io.kubernetes.container.name']}"


def _cpu_nanoseconds(stat: Doc | None) -> int | None:
    """A crictl stat's CPU counter; None for a container without one yet (starting or stopping)."""
    value = section(section(stat or {}, "cpu"), "usageCoreNanoSeconds").get("value")
    return None if value is None else int(value)


def sampled_cores(first: Doc, later: Doc | None) -> float:
    """Cores a container used between two crictl stats CPU_SAMPLE_SECONDS apart; 0 without both counters."""
    start, end = _cpu_nanoseconds(first), _cpu_nanoseconds(later)
    if start is None or end is None:
        return 0
    return (end - start) / 1e9 / CPU_SAMPLE_SECONDS


def container_usage(items: list[Doc], *, cpu: bool = True) -> dict[str, Usage]:
    """Usage per running container, keyed 'namespace/workload/container'; replicas add up."""
    labels = {container["id"]: container["labels"] for container in _crictl("ps")["containers"]}
    names = workloads(items)
    first = _crictl("stats")["stats"]
    if cpu:
        time.sleep(CPU_SAMPLE_SECONDS)  # a measuring interval, not a wait for a condition
    second = {stat["attributes"]["id"]: stat for stat in _crictl("stats")["stats"]} if cpu else {}
    usage: dict[str, Usage] = {}
    for stat in first:
        label = labels.get(stat["attributes"]["id"])
        if label is None:
            continue
        key = _key(label, names)
        before = usage.get(key, Usage(0, 0))
        mib = round(int(stat["memory"]["workingSetBytes"]["value"]) / MIB)
        cores = sampled_cores(stat, second.get(stat["attributes"]["id"]))
        usage[key] = Usage(before.mib + mib, before.cpu + cores)
    return dict(sorted(usage.items()))


def youngest_container_seconds(items: list[Doc]) -> float:
    """Seconds since the most recent container start among the running pods."""
    starts = [
        datetime.fromisoformat(status["state"]["running"]["startedAt"])
        for pod in items
        for status in pod["status"].get("containerStatuses", [])
        if "running" in status.get("state", {})
    ]
    return (datetime.now(UTC) - max(starts)).total_seconds()


def budget_mib(profiles: dict[str, bool]) -> int:
    """The node's memory budget: --full's when any optional profile runs."""
    return memory.FULL_BUDGET_MB if any(profiles.values()) else memory.DEFAULT_BUDGET_MB


def _recently_restarted(container: Doc, now: datetime) -> bool:
    terminated = container.get("lastState", {}).get("terminated", {})
    finished = terminated.get("finishedAt")
    if not finished or (terminated.get("reason"), terminated.get("exitCode")) == NODE_STOPPED:
        return False
    return (now - datetime.fromisoformat(finished)).total_seconds() < RECENT_RESTART_SECONDS


def problems(
    items: list[Doc],
    jobs: list[Doc],
) -> list[str]:
    """Pods not Ready or restarted in the last hour (not by a node stop), and failed Jobs, one line each."""
    found: list[str] = []
    now = datetime.now(UTC)
    for pod in items:
        meta, status = pod["metadata"], pod["status"]
        if any(ref.get("kind") == "Job" for ref in meta.get("ownerReferences", [])):
            continue
        containers: list[Doc] = status.get("containerStatuses", [])
        ready = sum(1 for container in containers if container.get("ready"))
        recent = [container["name"] for container in containers if _recently_restarted(container, now)]
        if status.get("phase") != "Running" or ready < len(containers) or recent:
            restarted = f" restarted: {', '.join(recent)}" if recent else ""
            counts = f"{ready}/{len(containers)}{restarted}"
            found.append(f"{meta['namespace']}/{meta['name']} {status.get('phase')} {counts}")
    failed = [job for job in jobs if "Failed" in kube.true_conditions(job)]
    found.extend(f"job {job['metadata']['name']} Failed" for job in failed)
    return found


def grown(used_mib: int, baseline_mib: int) -> bool:
    """Whether a container uses more than the baseline allows."""
    return used_mib > baseline_mib * GROWTH_FACTOR + GROWTH_SLACK_MIB


def _baseline() -> dict[str, int]:
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))["containers"]
    except FileNotFoundError:
        return {}


def _gib(mib: float) -> str:
    return f"{mib / 1024:.1f}"


def _node_lines(profiles: dict[str, bool], node: Usage, usage: dict[str, Usage]) -> list[str]:
    cap = memory.node_mb()
    containers = sum(container.mib for container in usage.values())
    nano_cpus = process.output(["docker", "inspect", PROFILE, "--format", "{{.HostConfig.NanoCpus}}"]).strip()
    cores = "all host" if nano_cpus in {"", "0"} else f"{int(nano_cpus) / 1e9:g}"
    root = disk.docker_root()
    space = shutil.disk_usage(root)
    return [
        (
            f"Node        memory {_gib(node.mib)} / {_gib(cap) if cap else '?'} GiB cap, "
            f"budget {_gib(budget_mib(profiles))} GiB, containers {_gib(containers)} GiB"
        ),
        f"            of which {_gib(reclaimable_kernel_mib())} GiB kernel caches, freed under memory pressure",
        f"            CPU {node.cpu:.1f} / {cores} cores",
        f"Disk        {root}: {disk.verdict(space.total, space.free, disk.DEPLOY)[1]}",
    ]


def _access_line() -> str:
    edge = kube.edge_ip()
    running = process.running(tunnel.TUNNEL_PATTERN)
    access = f"tunnel PID {running}, edge {edge or 'no IP'}" if running else "tunnel not running"
    hint = f"; {tunnel.hosts_hint(edge)}" if edge else ""
    return f"Access      {access}{hint}"


def _container_lines(usage: dict[str, Usage], top: int) -> list[str]:
    baseline = _baseline()
    lines = ["Containers  MiB  vs baseline  CPU   namespace/workload/container  (! above the tolerance)"]
    ranked = sorted(usage.items(), key=lambda pair: -pair[1].mib)
    for key, used in ranked[: top or None]:
        delta = f"{used.mib - baseline[key]:+6d}" if key in baseline else "   new"
        mark = "!" if key in baseline and grown(used.mib, baseline[key]) else " "
        lines.append(f"         {used.mib:6d}  {delta}{mark}     {used.cpu:5.2f}  {key}")
    if top and len(ranked) > top:
        lines.append(f"            ... {len(ranked) - top} more (--all)")
    return lines


# A problem per line, aligned under the first.
PROBLEM_SEPARATOR = "\n            "


def report(top: int) -> None:
    """Prints the status; top limits the container list (0: all)."""
    items = pods()
    ours = [pod["metadata"] for pod in items if pod["metadata"]["namespace"] in {NAMESPACE, MONITORING_NAMESPACE}]
    profiles = enabled_profiles([meta["name"] for meta in ours])
    usage = container_usage(items)
    age = youngest_container_seconds(items)
    settled = "settled" if age >= SETTLE_SECONDS else "not settled yet: memory is still growing"
    hold = lock.current()
    found = problems(items, kube.get_json("jobs", "-A")["items"])
    running = " ".join(profile for profile, on in profiles.items() if on)
    lines = [
        f"podiumd-minikube cluster status {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        *_node_lines(profiles, node_usage(), usage),
        *deployed.lines(items),
        f"Profiles    {running or 'none (default)'}",
        f"Started     youngest container {age / 60:.0f} min ago ({settled})",
        _access_line(),
        f"Lock        {lock.describe(hold) if hold else 'free'}",
        f"Problems    {PROBLEM_SEPARATOR.join(found) or 'none'}",
        *_container_lines(usage, top),
    ]
    print("\n".join(lines))
