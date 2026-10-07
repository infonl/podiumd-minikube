"""In-cluster resolution of the *.local ingress hosts to Traefik.

The reference environments reach their public hosts through public DNS;
minikube has none, so CoreDNS gets a server block for exactly the chart hosts,
answering them with Traefik's ClusterIP. Its zones are the host names
themselves: a zone `local` would also capture cluster.local and break all
in-cluster DNS. A separate server block leaves minikube's own configuration
untouched; CoreDNS' reload plugin picks the change up.
"""

import json

from lib import kube
from lib.paths import TRAEFIK_NAMESPACE
from lib.paths import TRAEFIK_SERVICE

BEGIN = "# BEGIN podiumd-minikube ingress hosts"
END = "# END podiumd-minikube ingress hosts"


def corefile_with_hosts(corefile: str, ip: str, hosts: list[str]) -> str:
    """corefile with this project's server block (replacing an earlier one) answering hosts with ip."""
    lines = corefile.rstrip("\n").splitlines()
    if BEGIN in lines:
        lines = lines[: lines.index(BEGIN)] + lines[lines.index(END) + 1 :]
    zones = " ".join(f"{host}:53" for host in hosts)
    block = [BEGIN, f"{zones} {{", "    errors", "    hosts {", f"        {ip} {' '.join(hosts)}", "    }", "}", END]
    return "\n".join([*lines, *block]) + "\n"


def apply_hosts(hosts: list[str]) -> None:
    """Points hosts at Traefik's ClusterIP inside the cluster."""
    ip = kube.kubectl("get", "svc", TRAEFIK_SERVICE, "-n", TRAEFIK_NAMESPACE, "-o", "jsonpath={.spec.clusterIP}")
    corefile = kube.kubectl("get", "configmap", "coredns", "-n", "kube-system", "-o", "jsonpath={.data.Corefile}")
    updated = corefile_with_hosts(corefile, ip, hosts)
    if updated == corefile:
        print(f"CoreDNS already resolves {len(hosts)} ingress host(s) to Traefik ({ip}).")
        return
    print(f"Pointing {len(hosts)} ingress host(s) at Traefik ({ip}) in CoreDNS...")
    patch = json.dumps({"data": {"Corefile": updated}})
    kube.kubectl_shown("patch", "configmap", "coredns", "-n", "kube-system", "--type", "merge", "-p", patch)
