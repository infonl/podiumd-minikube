"""podiumd_minikube.provision: starting the node, tuning it and loading the chart's images."""

import json
import tempfile

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import chart
from podiumd_minikube import cni
from podiumd_minikube import dependency
from podiumd_minikube import disk
from podiumd_minikube import gateway
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import memory
from podiumd_minikube import pki
from podiumd_minikube import provision
from podiumd_minikube.paths import PROFILE
from podiumd_minikube.process import UserError

SSH = ("minikube", "ssh", "-p", PROFILE, "--")


@pytest.fixture(autouse=True)
def private_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _static_pod(name: str) -> str:
    return f"apiVersion: v1\nkind: Pod\nmetadata:\n  name: {name}\nspec:\n  containers:\n  - name: {name}\n"


def _record(calls: list[str], name: str):
    return lambda *_args, **_kwargs: calls.append(name)


@pytest.mark.parametrize(("status", "started"), [(0, False), (1, True)])
def test_start_minikube_starts_only_a_stopped_profile(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, status: int, started: bool
):
    checked: list[str] = []
    manifest = tmp_path / "cilium.yaml"
    manifest.write_text("image: quay.io/cilium/cilium:v1\nimage: quay.io/cilium/operator-generic:v1\n")
    monkeypatch.setattr(cni, "write_manifest", lambda: str(manifest))
    monkeypatch.setattr(memory, "check", _record(checked, "memory"))
    fake_run.on("minikube", "status", returncode=status)
    fake_run.on("docker", "image", "inspect", "quay.io/cilium/cilium:v1", stdout=provision.PLATFORM)
    fake_run.on("minikube", "start", returncode=1)
    images = ["quay.io/cilium/cilium:v1", "quay.io/cilium/operator-generic:v1"]
    if started:
        with pytest.raises(UserError):
            provision.start_minikube(4, 8192)
    else:
        provision.start_minikube(4, 8192)
    starts = fake_run.ran("minikube", "start")
    assert bool(starts) == started
    assert checked == ([] if started else ["memory"])
    if started:
        assert starts[0][:7] == ["minikube", "start", "-p", PROFILE, "--driver=docker", "--cpus=4", "--memory=8192"]
        assert f"--cni={manifest}" in starts[0]
        assert fake_run.ran("docker", "pull") == [["docker", "pull", "--platform", provision.PLATFORM, images[1]]]
        # The cache entries go again, also after a failed start.
        assert fake_run.ran("minikube", "cache") == [["minikube", "cache", verb, *images] for verb in ("add", "delete")]


@pytest.mark.parametrize(
    ("control_plane", "services", "kubelet_config", "restarted"),
    [
        (["etcd"], ["containerd"], False, ["containerd", "kubelet"]),
        ([], [], True, ["kubelet"]),
        ([], ["kubelet"], True, ["kubelet"]),
        ([], [], False, []),
    ],
)
def test_start_node_writes_the_tuning_then_restarts_the_kubelet_once(
    fake_run: FakeRun,
    monkeypatch: pytest.MonkeyPatch,
    *,
    control_plane: list[str],
    services: list[str],
    kubelet_config: bool,
    restarted: list[str],
):
    calls: list[str] = []
    monkeypatch.setenv("MINIKUBE_CPUS", "4")
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    monkeypatch.setattr(memory, "wanted", lambda: 8192)
    monkeypatch.setattr(provision, "start_minikube", lambda cpus, mb: calls.append(f"start {cpus} {mb}"))
    for module, name in [(kube, "require_minikube_context"), (cni, "apply")]:
        monkeypatch.setattr(module, name, _record(calls, name))
    monkeypatch.setattr(provision, "tune_control_plane", lambda: [*control_plane])
    monkeypatch.setattr(provision, "tune_node_services", lambda: [*services])
    monkeypatch.setattr(provision, "isolate_pod_dns", lambda: kubelet_config)
    monkeypatch.setattr(provision, "restart_node_services", lambda names: calls.append(f"restart {names}"))
    monkeypatch.setattr(provision, "wait_for_control_plane", lambda names: calls.append(f"wait {names}"))
    provision.start_node()
    assert calls == [
        "start 4 8192",
        "require_minikube_context",
        "apply",
        f"restart {restarted}",
        f"wait {control_plane}",
    ]
    assert fake_run.ran("docker", "update") == [["docker", "update", "--cpus=4", "--cpuset-cpus=0-3", PROFILE]]


def _apiserver(env: dict[str, str], ready: list[bool]) -> str:
    return json.dumps(
        {
            "spec": {"containers": [{"env": [{"name": name, "value": value} for name, value in env.items()]}]},
            "status": {"containerStatuses": [{"ready": state} for state in ready]},
        }
    )


TUNED = provision.CONTROL_PLANE_ENV["kube-apiserver"]


@pytest.mark.parametrize(
    ("returncode", "pod", "ready"),
    [
        (1, "", False),
        (0, _apiserver(TUNED, [True]), True),
        (0, _apiserver(TUNED, [False]), False),
        (0, _apiserver(TUNED, []), False),
        (0, _apiserver({"GOGC": "50"}, [True]), False),
    ],
)
def test_tune_control_plane_writes_untuned_pods_and_waits_until_tuned_and_ready(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, *, returncode: int, pod: str, ready: bool
):
    tuned = {name: provision.with_control_plane_env(_static_pod(name)) or "" for name in provision.CONTROL_PLANE_ENV}
    tuned["kube-apiserver"] = _static_pod("kube-apiserver")
    for name, manifest in tuned.items():
        fake_run.on(*SSH, f"sudo cat {provision.STATIC_PODS}/{name}.yaml", stdout=manifest)
    fake_run.on(
        "kubectl", "get", "pod", "-n", "kube-system", f"kube-apiserver-{PROFILE}", returncode=returncode, stdout=pod
    )
    monkeypatch.setattr(provision, "CONTROL_PLANE_TIMEOUT", 0)
    changed = provision.tune_control_plane()
    assert changed == ["kube-apiserver"]
    if ready:
        provision.wait_for_control_plane(changed)
    else:
        with pytest.raises(UserError, match="kube-apiserver not ready 0s after its env change"):
            provision.wait_for_control_plane(changed)
    assert [call[-1] for call in fake_run.ran("minikube", "cp")] == ["/etc/kubernetes/.kube-apiserver.yaml"]
    moved = [call[-1] for call in fake_run.ran(*SSH) if "sudo mv" in call[-1]]
    assert len(moved) == 1 and "sudo chmod 600 /etc/kubernetes/.kube-apiserver.yaml" in moved[0]
    assert moved[0].endswith(
        "sudo mv /etc/kubernetes/.kube-apiserver.yaml /etc/kubernetes/manifests/kube-apiserver.yaml"
    )


def test_tune_node_services_writes_only_what_differs(fake_run: FakeRun):
    (path, content) = provision.node_drop_ins()["kubelet"]
    fake_run.on(*SSH, f"cat {path} 2>/dev/null || true", stdout=content)
    assert provision.tune_node_services() == ["containerd", "systemd-journald"]
    assert len(fake_run.ran("minikube", "cp")) == 2
    assert not [call for call in fake_run.ran(*SSH) if "systemctl" in call[-1]]


def test_restart_node_services_restarts_them_in_one_command(fake_run: FakeRun):
    provision.restart_node_services([])
    provision.restart_node_services(["containerd", "kubelet"])
    assert fake_run.calls == [[*SSH, "sudo systemctl daemon-reload && sudo systemctl restart containerd kubelet"]]


RESOLV = "nameserver 192.168.49.1\nsearch home\n"
KUBELET = "kind: KubeletConfiguration\n"


@pytest.mark.parametrize(
    ("pod_resolv", "kubelet", "written"),
    [
        ("nameserver 192.168.49.1\n", provision.with_pod_resolv_conf(KUBELET) or "", []),
        ("", KUBELET, [provision.POD_RESOLV_CONF, provision.KUBELET_CONFIG]),
    ],
)
def test_isolate_pod_dns_writes_only_what_differs(fake_run: FakeRun, pod_resolv: str, kubelet: str, written: list[str]):
    fake_run.on(*SSH, "cat /etc/resolv.conf", stdout=RESOLV)
    fake_run.on(*SSH, f"cat {provision.POD_RESOLV_CONF} 2>/dev/null || true", stdout=pod_resolv)
    fake_run.on(*SSH, f"sudo cat {provision.KUBELET_CONFIG}", stdout=kubelet)
    changed = provision.isolate_pod_dns()
    commands = [call[-1] for call in fake_run.ran(*SSH)]
    assert [command.rsplit(" ", 1)[-1] for command in commands if "sudo mv" in command] == written
    assert changed == bool(written)
    assert not [command for command in commands if "systemctl" in command]


def test_chart_images_render_everything_without_digests(monkeypatch: pytest.MonkeyPatch):
    rendered = "image: a/app:1.0@sha256:" + "0" * 64 + "\nimage: b/db:2\n"
    monkeypatch.setattr(chart, "everything_args", lambda: ["--set", "x=1"])
    monkeypatch.setattr(manifests, "helm_template", lambda *args: rendered if args == ("--set", "x=1") else "")
    assert provision.chart_images() == ["a/app:1.0", "b/db:2"]


def test_load_images_skips_what_minikube_has(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]):
    fake_run.on("minikube", "image", "ls", stdout="docker.io/a/app:1.0\n")
    provision.load_images(["a/app:1.0"])
    assert not fake_run.ran("docker")
    assert "nothing to pull" in capsys.readouterr().out


def test_load_images_pulls_saves_and_loads_and_warns_on_failures(fake_run: FakeRun, capsys: pytest.CaptureFixture[str]):
    fake_run.on("minikube", "image", "ls", stdout="docker.io/a/app:1.0\n")
    fake_run.on("docker", "pull", "--platform", provision.PLATFORM, "c/bad:3", returncode=1)
    fake_run.on("docker", "save", "--platform", provision.PLATFORM, "c/bad:3", returncode=1, stderr="no such image")
    provision.load_images(["a/app:1.0", "b/db:2", "c/bad:3"])
    assert [call[-1] for call in fake_run.ran("docker", "pull")] == ["b/db:2", "c/bad:3"]
    assert sorted(call[4] for call in fake_run.ran("docker", "save")) == ["b/db:2", "c/bad:3"]
    loads = fake_run.ran("minikube", "image", "load")
    assert len(loads) == 1 and loads[0][-1].endswith("b_db_2.tar")
    err = capsys.readouterr().err
    assert "pulling c/bad:3 failed" in err
    assert "saving c/bad:3 failed" in err and "no such image" in err
    assert "not in minikube after loading: b/db:2 c/bad:3" in err


def test_load_images_pulls_only_what_the_host_lacks_for_the_platform(fake_run: FakeRun):
    fake_run.on("docker", "image", "inspect", "a/app:1.0", stdout=f"{provision.PLATFORM}\n")
    fake_run.on("docker", "image", "inspect", "b/db:2", stdout="linux/arm64\n")
    fake_run.on("docker", "image", "inspect", "c/new:3", returncode=1)
    provision.load_images(["a/app:1.0", "b/db:2", "c/new:3"])
    assert [call[-1] for call in fake_run.ran("docker", "pull")] == ["b/db:2", "c/new:3"]
    assert sorted(call[4] for call in fake_run.ran("docker", "save")) == ["a/app:1.0", "b/db:2", "c/new:3"]


def test_provision_runs_every_step_in_order(fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    monkeypatch.setattr(disk, "check", lambda need: calls.append(f"disk {need.fail_gib}"))
    monkeypatch.setattr(provision, "chart_images", lambda: ["a/app:1.0"])
    monkeypatch.setattr(provision, "load_images", lambda images: calls.append(f"load {' '.join(images)}"))
    monkeypatch.setattr(provision, "remove_old_tags", lambda images: calls.append(f"remove old {' '.join(images)}"))
    for module, name in [
        (provision, "start_node"),
        (gateway, "install"),
        (pki, "install_cert_manager"),
        (pki, "install_issuer"),
        (dependency, "sync"),
    ]:
        monkeypatch.setattr(module, name, _record(calls, name))
    provision.provision()
    assert calls == [
        f"disk {disk.PROVISION.fail_gib}",
        "start_node",
        "install",
        "install_cert_manager",
        "install_issuer",
        "sync",
        "load a/app:1.0",
        "remove old a/app:1.0",
    ]
    assert fake_run.calls == [
        ["kubectl", "label", "node", PROFILE, "kubernetes.azure.com/agentpool=userpool", "--overwrite"]
    ]


def test_old_tags_are_other_tags_of_the_charts_repositories():
    images = ["docker.io/library/busybox:1.37", "quay.io/keycloak/keycloak:26.8.1", "registry:5000/app:2"]
    local = [
        "busybox:1.36", "busybox:1.37", "quay.io/keycloak/keycloak:26.7.3", "quay.io/keycloak/keycloak:26.8.1",
        "registry:5000/app:1", "postgres:15", "busybox:<none>",
    ]  # fmt: skip
    assert provision.old_tags(images, local) == [
        "busybox:1.36",
        "quay.io/keycloak/keycloak:26.7.3",
        "registry:5000/app:1",
    ]


def test_remove_old_tags_runs_one_docker_rmi(fake_run: FakeRun):
    fake_run.on("docker", "images", stdout="busybox:1.36\nbusybox:1.37\npostgres:15\n")
    provision.remove_old_tags(["busybox:1.37"])
    assert fake_run.ran("docker", "rmi") == [["docker", "rmi", "busybox:1.36"]]


def test_load_images_removes_the_nodes_tarballs_after_each_batch(fake_run: FakeRun):
    provision.load_images(["a/app:1.0"])
    assert fake_run.ran("minikube", "ssh")[-1][-1] == f"sudo rm -rf {provision.NODE_IMAGE_TARBALLS}/*"
