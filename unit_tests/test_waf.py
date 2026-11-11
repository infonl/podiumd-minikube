"""podiumd_minikube.waf: the WAF's nginx config, ModSecurity settings and objects."""

import pytest

from conftest import FakeRun

from podiumd_minikube import gateway
from podiumd_minikube import waf
from podiumd_minikube.paths import EDGE_NAMESPACE
from podiumd_minikube.paths import EDGE_SERVICE
from podiumd_minikube.process import UserError


def test_config_keeps_ngfs_limits_and_proxies_both_listeners_to_ngf():
    text = waf.config()
    assert "client_max_body_size 0;" in text
    assert "proxy_buffers 8 128k;" in text
    assert "proxy_busy_buffers_size 256k;" in text
    assert f"proxy_pass http://{gateway.NGF_SERVICE}.{EDGE_NAMESPACE}.svc.cluster.local:80;" in text
    assert f"proxy_pass https://{gateway.NGF_SERVICE}.{EDGE_NAMESPACE}.svc.cluster.local:443;" in text
    assert "proxy_ssl_name $host;" in text
    assert "return 301" not in text


def test_config_removes_client_certificate_headers_a_client_sent():
    text = waf.config()
    for header in waf.CLIENT_CERT_HEADERS:
        assert text.count(f'proxy_set_header {header} "";') == 2


def test_modsecurity_inspects_bodies_up_to_the_images_limit_when_ngf_has_none():
    env = waf.modsecurity_env()
    assert env["MODSEC_RULE_ENGINE"] == "DetectionOnly"
    assert env["MODSEC_REQ_BODY_LIMIT"] == env["MODSEC_REQ_BODY_NOFILES_LIMIT"] == str(waf.IMAGE_BODY_LIMIT)
    assert env["MODSEC_AUDIT_LOG_PARTS"] == "AHZ"
    assert "PATCH" in env["ALLOWED_METHODS"].split()


@pytest.mark.parametrize(("size", "found"), [("0", 0), ("128k", 131072), ("600m", 629145600)])
def test_size_bytes(size: str, found: int):
    assert waf.size_bytes(size) == found


def test_size_bytes_refuses_another_unit():
    with pytest.raises(UserError, match="no nginx size"):
        waf.size_bytes("1t")


def test_the_waf_holds_the_edge_ip_without_the_labels_gateway_prunes():
    configmap, deployment, service = waf.rendered()
    assert service["metadata"]["name"] == EDGE_SERVICE
    assert service["spec"]["clusterIP"] == gateway.EDGE_CLUSTER_IP
    assert service["spec"]["type"] == "LoadBalancer"
    for doc in (configmap, deployment, service):
        assert not set(gateway.LABELS.items()) <= set(doc["metadata"]["labels"].items())
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["runAsUser"] == waf.IMAGE_UID


def test_apply_waits_for_the_rollout(fake_run: FakeRun):
    waf.apply()
    assert fake_run.ran("kubectl", "rollout", "status", f"deployment/{EDGE_SERVICE}")
