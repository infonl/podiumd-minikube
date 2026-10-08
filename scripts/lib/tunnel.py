"""`minikube tunnel` and the /etc/hosts line that points the *.local names at the edge."""

import sys

from pathlib import Path

from lib import hosts
from lib import kube
from lib import polling
from lib import process
from lib.paths import PROFILE
from lib.process import UserError

TUNNEL_LOG = Path("/tmp/minikube-tunnel.log")  # noqa: S108 - user-visible log, fixed name on purpose
TUNNEL_PATTERN = "minikube tunnel"
TIMEOUT_SECONDS = 30
# minikube's default service network, which holds the LoadBalancer IPs the tunnel hands out.
SERVICE_CIDR = "10.96.0.0/12"
STOP_HINT = "Stop it with: ./scripts/setup-tunnel stop"
ETC_HOSTS = Path("/etc/hosts")


def route_args(action: str, node_ip: str) -> list[str]:
    """`sudo ip route <action>` for minikube's service network via the node."""
    return ["sudo", "ip", "route", action, SERVICE_CIDR, "via", node_ip]


def setup_tunnel() -> None:
    """Adds the route in the foreground (sudo asks here), then runs minikube tunnel in the background.

    The tunnel itself needs sudo only to add that route; it finds the route
    present and runs without a terminal. Not under sudo: minikube would look
    for the profile in root's home. A recorded IP survives a dead tunnel
    (requests then hang), so only a running process proves the tunnel.
    """
    kube.require_minikube_context()
    pid = process.running(TUNNEL_PATTERN)
    if pid and kube.edge_ip():
        print(f"'minikube tunnel' runs (PID {pid}) and the edge has external IP {kube.edge_ip()} - already up.")
        print(STOP_HINT)
        return
    node_ip = process.output(["minikube", "ip", "-p", PROFILE]).strip()
    print(f"Routing {SERVICE_CIDR} via the minikube node {node_ip} (sudo may ask for your password)...")
    process.run(route_args("replace", node_ip), capture=False)
    if not pid:
        print(f"Starting 'minikube tunnel' in the background (log: {TUNNEL_LOG})...")
        process.spawn(["minikube", "tunnel", "-p", PROFILE], TUNNEL_LOG)
    print("Waiting for the edge's external IP...")
    ip = polling.wait_until(kube.edge_ip, timeout=TIMEOUT_SECONDS, interval=2)
    if not ip:
        log = TUNNEL_LOG.read_text(encoding="utf-8") if TUNNEL_LOG.is_file() else "(no log file yet)"
        print("\n".join(log.splitlines()[-20:]), file=sys.stderr)
        msg = f"no external IP for the edge after {TIMEOUT_SECONDS}s; tunnel log above ({TUNNEL_LOG})"
        raise UserError(msg)
    print(f"Tunnel is up in the background. Edge external IP: {ip}")
    print("\nRun ./scripts/update-hosts to add/refresh the /etc/hosts entry for it.")
    print(STOP_HINT)


def stop_tunnel() -> None:
    """Stops the background minikube tunnel and removes its route (sudo may ask)."""
    if process.running(TUNNEL_PATTERN):
        print("Stopping 'minikube tunnel'...")
        process.run(["pkill", "-f", TUNNEL_PATTERN], check=False)
    else:
        print("No 'minikube tunnel' runs.")
    process.run(["minikube", "tunnel", "--cleanup", "-p", PROFILE], check=False)
    node_ip = process.output(["minikube", "ip", "-p", PROFILE]).strip()
    print(f"Removing the route {SERVICE_CIDR} via {node_ip} (sudo may ask for your password)...")
    process.run(route_args("del", node_ip), capture=False, check=False)
    print("Tunnel stopped; the *.local hosts are unreachable from this machine until ./scripts/setup-tunnel.")


def replace_hosts_line(text: str, line: str) -> str:
    """text without earlier lines of this project (any line naming zac.local), plus line."""
    kept = [old for old in text.splitlines() if "zac.local" not in old]
    return "\n".join([*kept, line]) + "\n"


def update_hosts() -> None:
    """Writes the current edge IP line to /etc/hosts (backup in /etc/hosts.bak)."""
    kube.require_minikube_context()
    ip = kube.edge_ip()
    if not ip:
        msg = "the edge has no external IP yet: run ./scripts/setup-tunnel first"
        raise UserError(msg)
    print("Caching sudo credentials up front...")
    process.run(["sudo", "-v"], capture=False)
    current = ETC_HOSTS.read_text(encoding="utf-8")
    line = hosts.hosts_line(ip, hosts.chart_hosts())
    process.run(["sudo", "cp", str(ETC_HOSTS), f"{ETC_HOSTS}.bak"])
    process.run(["sudo", "tee", str(ETC_HOSTS)], stdin=replace_hosts_line(current, line))
    print(f"Done. /etc/hosts now has:\n{line}")
