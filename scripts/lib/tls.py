"""HTTPS on Traefik: one certificate for every ingress host, as Traefik's default.

As the reference environments: one cert-manager Certificate with all hosts
(podiumd-infra's name podiumd-tls). Traefik serves it for every websecure
route, so the subcharts' Ingresses need no tls section. HTTP keeps working
next to HTTPS, as in ExternalsPodiumD.
"""

from typing import Any

import yaml

from lib import kube
from lib.paths import TRAEFIK_NAMESPACE
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


def certificate_manifests(hosts: list[str]) -> str:
    """The Certificate for hosts and the TLSStore that makes it Traefik's default."""
    store = {
        "apiVersion": "traefik.io/v1alpha1",
        "kind": "TLSStore",
        "metadata": {"name": "default", "namespace": TRAEFIK_NAMESPACE},
        "spec": {"defaultCertificate": {"secretName": CERTIFICATE}},
    }
    return yaml.safe_dump_all([certificate(TRAEFIK_NAMESPACE, hosts), store], sort_keys=False)


def apply_certificate(hosts: list[str]) -> None:
    """Applies the Certificate and TLSStore and waits until cert-manager has issued it."""
    print(f"Applying the TLS certificate for {len(hosts)} host(s)...")
    kube.kubectl_shown("apply", "-f", "-", stdin=certificate_manifests(hosts))
    kube.kubectl_shown(
        "wait", "--for=condition=Ready", f"certificate/{CERTIFICATE}", "-n", TRAEFIK_NAMESPACE, "--timeout=120s"
    )
