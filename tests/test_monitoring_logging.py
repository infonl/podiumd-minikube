"""
monitoringLogging profile checks - the monitoring-logging dependency's own
Grafana/Prometheus/Loki/Tempo stack, the heavier alternative to
templates/metrics/'s raw templates that backs the same "metrics" profile
when monitoringLogging.enabled=true (see values.yaml's own monitoringLogging
comment). Mirrors test_metrics.py's approach of hitting Grafana's own
datasource proxy endpoints to confirm things are actually healthy, not just
that the pods are Running - but for three datasources instead of two, and
with an added check for Alloy's own log forwarding into Loki, which the
raw-templates implementation has no equivalent of at all.
"""

import requests
import pytest

from conftest import host_url, host_headers


@pytest.fixture(autouse=True)
def _skip_if_monitoring_logging_disabled(enabled_profiles):
    if not enabled_profiles.get("monitoringLogging"):
        pytest.skip(
            "monitoringLogging.enabled is not set - templates/metrics/'s raw "
            "templates are backing the 'metrics' profile instead (see test_metrics.py)"
        )


def _grafana_get(traefik_ip, path, **kwargs):
    return requests.get(
        host_url(traefik_ip, path),
        headers=host_headers("grafana.local"),
        timeout=10,
        **kwargs,
    )


def test_grafana_datasources_provisioned(traefik_ip):
    response = _grafana_get(traefik_ip, "/api/datasources")
    assert response.status_code == 200
    names = {ds["name"] for ds in response.json()}
    assert names == {"Prometheus", "loki", "Tempo"}


def test_prometheus_scrape_targets_healthy(traefik_ip):
    response = _grafana_get(traefik_ip, "/api/datasources/proxy/uid/prometheus/api/v1/targets")
    assert response.status_code == 200
    targets = response.json()["data"]["activeTargets"]
    assert targets, "no active Prometheus scrape targets found"
    unhealthy = [t for t in targets if t["health"] != "up"]
    assert not unhealthy, f"unhealthy scrape targets: {unhealthy}"

    # The two explicit additionalScrapeConfigs (values.yaml's own
    # kube-prometheus-stack.prometheus.prometheusSpec block) - the ones this
    # chart's default ServiceMonitor discovery wouldn't pick up on its own.
    jobs = {t["labels"].get("job") for t in targets}
    assert {"zac-admin", "tempo"} <= jobs


def test_loki_has_pod_logs(traefik_ip):
    """
    Confirms Alloy is actually discovering and forwarding this namespace's
    pod logs into Loki - not just that Loki itself answers queries, which a
    "success" envelope with an empty result set would also satisfy while
    hiding a completely broken Alloy pipeline (confirmed live during
    development: this exact gap is what a plain "endpoint responds" check
    would have missed).
    """
    response = _grafana_get(
        traefik_ip,
        "/api/datasources/proxy/uid/loki/loki/api/v1/query_range",
        params={"query": '{namespace="podiumd-minikube"}', "limit": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    streams = body["data"]["result"]
    assert streams, "Loki returned no log streams for this namespace - Alloy may not be forwarding logs"
