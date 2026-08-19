"""
monitoringLogging profile checks - adapted from
../tests/test_monitoring_logging.py. johnb00 has no monitoring-logging
dependency deployed - always skips. Kept for parity/future use.
"""

import requests
import pytest

from conftest import app_url, NAMESPACE


@pytest.fixture(autouse=True)
def _skip_if_monitoring_logging_disabled(enabled_profiles):
    if not enabled_profiles.get("monitoringLogging"):
        pytest.skip("monitoringLogging.enabled is not set on johnb00")


def _grafana_get(path, **kwargs):
    return requests.get(app_url("grafana", path), timeout=10, **kwargs)


def test_grafana_datasources_provisioned():
    response = _grafana_get("/api/datasources")
    assert response.status_code == 200
    names = {ds["name"] for ds in response.json()}
    assert names == {"Prometheus", "loki", "Tempo"}


def test_prometheus_scrape_targets_healthy():
    response = _grafana_get("/api/datasources/proxy/uid/prometheus/api/v1/targets")
    assert response.status_code == 200
    targets = response.json()["data"]["activeTargets"]
    assert targets, "no active Prometheus scrape targets found"
    unhealthy = [t for t in targets if t["health"] != "up"]
    assert not unhealthy, f"unhealthy scrape targets: {unhealthy}"

    jobs = {t["labels"].get("job") for t in targets}
    assert {"zac-admin", "tempo"} <= jobs


def test_loki_has_pod_logs():
    response = _grafana_get(
        "/api/datasources/proxy/uid/loki/loki/api/v1/query_range",
        params={"query": f'{{namespace="{NAMESPACE}"}}', "limit": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    streams = body["data"]["result"]
    assert streams, "Loki returned no log streams for this namespace - Alloy may not be forwarding logs"
