"""
Metrics profile checks - confirms Grafana's own provisioned datasources and
Prometheus's own scrape targets are actually healthy, not just that the
pods are Running. This is what caught the zac-admin Service / Tempo OTLP
bind-address fixes actually working end to end.
"""

import requests
import pytest

from conftest import host_url, host_headers


@pytest.fixture(autouse=True)
def _skip_if_metrics_disabled(enabled_profiles):
    if not enabled_profiles.get("metrics"):
        pytest.skip("'metrics' profile is not deployed")


def test_grafana_datasources_provisioned(traefik_ip):
    response = requests.get(
        host_url(traefik_ip, "/api/datasources"),
        headers=host_headers("grafana.local"),
        timeout=10,
    )
    assert response.status_code == 200
    names = {ds["name"] for ds in response.json()}
    assert names == {"Prometheus", "Tempo"}


def test_prometheus_scrape_targets_healthy(traefik_ip):
    response = requests.get(
        host_url(traefik_ip, "/api/datasources/proxy/uid/prometheus/api/v1/targets"),
        headers=host_headers("grafana.local"),
        timeout=10,
    )
    assert response.status_code == 200
    targets = response.json()["data"]["activeTargets"]
    assert targets, "no active Prometheus scrape targets found"
    unhealthy = [t for t in targets if t["health"] != "up"]
    assert not unhealthy, f"unhealthy scrape targets: {unhealthy}"

    jobs = {t["labels"].get("job") for t in targets}
    assert {"prometheus", "tempo"} <= jobs
