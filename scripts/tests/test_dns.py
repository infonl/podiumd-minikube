"""lib.dns: the CoreDNS zone for the ingress hosts."""

from lib import dns

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
