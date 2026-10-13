"""monitoring-logging's CustomResourceDefinitions.

Their CRDs sit in subcharts `helm template --include-crds` does not reach, so without these
every Prometheus/ServiceMonitor/PodMonitor in the render fails to apply.
"""

import tarfile

import yaml

from podiumd_minikube import chart
from podiumd_minikube import kube
from podiumd_minikube.manifests import Doc
from podiumd_minikube.manifests import name_of
from podiumd_minikube.manifests import section
from podiumd_minikube.process import UserError

PROMETHEUS_OPERATOR_VERSION = "operator.prometheus.io/version"


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
    crds = crd_documents(str(tgz))
    if not crds:
        msg = f"no CustomResourceDefinition in {tgz}: check the monitoring-logging version"
        raise UserError(msg)
    kube.kubectl_shown("apply", "--server-side", "--force-conflicts", "-f", "-", stdin=yaml.safe_dump_all(crds))
