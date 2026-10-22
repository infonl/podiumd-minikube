"""The apps' Redis host, redis-ha-master, reaches the redis-operator's master pod (labelled by podiumd's CronJob)."""

import json

from conftest import NAMESPACE
from conftest import kubectl

from podiumd_minikube import services


def test_master_service_and_flush_redis_find_the_master_pod():
    pods = json.loads(kubectl("get", "pods", "-n", NAMESPACE, "-l", services.REDIS_MASTER, "-o", "json"))["items"]
    assert len(pods) == 1, [pod["metadata"]["name"] for pod in pods]
    endpoints = json.loads(kubectl("get", "endpoints", "redis-ha-master", "-n", NAMESPACE, "-o", "json"))
    addresses = [a["ip"] for subset in endpoints.get("subsets", []) for a in subset.get("addresses", [])]
    assert addresses == [pods[0]["status"]["podIP"]]
