"""kiss.website_hook_blocked: when KISS's crawler hook cannot reach Elasticsearch yet."""

from typing import Any

import pytest

from podiumd_minikube import kiss
from podiumd_minikube import kube
from podiumd_minikube import manifests

HOOK = "kiss-crawler-index-template"
PEER = {"podSelector": {"matchLabels": {"batch.kubernetes.io/job-name": HOOK}}}
OTHER = {"podSelector": {"matchLabels": {"app": "kibana"}}}


def _hook() -> manifests.Doc:
    env = [{"name": "ELASTIC_BASE_URL", "value": "https://kiss-es-http.podiumd.svc:9200"}]
    return {
        "kind": "Job",
        "metadata": {"name": HOOK},
        "spec": {"template": {"spec": {"containers": [{"name": "index-template", "env": env}]}}},
    }


def _policy(*peers: manifests.Doc) -> manifests.Doc:
    return {
        "kind": "NetworkPolicy",
        "metadata": {"name": "podiumd-elasticsearch"},
        "spec": {"ingress": [{"from": list(peers)}]},
    }


def _render(*docs: manifests.Doc) -> manifests.Render:
    return manifests.Render(docs=list(docs), large_configmaps=[])


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Live objects by "kind/name"; kube.exists and kube.get_json read them."""
    live: dict[str, Any] = {}
    monkeypatch.setattr(kube, "exists", lambda resource, namespace="": resource in live)
    monkeypatch.setattr(kube, "get_json", lambda kind, name, *_args: live[f"{kind}/{name}"])
    return live


def test_no_hook_without_a_website(cluster: dict[str, Any]):
    assert kiss.website_hook_blocked(_render(_policy(PEER))) == ""


def test_blocked_before_elasticsearch_exists(cluster: dict[str, Any]):
    assert kiss.website_hook_blocked(_render(_hook(), _policy(PEER))) == "Service kiss-es-http does not exist yet"


def test_blocked_while_the_live_policy_lacks_the_hooks_peer(cluster: dict[str, Any]):
    cluster["service/kiss-es-http"] = {}
    cluster["networkpolicy/podiumd-elasticsearch"] = _policy(OTHER)
    assert kiss.website_hook_blocked(_render(_hook(), _policy(OTHER, PEER))) == (
        "NetworkPolicy podiumd-elasticsearch does not admit it yet"
    )


def test_not_blocked_once_the_live_policy_admits_the_hook(cluster: dict[str, Any]):
    cluster["service/kiss-es-http"] = {}
    cluster["networkpolicy/podiumd-elasticsearch"] = _policy(OTHER, PEER)
    assert kiss.website_hook_blocked(_render(_hook(), _policy(OTHER, PEER))) == ""
