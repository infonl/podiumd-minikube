"""The *.local hostnames this chart's Ingresses can have."""

from podiumd_minikube import chart
from podiumd_minikube import manifests

MARKER = "# podiumd-minikube"


def chart_hosts() -> list[str]:
    """Ingress hosts of a render with every profile on (for /etc/hosts and the TLS certificate).

    No fixup changes a host, so only the Ingresses are parsed.
    """
    return manifests.ingress_hosts(manifests.of_kind(manifests.helm_template(*chart.everything_args()), "Ingress"))


def hosts_line(ip: str, hostnames: list[str]) -> str:
    """Return the /etc/hosts line mapping hostnames to ip, with this project's marker."""
    return f"{ip} {' '.join(hostnames)}  {MARKER}"
