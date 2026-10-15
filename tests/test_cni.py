"""Cilium is the node's only CNI and service proxy, with the values cni.values() sets."""

import json

from conftest import kubectl

from podiumd_minikube import cni


def test_cilium_replaces_kindnet_and_kube_proxy():
    daemonsets = json.loads(kubectl("get", "daemonsets", "-n", "kube-system", "-o", "json"))["items"]
    names = {ds["metadata"]["name"]: ds for ds in daemonsets}
    assert "kindnet" not in names
    assert "kube-proxy" not in names
    status = names["cilium"]["status"]
    assert status["numberReady"] == status["desiredNumberScheduled"] == 1


def test_cilium_config_matches_the_rendered_values():
    config = json.loads(kubectl("get", "configmap", "cilium-config", "-n", "kube-system", "-o", "json"))["data"]
    bpf = cni.values()["bpf"]
    assert config["kube-proxy-replacement"] == "true"
    assert config["enable-hubble"] == "false"
    assert config["enable-l7-proxy"] == "false"
    assert config["bpf-lb-external-clusterip"] == "false"
    assert config["bpf-ct-global-tcp-max"] == str(bpf["ctTcpMax"])
