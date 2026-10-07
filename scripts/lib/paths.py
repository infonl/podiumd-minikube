"""Fixed names and paths of this chart and its cluster."""

from pathlib import Path

CHART_DIR = Path(__file__).resolve().parents[2]
VALUES_YAML = CHART_DIR / "values.yaml"
CHART_YAML = CHART_DIR / "Chart.yaml"
CHART_LOCK = CHART_DIR / "Chart.lock"
CHARTS_DIR = CHART_DIR / "charts"
VERSIONS_FILE = CHART_DIR / ".podiumd-versions.yaml"
VENDOR_DIR = CHART_DIR / "vendor" / "dimpact-zaakafhandelcomponent"

RELEASE_NAME = "podiumd-minikube"
NAMESPACE = "podiumd-minikube"
PROFILE = "minikube"
# NGINX Gateway Fabric's data-plane Service for Gateway public-gateway (lib.gateway).
EDGE_NAMESPACE = "ingress-basic"
EDGE_SERVICE = "public-gateway-nginx"
