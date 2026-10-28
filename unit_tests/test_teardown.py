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
    fake_run.on("kubectl", "get", teardown.CLUSTER_KINDS, stdout=json.dumps({"items": []}))
    fake_run.on("minikube", "ssh", returncode=node_code)
    assert teardown.reset_namespace(yes=True) == 0
    deleted = sorted(call[3] for call in fake_run.ran("kubectl", "delete", "namespace"))
    assert deleted == sorted(teardown.RESET_NAMESPACES)
    uninstalled = sorted(call[2] for call in fake_run.ran("helm", "uninstall"))
    assert uninstalled == sorted(r for releases in teardown.NAMESPACE_RELEASES.values() for r in releases)
    assert all("--no-hooks" in call for call in fake_run.ran("helm", "uninstall"))
    assert fake_run.ran("kubectl", "delete", "pv") == (
        [["kubectl", "delete", "pv", "pv-postgres"]] if node_code == 0 else []
    )
    assert (
        f"sudo rm -rf /data/{NAMESPACE}/* /tmp/hostpath-provisioner/{NAMESPACE} "
        in fake_run.ran("minikube", "ssh")[0][-1]
    )
    assert fake_run.ran("kubectl", "get", teardown.CLUSTER_KINDS)
    assert not fake_run.ran("kubectl", "delete", "clusterrole")
    assert forgotten == ["forget_vault"]
    captured = capsys.readouterr()
    assert ("Deleting PersistentVolumes still bound to them...\n  (none found)" in captured.out) == (node_code == 1)
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


def test_cluster_leftovers_match_the_instance_label_or_the_release_annotation():
    items = [
        {"kind": "ClusterRole", "metadata": {"name": "a", "labels": {"app.kubernetes.io/instance": "podiumd"}}},
        {
            "kind": "ClusterRole",
            "metadata": {"name": "b", "annotations": {"meta.helm.sh/release-name": "podiumd-minikube"}},
        },
        {"kind": "ClusterRoleBinding", "metadata": {"name": "c", "labels": {"app.kubernetes.io/instance": "cilium"}}},
    ]
    assert teardown.cluster_leftovers(items) == ["clusterrole/a", "clusterrole/b"]


def test_clear_finalizers_patches_only_objects_that_have_them(fake_run: FakeRun):
    fake_run.on("kubectl", "api-resources", stdout="pods\nsolrclouds.solr.apache.org\n")
    items = [
        {
            "apiVersion": "solr.apache.org/v1beta1",
            "kind": "SolrCloud",
            "metadata": {"name": "zac", "finalizers": ["x"]},
        },
        {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "p"}},
    ]
    fake_run.on("kubectl", "get", "pods,solrclouds.solr.apache.org", stdout=json.dumps({"items": items}))
    assert teardown.clear_finalizers("podiumd") == 1
    assert [call[2] for call in fake_run.ran("kubectl", "patch")] == ["solrcloud.solr.apache.org/zac"]
