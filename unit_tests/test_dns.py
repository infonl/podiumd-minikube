"""podiumd_minikube.dns: the CoreDNS zone for the ingress hosts."""

import json

import pytest

from conftest import FakeRun

from podiumd_minikube import dns
from podiumd_minikube.paths import EDGE_NAMESPACE
from podiumd_minikube.paths import EDGE_SERVICE

COREFILE = ".:53 {\n    reload\n}\n"


def test_corefile_gets_one_local_zone_and_replaces_it_on_update():
    first = dns.corefile_with_hosts(COREFILE, "10.0.0.1", ["zac.local", "pabc.local"])
    assert first.startswith(COREFILE)
    assert "zac.local:53 pabc.local:53 {" in first
    assert "local:53 {" not in first.replace("zac.local:53", "").replace("pabc.local:53", "")
    assert "        10.0.0.1 zac.local pabc.local" in first
    second = dns.corefile_with_hosts(first, "10.0.0.2", ["zac.local"])
    assert second.count(dns.BEGIN) == 1
    assert "10.0.0.1" not in second
    assert dns.corefile_with_hosts(second, "10.0.0.2", ["zac.local"]) == second


@pytest.mark.parametrize("current", [False, True])
def test_apply_hosts_patches_coredns_only_when_it_differs(fake_run: FakeRun, *, current: bool):
    corefile = dns.corefile_with_hosts(COREFILE, "10.0.0.1", ["zac.local"]) if current else COREFILE
    fake_run.on("kubectl", "get", "svc", EDGE_SERVICE, "-n", EDGE_NAMESPACE, stdout="10.0.0.1")
    fake_run.on("kubectl", "get", "configmap", "coredns", stdout=corefile)
    dns.apply_hosts(["zac.local"])
    patches = fake_run.ran("kubectl", "patch", "configmap", "coredns")
    if current:
        assert not patches
    else:
        assert patches[0][4:8] == ["-n", "kube-system", "--type", "merge"]
        patch = json.loads(patches[0][-1])
        assert patch == {"data": {"Corefile": dns.corefile_with_hosts(COREFILE, "10.0.0.1", ["zac.local"])}}
