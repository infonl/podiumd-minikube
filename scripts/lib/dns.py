"""In-cluster resolution of the *.local ingress hosts to Traefik.

The reference environments reach their public hosts through public DNS;
minikube has none, so CoreDNS gets a server block for exactly the chart hosts,
answering them with Traefik's ClusterIP. Its zones are the host names
themselves: a zone `local` would also capture cluster.local and break all
in-cluster DNS. A separate server block leaves minikube's own configuration
untouched; CoreDNS' reload plugin picks the change up.

Pods inherit the host's search domains (e.g. info.local). With ndots:5, musl
(Alpine) tries keycloak.local.info.local first and stops at a "no data"
answer from the host's DNS, so the block also answers those names, with
NXDOMAIN, and musl moves on to keycloak.local itself.
"""

import json

from lib import kube
from lib.paths import NAMESPACE
from lib.paths import TRAEFIK_NAMESPACE
from lib.paths import TRAEFIK_SERVICE

BEGIN = "# BEGIN podiumd-minikube ingress hosts"
END = "# END podiumd-minikube ingress hosts"


def outside_search_domains(resolv_conf: str) -> list[str]:
    """Search domains in resolv_conf that are not the cluster's own."""
    for line in resolv_conf.splitlines():
        if line.startswith("search "):
            return [domain for domain in line.split()[1:] if not domain.endswith("cluster.local")]
    return []


def corefile_with_hosts(corefile: str, ip: str, hosts: list[str], search_domains: list[str]) -> str:
    """corefile with this project's server block (replacing an earlier one) answering hosts with ip.

    host.<search domain> names get their own block answering NXDOMAIN.
    """
    lines = corefile.rstrip("\n").splitlines()
    if BEGIN in lines:
        lines = lines[: lines.index(BEGIN)] + lines[lines.index(END) + 1 :]
    block = [
        f"{' '.join(f'{host}:53' for host in hosts)} {{",
        "    errors",
        "    hosts {",
        f"        {ip} {' '.join(hosts)}",
        "    }",
        "}",
    ]
    expanded = [f"{host}.{domain}:53" for domain in search_domains for host in hosts]
    if expanded:
        block += [f"{' '.join(expanded)} {{", "    template ANY ANY {", "        rcode NXDOMAIN", "    }", "}"]
    return "\n".join([*lines, BEGIN, *block, END]) + "\n"


def apply_hosts(hosts: list[str]) -> None:
    """Points hosts at Traefik's ClusterIP inside the cluster."""
    ip = kube.kubectl("get", "svc", TRAEFIK_SERVICE, "-n", TRAEFIK_NAMESPACE, "-o", "jsonpath={.spec.clusterIP}")
    corefile = kube.kubectl("get", "configmap", "coredns", "-n", "kube-system", "-o", "jsonpath={.data.Corefile}")
    # Any running pod has the search domains the node hands out.
    resolv_conf = kube.kubectl("exec", "-n", NAMESPACE, "deploy/postgres", "--", "cat", "/etc/resolv.conf")
    updated = corefile_with_hosts(corefile, ip, hosts, outside_search_domains(resolv_conf))
    if updated == corefile:
        print(f"CoreDNS already resolves {len(hosts)} ingress host(s) to Traefik ({ip}).")
        return
    print(f"Pointing {len(hosts)} ingress host(s) at Traefik ({ip}) in CoreDNS...")
    patch = json.dumps({"data": {"Corefile": updated}})
    kube.kubectl_shown("patch", "configmap", "coredns", "-n", "kube-system", "--type", "merge", "-p", patch)
