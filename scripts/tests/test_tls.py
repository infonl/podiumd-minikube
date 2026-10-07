"""lib.tls: the edge certificate."""

from lib import manifests
from lib import tls


def test_certificate_covers_the_hosts_in_the_namespace():
    certificate = tls.certificate("ingress-basic", ["zac.local", "pabc.local"])
    assert certificate["metadata"]["namespace"] == "ingress-basic"
    assert certificate["spec"]["dnsNames"] == ["zac.local", "pabc.local"]
    assert certificate["spec"]["issuerRef"]["name"] == "podiumd-local-ca"


def test_ingress_hosts_are_sorted_and_unique():
    docs: list[manifests.Doc] = [
        {"kind": "Ingress", "spec": {"rules": [{"host": "zac.local"}, {"host": "a.local"}]}},
        {"kind": "Ingress", "spec": {"rules": [{"host": "zac.local"}, {"http": {}}]}},
        {"kind": "Service", "spec": {"rules": [{"host": "not.local"}]}},
    ]
    assert manifests.ingress_hosts(docs) == ["a.local", "zac.local"]
