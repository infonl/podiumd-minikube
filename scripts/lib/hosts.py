"""The *.local hostnames of this chart's Ingresses, for /etc/hosts."""

HOSTNAMES = [
    "zac.local", "keycloak.local", "openzaak.local", "openklant.local", "pabc.local", "solr.local",
    "objecten.local", "objecttypen.local", "opennotificaties.local", "openarchiefbeheer-web.local",
    "openarchiefbeheer-ui.local", "openformulieren-nginx.local", "openformulieren-web.local",
    "grafana.local", "mailpit.local", "ita.local", "kiss.local",
]  # fmt: skip

MARKER = "# podiumd-minikube"


def hosts_line(ip: str) -> str:
    """The /etc/hosts line mapping every hostname to ip, with this project's marker."""
    return f"{ip} {' '.join(HOSTNAMES)}  {MARKER}"
