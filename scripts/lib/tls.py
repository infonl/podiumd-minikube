"""HTTPS on the edge: one certificate for every ingress host.

As the reference environments: one cert-manager Certificate with all hosts
(ExternalsPodiumD's global-tls, podiumd-infra's name podiumd-tls), served by
the Gateway's https listener (lib.gateway). HTTP keeps working next to HTTPS,
as in ExternalsPodiumD.
"""

from typing import Any

from lib.pki import ISSUER

CERTIFICATE = "podiumd-tls"


def certificate(namespace: str, hosts: list[str]) -> dict[str, Any]:
    """The Certificate (Secret podiumd-tls) for hosts in namespace, issued by the local CA."""
    return {
        "apiVersion": "cert-manager.io/v1",
        "kind": "Certificate",
        "metadata": {"name": CERTIFICATE, "namespace": namespace},
        "spec": {
            "secretName": CERTIFICATE,
            "issuerRef": {"name": ISSUER, "kind": "ClusterIssuer", "group": "cert-manager.io"},
            "dnsNames": hosts,
        },
    }
