"""The cluster's state at a glance: node and container usage, profiles, problems, access, lock and disk.

Also the measuring that tests/test_memory.py checks against tests/memory-baseline.json.
"""

import json
import shutil
import time

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Any

from lib import disk
from lib import kube
from lib import lock
from lib import memory
from lib import process
from lib import tunnel
from lib.paths import CHART_DIR
from lib.paths import NAMESPACE
from lib.paths import PROFILE

MIB = 1024**2
BASELINE = CHART_DIR / "tests" / "memory-baseline.json"
# .claude/memory/laptop-resources.md: the default profile within ~16 GiB, --full within ~20 GiB.
DEFAULT_BUDGET_MIB = 16 * 1024
FULL_BUDGET_MIB = 20 * 1024
# Settled usage drifts by tens of MiB; a regression is more than this above the baseline.
GROWTH_FACTOR = 1.2
GROWTH_SLACK_MIB = 64
# A container this young has not settled: Celery forks its children, JVMs grow their heap.
SETTLE_SECONDS = 300
# Restarts older than this (a reboot, a deploy) are history, not a problem.
RECENT_RESTART_SECONDS = 3600
# Seconds between the two CPU samples (crictl reports cumulative CPU time).
CPU_SAMPLE_SECONDS = 1.0
_UNITS = {"B": 1, "KiB": 1024, "MiB": MIB, "GiB": 1024 * MIB}
# Optional profile -> the pod-name prefix that shows it runs. monitoringLogging's
# subcharts carry the release name; templates/metrics' Grafana does not.
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
    "frankgateway": "frankgateway-outway",
    "clamav": "clamav",
    "metrics": "grafana",
    "monitoringLogging": "podiumd-minikube-grafana",
}


@dataclass(frozen=True)
class Usage:
    """A container's working set (MiB) and CPU (cores)."""

    mib: int
    cpu: float


def enabled_profiles(pod_names: list[str]) -> dict[str, bool]:
    """Which optional profiles run, from the names of the running pods."""
    return {
        profile: any(name == prefix or name.startswith(prefix + "-") for name in pod_names)
        for profile, prefix in PROFILE_PODS.items()
    }


def pods() -> list[dict[str, Any]]:
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
    return Usage(mib=_mib(used.split(" / ")[0]), cpu=float(cpu.rstrip("%")) / 100)


def workloads(items: list[dict[str, Any]]) -> dict[str, str]:
    """Workload name per 'namespace/pod': a ReplicaSet's Deployment, else the pod's owner, else the pod."""
    names: dict[str, str] = {}
    for pod in items:
        meta = pod["metadata"]
        owner = next(iter(meta.get("ownerReferences", [])), {"kind": "", "name": meta["name"]})
        name = owner["name"].rsplit("-", 1)[0] if owner["kind"] == "ReplicaSet" else owner["name"]
        names[f"{meta['namespace']}/{meta['name']}"] = name
    return names


def _crictl(*args: str) -> Any:
    return json.loads(process.output(["minikube", "ssh", "-p", PROFILE, "--", "sudo", "crictl", *args, "-o", "json"]))


def _key(label: dict[str, str], names: dict[str, str]) -> str:
    namespace, pod = label["io.kubernetes.pod.namespace"], label["io.kubernetes.pod.name"]
    return f"{namespace}/{names.get(f'{namespace}/{pod}', pod)}/{label['io.kubernetes.container.name']}"


def _cores(first: dict[str, Any], later: dict[str, Any] | None) -> float:
    if later is None:
        return 0.0
    spent = int(later["cpu"]["usageCoreNanoSeconds"]["value"]) - int(first["cpu"]["usageCoreNanoSeconds"]["value"])
    return spent / 1e9 / CPU_SAMPLE_SECONDS


def container_usage(items: list[dict[str, Any]], *, cpu: bool = True) -> dict[str, Usage]:
    """Usage per running container, keyed 'namespace/workload/container'; replicas add up."""
    labels = {c["id"]: c["labels"] for c in _crictl("ps")["containers"]}
    names = workloads(items)
    first = _crictl("stats")["stats"]
    if cpu:
        time.sleep(CPU_SAMPLE_SECONDS)  # a measuring interval, not a wait for a condition
    second = {s["attributes"]["id"]: s for s in _crictl("stats")["stats"]} if cpu else {}
    usage: dict[str, Usage] = {}
    for stat in first:
        label = labels.get(stat["attributes"]["id"])
        if label is None:
            continue
        key = _key(label, names)
        before = usage.get(key, Usage(0, 0.0))
        mib = round(int(stat["memory"]["workingSetBytes"]["value"]) / MIB)
        usage[key] = Usage(before.mib + mib, before.cpu + _cores(stat, second.get(stat["attributes"]["id"])))
    return dict(sorted(usage.items()))


def youngest_container_seconds(items: list[dict[str, Any]]) -> float:
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
    return FULL_BUDGET_MIB if any(profiles.values()) else DEFAULT_BUDGET_MIB


def _recently_restarted(container: dict[str, Any], now: datetime) -> bool:
    finished = container.get("lastState", {}).get("terminated", {}).get("finishedAt")
    return bool(finished) and (now - datetime.fromisoformat(finished)).total_seconds() < RECENT_RESTART_SECONDS


def problems(items: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[str]:
    """Pods not Ready or restarted in the last hour, and failed Jobs, one line each."""
    found: list[str] = []
    now = datetime.now(UTC)
    for pod in items:
        meta, status = pod["metadata"], pod["status"]
        if any(ref.get("kind") == "Job" for ref in meta.get("ownerReferences", [])):
            continue
        containers: list[dict[str, Any]] = status.get("containerStatuses", [])
        ready = sum(1 for c in containers if c.get("ready"))
        recent = [c["name"] for c in containers if _recently_restarted(c, now)]
        if status.get("phase") != "Running" or ready < len(containers) or recent:
            restarted = f" restarted: {', '.join(recent)}" if recent else ""
            found.append(
                f"{meta['namespace']}/{meta['name']} {status.get('phase')} {ready}/{len(containers)}{restarted}"
            )
    for job in jobs:
        conditions: list[dict[str, Any]] = job.get("status", {}).get("conditions") or []
        if any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions):
            found.append(f"job {job['metadata']['name']} Failed")
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
    nano_cpus = process.output(["docker", "inspect", PROFILE, "--format", "{{.HostConfig.NanoCpus}}"]).strip()
    cores = f"{int(nano_cpus) / 1e9:g}" if nano_cpus not in {"", "0"} else "all host"
    root = disk.docker_root()
    space = shutil.disk_usage(root)
    return [
        (
            f"Node        memory {_gib(node.mib)} / {_gib(cap) if cap else '?'} GiB cap, "
            f"budget {_gib(budget_mib(profiles))} GiB, containers {_gib(sum(u.mib for u in usage.values()))} GiB"
        ),
        f"            CPU {node.cpu:.1f} / {cores} cores",
        f"Disk        {root}: {disk.verdict(space.total, space.free, disk.DEPLOY)[1]}",
    ]


def _access_line() -> str:
    edge = kube.edge_ip()
    running = process.running(tunnel.TUNNEL_PATTERN)
    access = f"tunnel PID {running}, edge {edge or 'no IP'}" if running else "tunnel not running"
    return f"Access      {access}" + (f"; {tunnel.hosts_hint(edge)}" if edge else "")


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


def report(top: int) -> None:
    """Prints the status; top limits the container list (0: all)."""
    items = pods()
    profiles = enabled_profiles([p["metadata"]["name"] for p in items if p["metadata"]["namespace"] == NAMESPACE])
    usage = container_usage(items)
    age = youngest_container_seconds(items)
    settled = "settled" if age >= SETTLE_SECONDS else "not settled yet: memory is still growing"
    hold = lock.current()
    found = problems(items, kube.get_json("jobs", "-A")["items"])
    lines = [
        f"podiumd-minikube cluster status {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        *_node_lines(profiles, node_usage(), usage),
        f"Profiles    {' '.join(p for p, on in profiles.items() if on) or 'none (default)'}",
        f"Started     youngest container {age / 60:.0f} min ago ({settled})",
        _access_line(),
        f"Lock        {lock.describe(hold) if hold else 'free'}",
        "Problems    " + ("\n            ".join(found) if found else "none"),
        *_container_lines(usage, top),
    ]
    print("\n".join(lines))
