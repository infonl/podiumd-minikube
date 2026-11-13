"""podiumd_minikube.waf: the WAF's nginx config, ModSecurity settings and objects."""

import pytest

from conftest import FakeRun

from podiumd_minikube import gateway
from podiumd_minikube import waf
from podiumd_minikube.paths import EDGE_NAMESPACE
from podiumd_minikube.paths import EDGE_SERVICE
from podiumd_minikube.process import UserError

INWAY = {
    "kind": "Ingress",
    "spec": {
        "rules": [{"host": "inway.local", "http": {"paths": [{"backend": {"service": {"name": waf.MTLS_SERVICE}}}]}}]
    },
}
ZAC = {
    "kind": "Ingress",
    "spec": {"rules": [{"host": "zac.local", "http": {"paths": [{"backend": {"service": {"name": "zac"}}}]}}]},
}


def test_config_keeps_ngfs_limits_and_proxies_both_listeners_to_ngf():
    text = waf.config([])
    assert "client_max_body_size 0;" in text
    assert "proxy_buffers 8 128k;" in text
    assert "proxy_busy_buffers_size 256k;" in text
    assert f"proxy_pass http://{gateway.NGF_SERVICE}.{EDGE_NAMESPACE}.svc.cluster.local:80;" in text
    assert f"proxy_pass https://{gateway.NGF_SERVICE}.{EDGE_NAMESPACE}.svc.cluster.local:443;" in text
    assert "proxy_ssl_name $host;" in text
    assert "return 301" not in text


def test_config_removes_client_certificate_headers_a_client_sent():
    text = waf.config(["inway.local"])
    for header, variable in waf.CLIENT_CERT_HEADERS.items():
        assert text.count(f'proxy_set_header {header} "";') == 2
        assert text.count(f"proxy_set_header {header} {variable};") == 1


def test_only_the_wafs_own_responses_get_the_routes_response_headers():
    text = waf.config([])
    for number, (name, value) in enumerate(gateway.RESPONSE_HEADERS.items()):
        assert f'map $upstream_status $waf_own_header_{number} {{ "" {value!r}; default ""; }}' in text
        assert f"add_header {name} $waf_own_header_{number} always;" in text


def test_mtls_hosts_are_those_of_ingresses_to_the_inway():
    assert waf.mtls_hosts([INWAY, ZAC]) == ["inway.local"]
    assert waf.mtls_hosts([ZAC]) == []


def test_an_mtls_host_asks_for_any_client_certificate():
    server = waf.config(["inway.local"]).rsplit("\n\n", maxsplit=1)[-1]
    assert "server_name inway.local;" in server
    assert "ssl_verify_client optional_no_ca;" in server
    assert "ssl_client_certificate" not in server


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
    configmap, deployment, service = waf.rendered([])
    assert service["metadata"]["name"] == EDGE_SERVICE
    assert service["spec"]["clusterIP"] == gateway.EDGE_CLUSTER_IP
    assert service["spec"]["type"] == "LoadBalancer"
    for doc in (configmap, deployment, service):
        assert not set(gateway.LABELS.items()) <= set(doc["metadata"]["labels"].items())
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["securityContext"]["runAsUser"] == waf.IMAGE_UID


def test_the_memory_limit_fits_a_500_mb_upload_in_each_worker():
    container = waf.rendered([])[1]["spec"]["template"]["spec"]["containers"][0]
    limit = waf.size_bytes(container["resources"]["limits"]["memory"].removesuffix("i").lower())
    workers = int(next(env["value"] for env in container["env"] if env["name"] == "NGINX_WORKER_PROCESSES"))
    assert limit > workers * 500 * 1024 * 1024


def test_apply_waits_for_the_rollout(fake_run: FakeRun):
    waf.apply([ZAC])
    assert fake_run.ran("kubectl", "rollout", "status", f"deployment/{EDGE_SERVICE}")


def test_tls_1_2_accepts_only_ecdhe_with_aead_suites():
    text = waf.rendered([])[0]["data"]["default.conf.template"]
    ciphers = text.split("ssl_ciphers ", 1)[1].split(";", 1)[0].split(":")
    assert ciphers
    assert all(c.startswith("ECDHE-") and ("GCM" in c or "CHACHA20" in c) for c in ciphers)
    assert "ssl_prefer_server_ciphers on;" in text
