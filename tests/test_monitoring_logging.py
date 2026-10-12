"""The metrics profile (monitoring-logging): the scrape job values.yaml adds to its Prometheus."""

import pytest
import requests

from conftest import host_url


@pytest.fixture(autouse=True)
def _skip_if_metrics_disabled(enabled_profiles):
    if not enabled_profiles.get("metrics"):
        pytest.skip("'metrics' profile is not deployed")


def test_added_scrape_jobs_are_up(edge_ip):
    response = requests.get(
        host_url("grafana.local", "/api/datasources/proxy/uid/prometheus/api/v1/targets"), timeout=10
    )
    assert response.status_code == 200
    targets = [t for t in response.json()["data"]["activeTargets"] if t["labels"].get("job") == "zac-admin"]
    assert targets, "scrape job zac-admin missing"
    down = [t["scrapeUrl"] for t in targets if t["health"] != "up"]
    assert not down, f"scrape targets down: {down}"
