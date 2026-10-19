"""podiumd_minikube.tunnel: minikube tunnel, its route and the /etc/hosts line."""

import io
import sys

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import hosts
from podiumd_minikube import process
from podiumd_minikube import tunnel
from podiumd_minikube.process import UserError

NODE_IP = "192.168.49.2"
EDGE_IP = "10.96.0.200"
LINE = f"{EDGE_IP} zac.local  {hosts.MARKER}"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def machine(fake_run: FakeRun, poll_once: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRun:
    """minikube context, no tunnel, no route, an edge IP; /etc/hosts and the log in tmp_path."""
    monkeypatch.setattr(tunnel, "ETC_HOSTS", tmp_path / "hosts")
    monkeypatch.setattr(tunnel, "TUNNEL_LOG", tmp_path / "tunnel.log")
    tunnel.ETC_HOSTS.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    monkeypatch.setattr(hosts, "chart_hosts", lambda: ["zac.local"])
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    fake_run.on("kubectl", "config", "current-context", stdout="minikube\n")
    fake_run.on("pgrep", "-f", tunnel.TUNNEL_PATTERN, returncode=1)
    fake_run.on("minikube", "ip", stdout=f"{NODE_IP}\n")
    fake_run.on("ip", "route", "show", stdout="")
    fake_run.on("kubectl", "get", "svc", stdout=EDGE_IP)
    return fake_run


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], Path]]:
    calls: list[tuple[list[str], Path]] = []
    monkeypatch.setattr(process, "spawn", lambda args, log: calls.append((list(args), log)))
    return calls


def test_setup_tunnel_routes_and_starts_the_tunnel(
    machine: FakeRun, spawned: list[tuple[list[str], Path]], capsys: pytest.CaptureFixture[str]
):
    tunnel.setup_tunnel()
    assert machine.ran("sudo") == [tunnel.route_args("replace", NODE_IP)]
    assert spawned == [(["minikube", "tunnel", "-p", "minikube"], tunnel.TUNNEL_LOG)]
    out = capsys.readouterr().out
    assert f"Edge external IP: {EDGE_IP}" in out
    assert "Run podiumd-minikube update-hosts" in out


def test_setup_tunnel_is_a_no_op_when_the_tunnel_has_an_ip(
    machine: FakeRun, spawned: list[tuple[list[str], Path]], capsys: pytest.CaptureFixture[str]
):
    machine.on("pgrep", "-f", tunnel.TUNNEL_PATTERN, stdout="4242\n")
    tunnel.setup_tunnel()
    assert not machine.ran("minikube", "ip")
    assert not spawned
    assert f"(PID 4242) and the edge has external IP {EDGE_IP} - already up" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("log", "shown"),
    [(None, "(no log file yet)"), ("\n".join(f"line {n}" for n in range(30)), "line 10\n")],
)
def test_setup_tunnel_without_an_edge_ip_shows_the_log(
    machine: FakeRun,
    spawned: list[tuple[list[str], Path]],
    capsys: pytest.CaptureFixture[str],
    log: str | None,
    shown: str,
):
    machine.on("pgrep", "-f", tunnel.TUNNEL_PATTERN, stdout="4242\n")
    machine.on("ip", "route", "show", stdout=f"{tunnel.SERVICE_CIDR} via {NODE_IP} dev br0\n")
    machine.on("kubectl", "get", "svc", returncode=1)
    if log is not None:
        tunnel.TUNNEL_LOG.write_text(log, encoding="utf-8")
    with pytest.raises(UserError, match="no external IP for the edge after 30s"):
        tunnel.setup_tunnel()
    assert not machine.ran("sudo")
    assert not spawned
    err = capsys.readouterr().err
    assert shown in err
    assert "line 9\n" not in err


@pytest.mark.parametrize(("pgrep", "route"), [(0, f"10.96.0.0/12 via {NODE_IP} dev br0\n"), (1, "")])
def test_stop_tunnel_removes_only_an_existing_route(machine: FakeRun, pgrep: int, route: str):
    machine.on("pgrep", "-f", tunnel.TUNNEL_PATTERN, returncode=pgrep, stdout="4242\n")
    machine.on("ip", "route", "show", stdout=route)
    machine.on("sudo", returncode=2)
    tunnel.stop_tunnel()
    assert bool(machine.ran("pkill", "-f", tunnel.TUNNEL_PATTERN)) is (pgrep == 0)
    assert machine.ran("sudo") == ([tunnel.route_args("del", NODE_IP)] if route else [])
    assert not machine.ran("minikube", "ip")


def test_hosts_hint_is_quiet_when_etc_hosts_is_current(machine: FakeRun):
    assert tunnel.hosts_hint(EDGE_IP).startswith("Run podiumd-minikube update-hosts")
    tunnel.ETC_HOSTS.write_text(f"127.0.0.1 localhost\n{LINE}\n", encoding="utf-8")
    assert tunnel.hosts_hint(EDGE_IP) == "/etc/hosts already points the *.local hosts at it."


def test_update_hosts_backs_up_and_rewrites_etc_hosts(
    machine: FakeRun, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys, "stdin", Terminal())
    tunnel.update_hosts()
    hosts_file = str(tunnel.ETC_HOSTS)
    assert machine.ran("sudo") == [
        ["sudo", "-v"], ["sudo", "cp", hosts_file, f"{hosts_file}.bak"], ["sudo", "tee", hosts_file],
    ]  # fmt: skip
    assert machine.stdins[-1] == f"127.0.0.1 localhost\n{LINE}\n"
    assert f"Done. /etc/hosts now has:\n{LINE}" in capsys.readouterr().out


def test_update_hosts_leaves_a_current_etc_hosts_alone(machine: FakeRun, capsys: pytest.CaptureFixture[str]):
    tunnel.ETC_HOSTS.write_text(f"{LINE}\n", encoding="utf-8")
    tunnel.update_hosts()
    assert not machine.ran("sudo")
    assert "/etc/hosts is up to date" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("absent", "message"),
    [(("kubectl", "get", "svc"), "the edge has no external IP yet"), (("sudo", "-n"), "sudo needs a terminal")],
)
def test_update_hosts_refuses(machine: FakeRun, absent: tuple[str, ...], message: str):
    machine.on(*absent, returncode=1)
    with pytest.raises(UserError, match=message):
        tunnel.update_hosts()
    assert not machine.ran("sudo", "tee")
