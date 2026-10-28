"""podiumd_minikube.teardown: what reset-namespace and teardown-cluster delete."""

import json

from typing import Any

import pytest

from conftest import FakeRun

from podiumd_minikube import openbao
from podiumd_minikube import provision
from podiumd_minikube import teardown
from podiumd_minikube import tunnel
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import PROFILE

VOLUMES = {
    "items": [
        {"metadata": {"name": "pv-postgres"}, "spec": {"claimRef": {"namespace": NAMESPACE}}},
        {"metadata": {"name": "pv-other"}, "spec": {"claimRef": {"namespace": "kube-system"}}},
    ]
}


@pytest.fixture
def forgotten(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(openbao, "forget_vault", lambda: calls.append("forget_vault"))
    return calls


def test_confirmed_when_yes_is_typed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert teardown.confirmed("summary", yes=False)


@pytest.mark.parametrize(("volumes", "node_code"), [(VOLUMES, 0), ({"items": []}, 1)])
def test_reset_namespace_deletes_the_namespace_its_volumes_and_its_cluster_objects(
    fake_run: FakeRun,
    forgotten: list[str],
    capsys: pytest.CaptureFixture[str],
    volumes: dict[str, list[dict[str, Any]]],
    node_code: int,
):
    fake_run.on("kubectl", "config", "current-context", stdout=f"{PROFILE}\n")
    fake_run.on("kubectl", "get", "pv", stdout=json.dumps(volumes))
    fake_run.on("minikube", "ssh", returncode=node_code)
    assert teardown.reset_namespace(yes=True) == 0
    assert fake_run.ran("kubectl", "delete", "namespace") == [
        ["kubectl", "delete", "namespace", *teardown.RESET_NAMESPACES, "--ignore-not-found"]
    ]
    waited = [call[-2] for call in fake_run.ran("kubectl", "wait")]
    assert waited == [f"namespace/{namespace}" for namespace in teardown.RESET_NAMESPACES]
    assert fake_run.ran("kubectl", "delete", "pv") == (
        [["kubectl", "delete", "pv", "pv-postgres"]] if node_code == 0 else []
    )
    assert (
        f"sudo rm -rf /data/{NAMESPACE}/* /tmp/hostpath-provisioner/{NAMESPACE} "
        in fake_run.ran("minikube", "ssh")[0][-1]
    )
    assert fake_run.calls[-1][-3:] == [
        "-l", f"app.kubernetes.io/instance in ({','.join(teardown.RESET_INSTANCES)})", "--ignore-not-found",
    ]  # fmt: skip
    assert forgotten == ["forget_vault"]
    captured = capsys.readouterr()
    assert ("(none found)" in captured.out) == (node_code == 1)
    assert ("could not reach the minikube node" in captured.err) == (node_code == 1)


def test_reset_namespace_deletes_nothing_unconfirmed(
    fake_run: FakeRun, forgotten: list[str], monkeypatch: pytest.MonkeyPatch
):
    fake_run.on("kubectl", "config", "current-context", stdout=PROFILE)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    assert teardown.reset_namespace(yes=False) == 1
    assert fake_run.calls == [["kubectl", "config", "current-context"]]
    assert not forgotten


@pytest.mark.parametrize(("answer", "code"), [("yes", 0), ("no", 1)])
def test_teardown_cluster_stops_the_tunnel_and_deletes_the_profile_once_confirmed(
    fake_run: FakeRun, forgotten: list[str], monkeypatch: pytest.MonkeyPatch, answer: str, code: int
):
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    monkeypatch.setattr(tunnel, "stop_tunnel", lambda: forgotten.append("stop_tunnel"))
    monkeypatch.setattr(provision, "chart_images", lambda: ["a/app:1.0"])
    monkeypatch.setattr(provision, "remove_old_tags", lambda images: forgotten.append(f"remove old {images[0]}"))
    assert teardown.teardown_cluster(yes=False) == code
    assert fake_run.calls == ([["minikube", "delete", "-p", PROFILE]] if code == 0 else [])
    assert forgotten == (["stop_tunnel", "forget_vault", "remove old a/app:1.0"] if code == 0 else [])
