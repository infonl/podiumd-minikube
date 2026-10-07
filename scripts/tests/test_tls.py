"""lib.tls: the Traefik default certificate."""

import yaml

from lib import manifests
from lib import tls


def test_certificate_covers_the_hosts_and_is_traefiks_default():
    certificate, store = yaml.safe_load_all(tls.certificate_manifests(["zac.local", "pabc.local"]))
    assert certificate["spec"]["dnsNames"] == ["zac.local", "pabc.local"]
    assert certificate["spec"]["issuerRef"]["name"] == "podiumd-local-ca"
    assert store["metadata"]["name"] == "default"
    assert store["spec"]["defaultCertificate"]["secretName"] == certificate["spec"]["secretName"]


def test_ingress_hosts_are_sorted_and_unique():
    docs: list[manifests.Doc] = [
        {"kind": "Ingress", "spec": {"rules": [{"host": "zac.local"}, {"host": "a.local"}]}},
        {"kind": "Ingress", "spec": {"rules": [{"host": "zac.local"}, {"http": {}}]}},
        {"kind": "Service", "spec": {"rules": [{"host": "not.local"}]}},
    ]
    assert manifests.ingress_hosts(docs) == ["a.local", "zac.local"]
