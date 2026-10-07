"""lib.dns: the CoreDNS zone for the ingress hosts."""

from lib import dns

COREFILE = ".:53 {\n    reload\n}\n"


def test_corefile_gets_one_local_zone_and_replaces_it_on_update():
    first = dns.corefile_with_hosts(COREFILE, "10.0.0.1", ["zac.local", "pabc.local"], [])
    assert first.startswith(COREFILE)
    assert "zac.local:53 pabc.local:53 {" in first
    assert "local:53 {" not in first.replace("zac.local:53", "").replace("pabc.local:53", "")
    assert "        10.0.0.1 zac.local pabc.local" in first
    second = dns.corefile_with_hosts(first, "10.0.0.2", ["zac.local"], [])
    assert second.count(dns.BEGIN) == 1
    assert "10.0.0.1" not in second
    assert dns.corefile_with_hosts(second, "10.0.0.2", ["zac.local"], []) == second


def test_search_domain_names_are_zones_without_an_answer():
    resolv = (
        "search podiumd-minikube.svc.cluster.local svc.cluster.local cluster.local info.local\nnameserver 10.96.0.10\n"
    )
    assert dns.outside_search_domains(resolv) == ["info.local"]
    corefile = dns.corefile_with_hosts(COREFILE, "10.0.0.1", ["zac.local"], ["info.local"])
    assert "zac.local:53 {" in corefile
    assert "zac.local.info.local:53 {\n    template ANY ANY {\n        rcode NXDOMAIN" in corefile
    assert "        10.0.0.1 zac.local\n" in corefile
