"""`minikube tunnel` and the /etc/hosts line that points the *.local names at the edge."""

import getpass
import shutil
import sys
import tempfile

from pathlib import Path

from podiumd_minikube import hosts
from podiumd_minikube import kube
from podiumd_minikube import lock
from podiumd_minikube import polling
from podiumd_minikube import process
from podiumd_minikube.paths import PROFILE
from podiumd_minikube.process import UserError

TUNNEL_LOG = Path("/tmp/minikube-tunnel.log")  # noqa: S108 - user-visible log, fixed name on purpose
TUNNEL_PATTERN = "minikube tunnel"
TIMEOUT_SECONDS = 30
# minikube's default service network, which holds the LoadBalancer IPs the tunnel hands out.
SERVICE_CIDR = "10.96.0.0/12"
STOP_HINT = "Stop it with: podiumd-minikube setup-tunnel stop"
ETC_HOSTS = Path("/etc/hosts")
SUDOERS_FILE = "/etc/sudoers.d/podiumd-minikube-tunnel"


def _ip() -> str:
    """The ip binary's real path: sudoers rules name commands by full path."""
    found = shutil.which("ip")
    return str(Path(found).resolve()) if found else "ip"


def route_args(action: str, node_ip: str) -> list[str]:
    """`sudo ip route <action>` for minikube's service network via the node."""
    return ["sudo", _ip(), "route", action, SERVICE_CIDR, "via", node_ip]


def sudoers_rule() -> str:
    """A sudoers.d rule that lets this user add and remove the tunnel's route without a password."""
    node_ip = process.output(["minikube", "ip", "-p", PROFILE]).strip()
    commands = ", ".join(" ".join(route_args(action, node_ip)[1:]) for action in ("replace", "del"))
    return f"# podiumd-minikube setup-tunnel: its route only.\n{getpass.getuser()} ALL=(root) NOPASSWD: {commands}\n"


def install_sudoers() -> None:
    """Checks sudoers_rule() with visudo, installs it as SUDOERS_FILE (sudo asks once) and verifies it."""
    rule = sudoers_rule()
    print(f"Installing {SUDOERS_FILE}:\n{rule}")
    node_ip = process.output(["minikube", "ip", "-p", PROFILE]).strip()
    with tempfile.NamedTemporaryFile("w", suffix=".sudoers", encoding="utf-8") as staged:
        staged.write(rule)
        staged.flush()
        process.run([shutil.which("visudo") or "/usr/sbin/visudo", "-cf", staged.name])
        process.run(
            ["sudo", "install", "-m", "440", "-o", "root", "-g", "root", staged.name, SUDOERS_FILE], capture=False
        )
    if not process.succeeds(["sudo", "-n", "-l", *route_args("replace", node_ip)[1:]]):
        msg = f"{SUDOERS_FILE} is installed, but sudo still asks for the route: check `sudo -l`"
        raise UserError(msg)
    print("Installed: setup-tunnel, deploy and start-cluster now add the route without a password.")


def routed_via() -> str | None:
    """The gateway the service network is routed via, or None without a route."""
    shown = process.output(["ip", "route", "show", SERVICE_CIDR]).split()
    return shown[2] if shown[1:2] == ["via"] else None


def sudo_answers(sudo_args: list[str]) -> bool:
    """Whether sudo can run sudo_args now: a terminal for the password, or a rule without one."""
    return sys.stdin.isatty() or process.succeeds(["sudo", "-n", "-l", *sudo_args])


def require_sudo(command: str, sudo_args: list[str]) -> None:
    """Refuses when sudo would ask for a password for sudo_args and no terminal can answer it."""
    if not sudo_answers(sudo_args):
        msg = (
            f"sudo needs a terminal for its password: run `podiumd-minikube {command}` in a terminal,"
            f" or install `podiumd-minikube setup-tunnel sudoers` as {SUDOERS_FILE} (README)"
        )
        raise UserError(msg)


def setup_tunnel(*, ask: bool = True) -> bool:
    """Adds the route in the foreground (sudo asks here), then runs minikube tunnel in the background.

    ask=False (deploy, start-cluster): only when sudo needs no password for the route; else
    prints the manual step and returns False.

    The tunnel itself needs sudo only to add that route; it finds the route
    present and runs without a terminal. Not under sudo: minikube would look
    for the profile in root's home. A recorded IP survives a dead tunnel
    (requests then hang), so only a running process proves the tunnel.
    """
    kube.require_minikube_context()
    pid = process.running(TUNNEL_PATTERN)
    if pid and kube.edge_ip():
        ip = kube.edge_ip()
        print(f"'minikube tunnel' runs (PID {pid}) and the edge has external IP {ip} - already up.")
        print(f"\n{hosts_hint(ip)}")
        print(STOP_HINT)
        return True
    node_ip = process.output(["minikube", "ip", "-p", PROFILE]).strip()
    if routed_via() == node_ip:
        print(f"{SERVICE_CIDR} is already routed via the minikube node {node_ip}.")
    else:
        route = route_args("replace", node_ip)
        if not ask and not process.succeeds(["sudo", "-n", "-l", *route[1:]]):
            print(f"\nNext: podiumd-minikube setup-tunnel (sudo asks for the route; README: {SUDOERS_FILE}).")
            return False
        require_sudo("setup-tunnel", route[1:])
        print(f"Routing {SERVICE_CIDR} via the minikube node {node_ip} (sudo may ask for your password)...")
        process.run(route, capture=False)
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
    print(f"\n{hosts_hint(ip)}")
    print(STOP_HINT)
    return True


@lock.holding("setup-tunnel stop")
def stop_tunnel() -> None:
    """Stops the background minikube tunnel and removes its route (sudo may ask)."""
    if process.running(TUNNEL_PATTERN):
        print("Stopping 'minikube tunnel'...")
        process.run(["pkill", "-f", TUNNEL_PATTERN], check=False)
    else:
        print("No 'minikube tunnel' runs.")
    # The route, not `minikube ip`: that fails on a stopped cluster.
    node_ip = routed_via()
    if node_ip:
        print(f"Removing the route {SERVICE_CIDR} via {node_ip} (sudo may ask for your password)...")
        process.run(route_args("del", node_ip), capture=False, check=False)
    print("Tunnel stopped; the *.local hosts are unreachable from this machine until podiumd-minikube setup-tunnel.")


def replace_hosts_line(text: str, line: str) -> str:
    """text without earlier lines of this project (any line naming zac.local), plus line."""
    kept = [old for old in text.splitlines() if "zac.local" not in old]
    return "\n".join([*kept, line]) + "\n"


def wanted_hosts_line(ip: str) -> str:
    """The /etc/hosts line update-hosts writes for ip."""
    return hosts.hosts_line(ip, hosts.chart_hosts())


def hosts_hint(ip: str) -> str:
    """What to do about /etc/hosts for ip: run update-hosts, or nothing."""
    current = ETC_HOSTS.read_text(encoding="utf-8")
    if replace_hosts_line(current, wanted_hosts_line(ip)) == current:
        return "/etc/hosts already points the *.local hosts at it."
    return "Run podiumd-minikube update-hosts to add/refresh the /etc/hosts entry for it."


def update_hosts() -> None:
    """Writes the current edge IP line to /etc/hosts (backup in /etc/hosts.bak)."""
    kube.require_minikube_context()
    ip = kube.edge_ip()
    if not ip:
        msg = "the edge has no external IP yet: run podiumd-minikube setup-tunnel first"
        raise UserError(msg)
    current = ETC_HOSTS.read_text(encoding="utf-8")
    line = wanted_hosts_line(ip)
    if replace_hosts_line(current, line) == current:
        print(f"/etc/hosts is up to date:\n{line}")
        return
    require_sudo("update-hosts", ["true"])
    print("Caching sudo credentials up front...")
    process.run(["sudo", "-v"], capture=False)
    process.run(["sudo", "cp", str(ETC_HOSTS), f"{ETC_HOSTS}.bak"])
    process.run(["sudo", "tee", str(ETC_HOSTS)], stdin=replace_hosts_line(current, line))
    print(f"Done. /etc/hosts now has:\n{line}")
