"""monitoring-logging (profile metrics): what values.yaml overrides in it."""

import json

import pytest

from conftest import kubectl

from podiumd_minikube.paths import MONITORING_NAMESPACE


def test_alloy_keeps_the_charts_node_name(enabled_profiles):
    """values.yaml's alloy.extraEnv replaces the chart's list; its NODE_NAME must stay, or no log reaches Loki."""
    if not enabled_profiles.get("metrics"):
        pytest.skip("'metrics' profile is not deployed")
    daemonset = json.loads(
        kubectl("get", "daemonset", "monitoring-logging-alloy", "-n", MONITORING_NAMESPACE, "-o", "json")
    )
    [alloy] = [c for c in daemonset["spec"]["template"]["spec"]["containers"] if c["name"] == "alloy"]
    env = {e["name"]: e for e in alloy.get("env", [])}
    assert env["NODE_NAME"]["valueFrom"]["fieldRef"]["fieldPath"] == "spec.nodeName"
    assert env["GOGC"]["value"] == "50"
