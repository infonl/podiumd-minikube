"""The metrics profile's raw templates: Grafana's provisioned datasource and Prometheus' ZAC scrape target."""

import pytest
import requests

from conftest import host_url


@pytest.fixture(autouse=True)
def _skip_if_metrics_disabled(enabled_profiles):
    if not enabled_profiles.get("metrics"):
        pytest.skip("'metrics' profile is not deployed")


def test_grafana_datasources_provisioned(edge_ip):
    response = requests.get(
        host_url("grafana.local", "/api/datasources"),
        timeout=10,
    )
    assert response.status_code == 200
    names = {ds["name"] for ds in response.json()}
    assert names == {"Prometheus"}


def test_prometheus_scrape_targets_healthy(edge_ip):
    response = requests.get(
        host_url("grafana.local", "/api/datasources/proxy/uid/prometheus/api/v1/targets"),
        timeout=10,
    )
    assert response.status_code == 200
    targets = response.json()["data"]["activeTargets"]
    assert targets, "no active Prometheus scrape targets found"
    unhealthy = [t for t in targets if t["health"] != "up"]
    assert not unhealthy, f"unhealthy scrape targets: {unhealthy}"

    assert {t["labels"].get("job") for t in targets} == {"prometheus"}
