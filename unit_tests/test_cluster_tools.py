"""Context guard, /etc/hosts, port mappings, teardown, provision and polling helpers."""

from pathlib import Path

import pytest
import yaml

from conftest import FakeRun

from podiumd_minikube import cni
from podiumd_minikube import hosts
from podiumd_minikube import kube
from podiumd_minikube import polling
from podiumd_minikube import ports
from podiumd_minikube import provision
from podiumd_minikube import teardown
from podiumd_minikube import tunnel
from podiumd_minikube.process import UserError


def test_require_minikube_context_refuses_another_context(fake_run: FakeRun):
    fake_run.on("kubectl", "config", "current-context", stdout="podiumd-aks\n")
    with pytest.raises(UserError, match="'podiumd-aks', not 'minikube'"):
        kube.require_minikube_context()
    fake_run.on("kubectl", "config", "current-context", stdout="minikube\n")
    kube.require_minikube_context()


def test_replace_hosts_line_drops_earlier_lines_of_this_project():
    text = "127.0.0.1 localhost\n10.0.0.1 zac.local keycloak.local\n"
    line = hosts.hosts_line("10.0.0.2", ["zac.local", "pabc.local"])
    assert tunnel.replace_hosts_line(text, line) == f"127.0.0.1 localhost\n{line}\n"
    assert line == f"10.0.0.2 zac.local pabc.local  {hosts.MARKER}"


def test_routes_resolve_named_ports_through_the_service():
    ingress = {
        "spec": {
            "rules": [
                {
                    "host": "zac.local",
                    "http": {"paths": [{"backend": {"service": {"name": "zac", "port": {"name": "http"}}}}]},
                },
                {
                    "host": "pabc.local",
                    "http": {"paths": [{"backend": {"service": {"name": "pabc", "port": {"number": 80}}}}]},
                },
            ]
        }
    }
    services = {"zac": {"spec": {"ports": [{"name": "http", "port": 8080}]}}}
    found = ports.routes([ingress, ingress])
    assert [(route.host, ports.port_number(route, services)) for route in found] == [
        ("pabc.local", "80"),
        ("zac.local", "8080"),
    ]


def test_bound_volumes_selects_by_claim_namespace():
    volumes = [
        {"metadata": {"name": "mine"}, "spec": {"claimRef": {"namespace": "podiumd"}}},
        {"metadata": {"name": "other"}, "spec": {"claimRef": {"namespace": "kube-system"}}},
        {"metadata": {"name": "unbound"}, "spec": {}},
    ]
    assert teardown.bound_volumes(volumes) == ["mine"]


def test_confirmed_aborts_unless_yes_is_typed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert not teardown.confirmed("summary", yes=False)
    assert teardown.confirmed("summary", yes=True)


def test_missing_images_match_by_suffix():
    loaded = ["docker.io/library/busybox:1.38.0", "registry/zac:5"]
    assert provision.missing_images(["busybox:1.38.0", "zac:6"], loaded) == ["zac:6"]


def test_wait_until_returns_the_first_truthy_result_or_none():
    answers = iter(["", "", "10.0.0.1"])
    assert polling.wait_until(lambda: next(answers), timeout=5, interval=0) == "10.0.0.1"
    assert polling.wait_until(lambda: None, timeout=0, interval=0) is None


def test_serving_pod_skips_terminating_and_unready_pods():
    def pod(name: str, *, ready: bool = True, deleting: bool = False) -> dict[str, object]:
        metadata = {"name": name, **({"deletionTimestamp": "2026-01-01T00:00:00Z"} if deleting else {})}
        status = {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}
        return {"metadata": metadata, "status": status}

    assert kube.serving_pod([pod("old", deleting=True), pod("starting", ready=False), pod("new")]) == "new"
    assert kube.serving_pod([pod("old", deleting=True)]) == ""


def test_route_args_cover_the_service_network_via_the_node(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tunnel, "_ip", lambda: "/usr/bin/ip")
    assert tunnel.route_args("replace", "192.168.49.2") == [
        "sudo", "/usr/bin/ip", "route", "replace", "10.96.0.0/12", "via", "192.168.49.2",
    ]  # fmt: skip


def test_sudoers_rule_allows_exactly_the_routes_commands(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tunnel, "_ip", lambda: "/usr/bin/ip")
    monkeypatch.setattr("getpass.getuser", lambda: "kees")
    fake_run.on("minikube", "ip", stdout="192.168.49.2\n")
    rule = tunnel.sudoers_rule().splitlines()[1]
    assert rule == (
        "kees ALL=(root) NOPASSWD: /usr/bin/ip route replace 10.96.0.0/12 via 192.168.49.2,"
        " /usr/bin/ip route del 10.96.0.0/12 via 192.168.49.2"
    )


@pytest.mark.parametrize("verified", [True, False])
def test_install_sudoers_checks_installs_and_verifies(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, *, verified: bool
):
    monkeypatch.setattr(tunnel, "_ip", lambda: "/usr/bin/ip")
    fake_run.on("minikube", "ip", stdout="192.168.49.2\n")
    fake_run.on("sudo", "-n", "-l", returncode=0 if verified else 1)
    if verified:
        tunnel.install_sudoers()
    else:
        with pytest.raises(tunnel.UserError, match="sudo still asks"):
            tunnel.install_sudoers()
    (check,) = [call for call in fake_run.calls if call[-2] == "-cf"]
    (install,) = fake_run.ran("sudo", "install")
    assert install[-2:] == [check[-1], tunnel.SUDOERS_FILE]


@pytest.mark.parametrize(
    ("tty", "allowed", "refused"), [(True, False, False), (False, True, False), (False, False, True)]
)
def test_require_sudo_refuses_only_without_a_terminal_or_a_rule(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, *, tty: bool, allowed: bool, refused: bool
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    fake_run.on("sudo", "-n", "-l", "/usr/bin/ip", returncode=0 if allowed else 1)
    if refused:
        with pytest.raises(tunnel.UserError, match="setup-tunnel sudoers"):
            tunnel.require_sudo("setup-tunnel", ["/usr/bin/ip", "route"])
    else:
        tunnel.require_sudo("setup-tunnel", ["/usr/bin/ip", "route"])


def test_routed_via_reads_the_gateway_of_the_route(fake_run: FakeRun):
    fake_run.on("ip", "route", "show", stdout="10.96.0.0/12 via 192.168.49.2 dev br-f9a9ff7171ee \n")
    assert tunnel.routed_via() == "192.168.49.2"
    fake_run.on("ip", "route", "show", stdout="")
    assert tunnel.routed_via() is None


def test_replace_hosts_line_is_unchanged_when_the_line_is_current():
    line = "10.0.0.1 zac.local  # podiumd-minikube"
    text = f"127.0.0.1 localhost\n{line}\n"
    assert tunnel.replace_hosts_line(text, line) == text


STATIC_POD = """apiVersion: v1
kind: Pod
metadata:
  name: etcd
spec:
  containers:
  - command:
    - etcd
    name: etcd
"""


def test_with_control_plane_env_sets_each_pods_values_once():
    tuned = provision.with_control_plane_env(STATIC_POD)
    assert tuned is not None and "GOGC" in tuned and "GOMEMLIMIT" not in tuned and "- etcd" in tuned
    assert provision.with_control_plane_env(tuned) is None
    # kube-apiserver already tuned with GOGC only: GOMEMLIMIT is added, GOGC kept once.
    apiserver = STATIC_POD.replace("name: etcd", "name: kube-apiserver").replace(
        "    name: kube-apiserver\n", "    name: kube-apiserver\n    env:\n    - name: GOGC\n      value: '50'\n"
    )
    tuned = provision.with_control_plane_env(apiserver)
    assert tuned is not None and "GOMEMLIMIT" in tuned and tuned.count("GOGC") == 1


def test_tune_control_plane_leaves_tuned_static_pods_alone(fake_run: FakeRun):
    fake_run.on("minikube", "ssh", stdout=provision.with_control_plane_env(STATIC_POD) or "")
    provision.tune_control_plane()
    assert not fake_run.ran("minikube", "cp")


def test_cpuset_args_pin_the_node_to_its_first_cpus_only_when_the_host_has_more():
    assert provision.cpuset_args(6, 24) == ["--cpuset-cpus=0-5"]
    assert not provision.cpuset_args(6, 6)


@pytest.mark.parametrize(("host", "node"), [(24, 8), (12, 8), (8, 6), (6, 4), (4, 2), (2, 2)])
def test_default_cpus_leaves_a_quarter_of_the_host_free_up_to_8(host: int, node: int):
    assert provision.default_cpus(host) == node


def test_nameservers_only_drops_search_and_options():
    resolv = "# Generated by Docker Engine.\nnameserver 192.168.49.1\r\nsearch home\noptions ndots:0\n"
    assert provision.nameservers_only(resolv) == "nameserver 192.168.49.1\n"


def test_with_pod_resolv_conf_sets_it_once():
    config = provision.with_pod_resolv_conf("kind: KubeletConfiguration\nclusterDomain: cluster.local\n")
    assert config is not None
    assert f"resolvConf: {provision.POD_RESOLV_CONF}" in config
    assert "clusterDomain: cluster.local" in config
    assert provision.with_pod_resolv_conf(config) is None


def test_confirmed_aborts_without_input(monkeypatch: pytest.MonkeyPatch):
    def no_input(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)
    assert not teardown.confirmed("summary", yes=False)


def test_cilium_render_gets_capacity_env_and_a_memory_limit(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(cni, "CHART", tmp_path / "cilium.tgz")
    cni.CHART.write_text("")
    agent = {
        "kind": "DaemonSet",
        "metadata": {"name": "cilium"},
        "spec": {"template": {"spec": {"containers": [{"name": "cilium-agent", "resources": cni.AGENT_RESOURCES}]}}},
    }
    fake_run.on("helm", "template", stdout=yaml.safe_dump(agent))
    env = yaml.safe_load(cni.render())["spec"]["template"]["spec"]["containers"][0]["env"]
    names = {item["name"] for item in env}
    assert {"GODEBUG", "GOMEMLIMIT"} <= names
    values = yaml.safe_load(fake_run.stdins[0] or "")
    assert values["kubeProxyReplacement"] is True
    assert values["bpf"]["lbExternalClusterIP"] is False


def test_cilium_apply_refuses_a_kindnet_node(fake_run: FakeRun):
    fake_run.on("kubectl", "get", "daemonset/kindnet")
    with pytest.raises(UserError, match="teardown-cluster"):
        cni.apply()
    assert not fake_run.ran("kubectl", "apply")
