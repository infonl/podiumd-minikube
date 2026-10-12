"""monitoring-logging's CustomResourceDefinitions.

`helm template` never renders a chart's crds/ directory, so without these
every Prometheus/ServiceMonitor/PodMonitor in the render fails to apply.
"""

import tarfile

import yaml

from lib import chart
from lib import kube
from lib.process import UserError


def crd_documents(tgz_path: str) -> str:
    """Every *.yaml member of tgz_path declaring a CustomResourceDefinition, in name order."""
    found: list[str] = []
    with tarfile.open(tgz_path) as archive:
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if not member.name.endswith(".yaml"):
                continue
            stream = archive.extractfile(member)
            text = stream.read().decode() if stream else ""
            if any(line == "kind: CustomResourceDefinition" for line in text.splitlines()):
                found.append(text if text.endswith("\n") else f"{text}\n")
    return "".join(found)


def apply_monitoring_logging_crds() -> None:
    """Applies the CRDs server-side (some exceed client-side apply's annotation limit)."""
    tgz = chart.tarball("monitoring-logging")
    if not tgz:
        msg = "no charts/monitoring-logging-*.tgz: run provision-cluster or deploy first (helm dependency update)"
        raise UserError(msg)
    crds = crd_documents(str(tgz))
    if not any(yaml.safe_load_all(crds)):
        msg = f"no CustomResourceDefinition in {tgz}: check the monitoring-logging version"
        raise UserError(msg)
    kube.kubectl_shown("apply", "--server-side", "-f", "-", stdin=crds)
