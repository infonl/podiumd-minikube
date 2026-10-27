"""Cilium, the node's CNI and service proxy, as on ExternalsPodiumD's AKS clusters.

minikube refuses to start containerd without a CNI, so the rendered chart is
handed to `minikube start --cni=<file>`; minikube rereads that file on every
start, so it lives at a fixed path.
"""

from typing import Any

import yaml

from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube.paths import CHART_DIR

CILIUM_REPO = "https://helm.cilium.io"
CILIUM_VERSION = "1.19.8"
MANIFEST = CHART_DIR / ".cni" / "cilium.yaml"
# Pulled once: every start renders the manifest, and a laptop start may have no network.
CHART = MANIFEST.parent / f"cilium-{CILIUM_VERSION}.tgz"
POD_CIDR = "10.244.0.0/16"
# Without kube-proxy, kubeadm leaves it out and Cilium serves every Service.
START_ARGS = [
    "--extra-config=kubeadm.skip-phases=addon/kube-proxy",
    f"--extra-config=kubeadm.pod-network-cidr={POD_CIDR}",
]
# Laptop budget: limits give the agent and operator a GOMEMLIMIT (limit_runtimes).
AGENT_RESOURCES = {"requests": {"cpu": "50m", "memory": "192Mi"}, "limits": {"memory": "512Mi"}}
OPERATOR_RESOURCES = {"requests": {"cpu": "10m", "memory": "64Mi"}, "limits": {"memory": "192Mi"}}


def values() -> dict[str, Any]:
    """ExternalsPodiumD's cilium-config (t-dimp, o-dimp) without its Azure parts."""
    return {
        "kubeProxyReplacement": True,
        "k8sServiceHost": "control-plane.minikube.internal",
        "k8sServicePort": 8443,
        "routingMode": "native",
        "ipv4NativeRoutingCIDR": POD_CIDR,
        "endpointRoutes": {"enabled": True},
        # AKS: Azure CNI hands out pod addresses and its overlay masquerades.
        "ipam": {"mode": "kubernetes"},
        "enableIPv4Masquerade": True,
        "l7Proxy": False,
        "envoy": {"enabled": False},
        "hubble": {"enabled": False},
        "socketLB": {"enabled": False},
        "bpf": {
            "lbExternalClusterIP": False,
            "hostLegacyRouting": True,
            # Laptop budget: by default the maps are sized from the host's memory, not the
            # node's (420 MiB of preallocated kernel memory on a 93 GiB host); fixed sizes win.
            "ctTcpMax": 65536,
            "ctAnyMax": 32768,
            "natMax": 65536,
            "neighMax": 65536,
            "policyMapMax": 16384,
            "lbMapMax": 16384,
            "authMapMax": 16384,
            "policyStatsMapMax": 16384,
        },
        # Not in the chart; the dynamic ratio otherwise sizes it at 440754 entries (38 MiB).
        "extraConfig": {"bpf-sock-rev-map-max": "16384"},
        # A cilium-config change alone does not restart the agent; the checksum annotation does.
        "rollOutCiliumPods": True,
        # By tag: an image minikube loads from the host's cache does not match a registry digest.
        "image": {"useDigest": False},
        "operator": {
            "replicas": 1,
            "resources": OPERATOR_RESOURCES,
            "rollOutPods": True,
            "image": {"useDigest": False},
        },
        "identityAllocationMode": "crd",
        "policyEnforcementMode": "default",
        "resources": AGENT_RESOURCES,
        # As the control plane (provision.CONTROL_PLANE_ENV): collect at 1.5x the live heap.
        "extraEnv": [{"name": "GOGC", "value": "50"}],
    }


def render() -> str:
    """The Cilium chart rendered with values(), CAPACITY_ENV and GOMEMLIMIT added."""
    if not CHART.is_file():
        CHART.parent.mkdir(exist_ok=True)
        process.fetch(
            ["helm", "pull", "cilium", "--repo", CILIUM_REPO, "--version", CILIUM_VERSION, "-d", str(CHART.parent)]
        )
    text = process.output(
        ["helm", "template", "cilium", str(CHART), "-n", "kube-system", "-f", "-"], stdin=yaml.safe_dump(values())
    )
    docs = [doc for doc in yaml.safe_load_all(text) if doc]
    manifests.limit_runtimes(docs)
    return yaml.safe_dump_all(docs, sort_keys=False)


def write_manifest() -> str:
    """Writes render() to MANIFEST; returns its path for `minikube start --cni`."""
    MANIFEST.parent.mkdir(exist_ok=True)
    MANIFEST.write_text(render(), encoding="utf-8")
    return str(MANIFEST)


def apply() -> None:
    """Applies MANIFEST, so a running node gets changed values; refuses a node that still runs kindnet."""
    if kube.exists("daemonset/kindnet", "kube-system"):
        msg = "the cluster runs kindnet, not Cilium: run `podiumd-minikube teardown-cluster`, then `provision-cluster`"
        raise process.UserError(msg)
    kube.kubectl("apply", "-f", str(MANIFEST))
    kube.kubectl("rollout", "status", "-n", "kube-system", "daemonset/cilium", "--timeout=300s")
