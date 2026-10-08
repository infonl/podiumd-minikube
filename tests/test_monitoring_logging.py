"""monitoringLogging: the scrape jobs values.yaml adds to its Prometheus (additionalScrapeConfigs)."""

import pytest
import requests

from conftest import host_url


@pytest.fixture(autouse=True)
def _skip_if_monitoring_logging_disabled(enabled_profiles):
    if not enabled_profiles.get("monitoringLogging"):
        pytest.skip(
            "monitoringLogging.enabled is off: templates/metrics/ backs the 'metrics' profile (test_metrics.py)"
        )


def test_added_scrape_jobs_are_up(edge_ip):
    response = requests.get(
        host_url("grafana.local", "/api/datasources/proxy/uid/prometheus/api/v1/targets"), timeout=10
    )
    assert response.status_code == 200
    targets = [t for t in response.json()["data"]["activeTargets"] if t["labels"].get("job") in {"zac-admin", "tempo"}]
    assert {t["labels"]["job"] for t in targets} == {"zac-admin", "tempo"}
    down = [t["scrapeUrl"] for t in targets if t["health"] != "up"]
    assert not down, f"scrape targets down: {down}"
