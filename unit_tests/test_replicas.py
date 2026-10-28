"""podiumd_minikube.replicas: the merge patches and the on/off switch."""

from typing import Any

import pytest

from podiumd_minikube import deploy
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube import replicas


def _keycloak(instances: int) -> dict[str, Any]:
    return {
        "apiVersion": "k8s.keycloak.org/v2alpha1",
        "kind": "Keycloak",
        "metadata": {"name": "keycloak"},
        "spec": {"instances": instances, "hostname": {"hostname": "keycloak.local"}},
    }


PDB = {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": "keycloak"}, "spec": {}}
ONE = [_keycloak(1)]
SEVERAL = [_keycloak(2), PDB]


def test_merge_patch_holds_only_the_changed_fields():
    old = {"spec": {"instances": 1, "keep": "x", "gone": 1}, "list": [1, 2]}
    new = {"spec": {"instances": 2, "keep": "x"}, "list": [1, 2, 3]}
    assert replicas.merge_patch(old, new) == {"spec": {"instances": 2, "gone": None}, "list": [1, 2, 3]}


def test_changes_patch_the_differing_object_and_create_or_delete_the_rest():
    created, patches, deleted = replicas.changes(ONE, SEVERAL)
    assert created == [PDB]
    assert patches == {("k8s.keycloak.org/v2alpha1", "Keycloak", "keycloak"): {"spec": {"instances": 2}}}
    assert not deleted
    assert replicas.changes(SEVERAL, ONE)[2] == [("policy/v1", "PodDisruptionBudget", "keycloak")]


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch, poll_once: None) -> list[str]:
    """kubectl calls of replicas.switch against fake renders and StatefulSets at the wanted count."""
    calls: list[str] = []
    counts = {"redis-ha": 3, "keycloak": 2}
    monkeypatch.setattr(kube, "require_minikube_context", lambda: None)
    monkeypatch.setattr(deploy, "options", lambda **_: deploy.Options())
    monkeypatch.setattr(deploy.Options, "render", lambda _self, *args: manifests.Render(SEVERAL if args else ONE, []))
    monkeypatch.setattr(kube, "kubectl_shown", lambda *args, stdin=None: calls.append(" ".join(args)))
    monkeypatch.setattr(
        kube,
        "get_json",
        lambda resource, *_: {"spec": {"replicas": counts[resource.split("/")[1]]}, "metadata": {}, "status": {}},
    )
    monkeypatch.setattr(deploy, "rolled_out", lambda _statefulset: True)
    monkeypatch.setattr(kube, "first_pod", lambda selector: calls.append(f"master {selector}"))
    return calls


def test_on_patches_creates_and_waits_for_the_chart_counts(switched: list[str]):
    replicas.switch(on=True)
    assert switched == [
        "apply -n podiumd -f -",
        'patch Keycloak.k8s.keycloak.org/keycloak -n podiumd --type merge -p {"spec": {"instances": 2}}',
        "master app=redis-ha,redis-role=master",
    ]


def test_off_fails_while_a_statefulset_keeps_its_pods(switched: list[str]):
    with pytest.raises(process.UserError, match="not at their pod count"):
        replicas.switch(on=False)
    assert switched[-1] == "delete PodDisruptionBudget.policy/keycloak -n podiumd --ignore-not-found"


def test_off_promotes_the_remaining_redis_pod(switched: list[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(replicas, "_counted", lambda counts: list(counts))
    replicas.switch(on=False)
    assert switched[-2:] == [
        "exec -n podiumd redis-ha-0 -c redis-ha -- redis-cli REPLICAOF NO ONE",
        "master app=redis-ha,redis-role=master",
    ]
