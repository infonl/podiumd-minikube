"""`minikube tunnel` and the /etc/hosts line that points the *.local names at the edge."""

import sys

from pathlib import Path

from lib import hosts
from lib import kube
from lib import polling
from lib import process
from lib.process import UserError

TUNNEL_LOG = Path("/tmp/minikube-tunnel.log")  # noqa: S108 - user-visible log, fixed name on purpose
TUNNEL_PATTERN = "minikube tunnel"
TIMEOUT_SECONDS = 30
ETC_HOSTS = Path("/etc/hosts")


def setup_tunnel() -> None:
    """Starts minikube tunnel unless one runs, then waits for the edge's external IP.

    A recorded IP survives a dead tunnel (requests then hang), so only a
    running process proves the tunnel. Not under sudo: minikube would look
    for the profile in root's home; it escalates for the route itself.
    """
    kube.require_minikube_context()
    pid = process.running(TUNNEL_PATTERN)
    if pid:
        ip = kube.edge_ip()
        if ip:
            print(f"'minikube tunnel' runs (PID {pid}) and the edge has external IP {ip} - already up.")
            print("\nRun ./scripts/update-hosts to add/refresh the /etc/hosts entry for it.")
            return
        print(f"'minikube tunnel' runs (PID {pid}) but the edge has no external IP yet; waiting {TIMEOUT_SECONDS}s...")
    else:
        stale = kube.edge_ip()
        if stale:
            print(f"the edge has external IP {stale} but no 'minikube tunnel' runs (stale) - starting a fresh one...")
        print("Caching sudo credentials up front: the detached tunnel cannot prompt for a password:")
        process.run(["sudo", "-v"], capture=False)
        print(f"Starting 'minikube tunnel' in the background (log: {TUNNEL_LOG})...")
        process.spawn(["minikube", "tunnel"], TUNNEL_LOG)
    print("Waiting for the edge's external IP...")
    ip = polling.wait_until(kube.edge_ip, timeout=TIMEOUT_SECONDS, interval=2)
    if not ip:
        log = TUNNEL_LOG.read_text(encoding="utf-8") if TUNNEL_LOG.is_file() else "(no log file yet)"
        print("\n".join(log.splitlines()[-20:]), file=sys.stderr)
        msg = f"no external IP for the edge after {TIMEOUT_SECONDS}s; tunnel log above ({TUNNEL_LOG})"
        raise UserError(msg)
    print(f"Tunnel is up. edge external IP: {ip}")
    print("\nRun ./scripts/update-hosts to add/refresh the /etc/hosts entry for it.")


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
