"""
Metrics profile checks - adapted from ../tests/test_metrics.py. johnb00 was
deployed with grafana-deploy left at its default 'false', so this always
skips (enabled_profiles.metrics is always False) - kept for parity/future
use if that's ever flipped on for this env.
"""

import requests
import pytest

from conftest import app_url


@pytest.fixture(autouse=True)
def _skip_if_metrics_disabled(enabled_profiles):
    if not enabled_profiles.get("metrics"):
        pytest.skip("'metrics' profile is not deployed on johnb00")


def test_grafana_datasources_provisioned():
    response = requests.get(app_url("grafana", "/api/datasources"), timeout=10)
    assert response.status_code == 200
    names = {ds["name"] for ds in response.json()}
    assert names == {"Prometheus", "Tempo"}


def test_prometheus_scrape_targets_healthy():
    response = requests.get(
        app_url("grafana", "/api/datasources/proxy/uid/prometheus/api/v1/targets"),
        timeout=10,
    )
    assert response.status_code == 200
    targets = response.json()["data"]["activeTargets"]
    assert targets, "no active Prometheus scrape targets found"
    unhealthy = [t for t in targets if t["health"] != "up"]
    assert not unhealthy, f"unhealthy scrape targets: {unhealthy}"

    jobs = {t["labels"].get("job") for t in targets}
    assert {"prometheus", "tempo"} <= jobs
