"""The edge's routes (lib.gateway): every Ingress host has an HTTPRoute that NGINX Gateway Fabric accepted."""

import json

from conftest import EDGE_NAMESPACE
from conftest import NAMESPACE
from conftest import kubectl


def _items(*args):
    return json.loads(kubectl("get", *args, "-o", "json"))["items"]


def _ingress_hosts():
    return {
        rule["host"] for ingress in _items("ingress", "-n", NAMESPACE) for rule in ingress["spec"].get("rules") or []
    }


def _route_conditions():
    """Parent conditions per route hostname."""
    return {
        host: [c for parent in route.get("status", {}).get("parents") or [] for c in parent["conditions"]]
        for route in _items("httproute", "-n", EDGE_NAMESPACE)
        for host in route["spec"].get("hostnames") or []
    }


def test_every_ingress_host_has_a_route(edge_ip):
    missing = _ingress_hosts() - set(_route_conditions())
    assert not missing, f"no HTTPRoute in {EDGE_NAMESPACE} for {sorted(missing)}: redeploy (lib.gateway.apply)"


def test_every_route_is_accepted_with_resolved_backends(edge_ip):
    refused = {
        host: [f"{c['type']}={c['status']} ({c.get('reason')})" for c in conditions if c["status"] != "True"]
        for host, conditions in _route_conditions().items()
        if not conditions or any(c["status"] != "True" for c in conditions)
    }
    assert not refused, f"routes not accepted or with unresolved backends: {refused}"
