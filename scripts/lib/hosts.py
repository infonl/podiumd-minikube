"""The *.local hostnames this chart's Ingresses can have."""

from lib import chart
from lib import manifests

MARKER = "# podiumd-minikube"


def chart_hosts() -> list[str]:
    """Ingress hosts of a render with every profile on (for /etc/hosts and the TLS certificate)."""
    render = manifests.fix_up(manifests.helm_template(*chart.everything_args()), objecten_merged=False, zac_pkce=False)
    return manifests.ingress_hosts(render.docs)


def hosts_line(ip: str, hostnames: list[str]) -> str:
    """The /etc/hosts line mapping hostnames to ip, with this project's marker."""
    return f"{ip} {' '.join(hostnames)}  {MARKER}"
