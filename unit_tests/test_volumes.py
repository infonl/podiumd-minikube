"""podiumd_minikube.volumes: a pod's fsGroup on minikube's hostPath volumes."""

import json

from conftest import FakeRun

from podiumd_minikube import volumes

ETCD = {
    "kind": "StatefulSet",
    "metadata": {"name": "frankgateway-etcd"},
    "spec": {
        "template": {"spec": {"securityContext": {"fsGroup": 1001}}},
        "volumeClaimTemplates": [{"metadata": {"name": "data"}}],
    },
}
APP = {
    "kind": "Deployment",
    "metadata": {"name": "app"},
    "spec": {
        "template": {
            "spec": {
                "securityContext": {"fsGroup": 1000},
                "volumes": [{"name": "v", "persistentVolumeClaim": {"claimName": "app-data"}}],
            }
        }
    },
}
PLAIN = {"kind": "Deployment", "metadata": {"name": "plain"}, "spec": {"template": {"spec": {"securityContext": None}}}}


def _claim(name: str, volume: str) -> dict[str, object]:
    return {"metadata": {"name": name}, "spec": {"volumeName": volume}}


def _volume(name: str, path: str | None) -> dict[str, object]:
    return {"metadata": {"name": name}, "spec": {"hostPath": {"path": path}} if path else {"csi": {}}}


def test_wanted_reads_claim_names_and_statefulset_claim_prefixes():
    assert volumes.wanted([ETCD, APP, PLAIN]) == {"data-frankgateway-etcd-": 1001, "app-data": 1000}


def test_changes_are_the_bound_claims_on_hostpath_volumes():
    claims = [_claim("data-frankgateway-etcd-0", "pv-1"), _claim("app-data", "pv-2"), _claim("other", "pv-3")]
    found = volumes.changes(
        volumes.wanted([ETCD, APP]),
        claims,
        [_volume("pv-1", "/h/etcd"), _volume("pv-2", None), _volume("pv-3", "/h/o")],
    )
    assert found == {"/h/etcd": 1001}


def test_script_changes_a_path_only_when_its_group_differs():
    assert volumes.script({"/h/etcd": 1001}) == (
        '[ "$(sudo stat -c %g /h/etcd)" = 1001 ] || { sudo chgrp -R 1001 /h/etcd && sudo chmod -R g+rwX /h/etcd'
        " && sudo find /h/etcd -type d -exec chmod g+s {} + && echo /h/etcd; }"
    )


def test_apply_runs_one_node_command(fake_run: FakeRun):
    fake_run.on("kubectl", "get", "pvc", stdout=json.dumps({"items": [_claim("data-frankgateway-etcd-0", "pv-1")]}))
    fake_run.on("kubectl", "get", "pv", stdout=json.dumps({"items": [_volume("pv-1", "/h/etcd")]}))
    fake_run.on("minikube", "ssh", stdout="/h/etcd\n")
    volumes.apply_fs_groups([ETCD], "podiumd")
    (ssh,) = fake_run.ran("minikube", "ssh")
    assert "chgrp -R 1001 /h/etcd" in ssh[-1]


def test_apply_skips_the_cluster_without_fs_groups(fake_run: FakeRun):
    volumes.apply_fs_groups([PLAIN], "podiumd")
    assert not fake_run.calls
