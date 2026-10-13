"""manifests.tune_solr's JVM settings reach the pods the solr-operator and zookeeper-operator build."""

import json

from conftest import NAMESPACE
from conftest import kubectl

from podiumd_minikube import manifests


def _env(statefulset):
    spec = json.loads(kubectl("get", "statefulset", statefulset, "-n", NAMESPACE, "-o", "json"))["spec"]
    return {item["name"]: item.get("value") for item in spec["template"]["spec"]["containers"][0].get("env", [])}


def test_solr_and_zookeeper_get_their_jvm_settings():
    solr = _env("zac-solr-solrcloud")
    zookeeper = _env("zac-solr-solrcloud-zookeeper")
    assert {key: solr.get(key) for key in manifests.SOLR_ENV} == manifests.SOLR_ENV
    assert {key: zookeeper.get(key) for key in manifests.ZOOKEEPER_ENV} == manifests.ZOOKEEPER_ENV
