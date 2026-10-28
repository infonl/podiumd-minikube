"""Fixed names and paths of this chart and its cluster."""

from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[2]
VALUES_YAML = CHART_DIR / "values.yaml"
CHART_YAML = CHART_DIR / "Chart.yaml"
CHART_LOCK = CHART_DIR / "Chart.lock"
CHARTS_DIR = CHART_DIR / "charts"
VERSIONS_FILE = CHART_DIR / ".podiumd-versions.yaml"
VENDOR_DIR = CHART_DIR / "vendor" / "dimpact-zaakafhandelcomponent"

# ExternalsPodiumD's releases and namespaces (pipelines/application.yml, monitoring.yml); RELEASE_NAME is this chart's.
RELEASE_NAME = "podiumd-minikube"
PODIUMD_RELEASE = "podiumd"
MONITORING_RELEASE = "monitoring-logging"
NAMESPACE = "podiumd"
MONITORING_NAMESPACE = "monitoring"
# The namespace of the kubectl-apply deploys before the Helm releases.
OLD_NAMESPACE = "podiumd-minikube"
PROFILE = "minikube"
# NGINX Gateway Fabric's data-plane Service for Gateway public-gateway (podiumd_minikube.gateway).
EDGE_NAMESPACE = "ingress-basic"
EDGE_SERVICE = "public-gateway-nginx"
