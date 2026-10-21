"""CustomResourceDefinitions the render lacks or carries too old: monitoring-logging's and the Keycloak operator's.

monitoring-logging's sit in subcharts `helm template --include-crds` does not reach, so without
them every Prometheus/ServiceMonitor/PodMonitor in the render fails to apply.
"""

import re
import tarfile
import urllib.error
import urllib.request

from pathlib import Path

import yaml

from podiumd_minikube import chart
from podiumd_minikube import kube
from podiumd_minikube.manifests import Doc
from podiumd_minikube.manifests import name_of
from podiumd_minikube.manifests import section
from podiumd_minikube.paths import CHARTS_DIR
from podiumd_minikube.process import UserError

PROMETHEUS_OPERATOR_VERSION = "operator.prometheus.io/version"
# The chart's scripts/install-keycloak-operator-crds.sh: upstream's CRDs for the operator's
# Keycloak version (the adfinis subchart bundles older ones; the 4.10.0 upgrade docs require these).
KEYCLOAK_CRD_URL = (
    "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/{version}/kubernetes/{name}-v1.yml"
)
KEYCLOAK_CRD_GROUP = ".k8s.keycloak.org"
# The client CRDs exist upstream from Keycloak 26.7.0 only.
KEYCLOAK_CRDS = ("keycloaks", "keycloakrealmimports", "keycloakoidcclients", "keycloaksamlclients")
_KEYCLOAK_VERSION = re.compile(r'&keycloakImageVersion "?([0-9][0-9.]*)"?')
# Kinds no component here uses (Alertmanager is off, as in podiumd-infra; no Grafana Agent or rollout
# operator runs). The Prometheus operator skips their controllers when absent; each CRD costs the
# kube-apiserver memory (these five monitoring ones: about 45 MiB of live heap).
UNUSED_MONITORING_LOGGING_CRDS = frozenset(
    {
        "alertmanagerconfigs.monitoring.coreos.com",
        "alertmanagers.monitoring.coreos.com",
        "prometheusagents.monitoring.coreos.com",
        "scrapeconfigs.monitoring.coreos.com",
        "thanosrulers.monitoring.coreos.com",
        "grafanaagents.monitoring.grafana.com",
        "integrations.monitoring.grafana.com",
        "logsinstances.monitoring.grafana.com",
        "metricsinstances.monitoring.grafana.com",
        "podlogs.monitoring.grafana.com",
        "replicatemplates.rollout-operator.grafana.com",
        "zoneawarepoddisruptionbudgets.rollout-operator.grafana.com",
    }
)


class _CrdLoader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """SafeLoader that reads a bare `=` (an enum value in these CRDs) as a string, not YAML's value tag."""


_CrdLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:value"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _version(doc: Doc) -> tuple[int, ...]:
    annotation = str(section(section(doc, "metadata"), "annotations").get(PROMETHEUS_OPERATOR_VERSION, ""))
    return tuple(int(part) for part in annotation.split(".") if part.isdigit())


def crd_documents(tgz_path: str) -> list[Doc]:
    """The CustomResourceDefinitions in tgz_path's *.yaml members, the newest per name.

    Loki's bundled grafana-agent-operator carries an old copy of the
    Prometheus operator's CRDs; applied after kube-prometheus-stack's, it
    dropped fields such as ServiceMonitor's scrapeProtocols.
    """
    newest: dict[str, Doc] = {}
    with tarfile.open(tgz_path) as archive:
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if not member.name.endswith(".yaml"):
                continue
            stream = archive.extractfile(member)
            text = stream.read().decode() if stream else ""
            # Templates are not YAML yet; only CRD files are parsed.
            if "kind: CustomResourceDefinition" not in text.splitlines():
                continue
            loaded: list[Doc | None] = list(yaml.load_all(text, Loader=_CrdLoader))
            for doc in loaded:
                if not doc or doc.get("kind") != "CustomResourceDefinition":
                    continue
                name = name_of(doc)
                if name not in newest or _version(doc) > _version(newest[name]):
                    newest[name] = doc
    return [newest[name] for name in sorted(newest)]


def needed(docs: list[dict[str, object]]) -> bool:
    """Whether docs hold objects of the Prometheus operator's kinds (the metrics profile)."""
    return any(str(doc.get("apiVersion", "")).startswith("monitoring.coreos.com/") for doc in docs)


def apply_monitoring_logging_crds() -> None:
    """Applies the CRDs server-side (some exceed client-side apply's annotation limit)."""
    tgz = chart.tarball("monitoring-logging")
    if not tgz:
        msg = "no charts/monitoring-logging-*.tgz: run provision-cluster or deploy first (helm dependency update)"
        raise UserError(msg)
    crds = [doc for doc in crd_documents(str(tgz)) if name_of(doc) not in UNUSED_MONITORING_LOGGING_CRDS]
    if not crds:
        msg = f"no CustomResourceDefinition in {tgz}: check the monitoring-logging version"
        raise UserError(msg)
    kube.kubectl_shown("apply", "--server-side", "--force-conflicts", "-f", "-", stdin=yaml.safe_dump_all(crds))


def keycloak_version(charts_dir: Path = CHARTS_DIR) -> str | None:
    """podiumd's Keycloak image version (its keycloakImageVersion anchor); None before a fetch."""
    tgz = chart.tarball("podiumd", charts_dir)
    text = chart.member_text(tgz, "podiumd/values.yaml") if tgz else None
    found = _KEYCLOAK_VERSION.search(text or "")
    return found.group(1) if found else None


def _fetch(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # nosec B310  # noqa: S310 - KEYCLOAK_CRD_URL, https
            return str(response.read().decode())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def with_upstream_keycloak(crds: list[Doc], version: str | None) -> list[Doc]:
    """crds with the Keycloak operator's replaced by upstream's for version (unchanged without a version)."""
    if not version:
        return crds
    upstream: list[Doc] = []
    for name in KEYCLOAK_CRDS:
        text = _fetch(KEYCLOAK_CRD_URL.format(version=version, name=name + KEYCLOAK_CRD_GROUP))
        upstream += [doc for doc in yaml.load_all(text or "", Loader=_CrdLoader) if doc]
    if not upstream:
        msg = f"no Keycloak CRDs upstream for Keycloak {version}: check {KEYCLOAK_CRD_URL}"
        raise UserError(msg)
    return [doc for doc in crds if not name_of(doc).endswith(KEYCLOAK_CRD_GROUP)] + upstream
