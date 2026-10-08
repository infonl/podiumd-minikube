"""lib.manifests: the fixups applied to helm template output."""

import json

import yaml

from lib import manifests

DIGEST = "a" * 64


def _docs(text: str) -> list[manifests.Doc]:
    return [doc for doc in yaml.safe_load_all(text) if doc]


def test_strip_image_digests_keeps_the_tag():
    text = f"image: repo/app:1.2@sha256:{DIGEST}\nimage: other:2\n"
    assert manifests.strip_image_digests(text) == "image: repo/app:1.2\nimage: other:2\n"


def test_images_are_unique_and_sorted():
    text = 'spec:\n  image: "b:1"\n  image: a:2\n  image: b:1\n'
    assert manifests.images(text) == ["a:2", "b:1"]


def test_images_skip_an_image_key_without_a_value():
    text = "properties:\n  image:\n    description: The image to run\n  image: c:3\n"
    assert manifests.images(text) == ["c:3"]


def test_disable_service_links_skips_bare_jobs():
    docs = _docs("""
kind: Deployment
spec: {template: {spec: {containers: []}}}
---
kind: CronJob
spec: {jobTemplate: {spec: {template: {spec: {}}}}}
---
kind: Job
spec: {template: {spec: {}}}
""")
    manifests.disable_service_links(docs)
    assert docs[0]["spec"]["template"]["spec"]["enableServiceLinks"] is False
    assert docs[1]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["enableServiceLinks"] is False
    assert "enableServiceLinks" not in docs[2]["spec"]["template"]["spec"]


def test_fix_up_drops_pabc_job_test_hooks_and_zac_otel_collector():
    text = """
kind: Job
metadata: {name: pabc-migrations-1}
---
kind: Pod
metadata: {name: grafana-test, annotations: {helm.sh/hook: test}}
---
kind: Deployment
metadata: {name: zac-unused-otel-collector}
---
kind: Service
metadata: {name: zac}
"""
    render = manifests.fix_up(text, objecten_merged=False, zac_pkce=False)
    assert [manifests.name_of(doc) for doc in render.docs] == ["zac"]


def _configmap(name: str, configuration: object) -> manifests.Doc:
    return {
        "kind": "ConfigMap",
        "metadata": {"name": name},
        "data": {"configuration.yaml": yaml.safe_dump(configuration)},
    }


def test_fixup_merged_objecten_removes_classic_only_settings():
    docs = [
        _configmap(
            "objecten-configuration", {"objecttypes": {"items": [{"identifier": "x", "service_identifier": "y"}]}}
        ),
        _configmap(
            "openformulieren-configuration",
            {"zgw_consumers": {"services": [{"identifier": "objecttypes-api", "header_value": "Token old"}]}},
        ),
    ]
    manifests.fixup_merged_objecten(docs)
    objecten = yaml.safe_load(docs[0]["data"]["configuration.yaml"])
    openformulieren = yaml.safe_load(docs[1]["data"]["configuration.yaml"])
    assert objecten["objecttypes"]["items"] == [{"identifier": "x"}]
    assert openformulieren["zgw_consumers"]["services"][0]["header_value"] == "Token fakeOpenFormulierenObjectsToken"


def test_fix_up_leaves_objecten_alone_on_the_classic_shape():
    doc = _configmap("objecten-configuration", {"objecttypes": {"items": [{"service_identifier": "y"}]}})
    render = manifests.fix_up(yaml.safe_dump(doc), objecten_merged=False, zac_pkce=False)
    assert render.docs == [doc]


def test_fix_realm_switches_pkce_only_for_the_zac_client():
    realm = {
        "clients": [{"clientId": "zaakafhandelcomponent", "attributes": {}}, {"clientId": "other", "attributes": {}}]
    }
    doc: manifests.Doc = {
        "kind": "ConfigMap",
        "metadata": {"name": "keycloak-realm"},
        "data": {"zaakafhandelcomponent-realm.json": json.dumps(realm)},
    }
    manifests.fix_realm([doc], zac_pkce=True)
    clients = json.loads(doc["data"]["zaakafhandelcomponent-realm.json"])["clients"]
    assert clients[0]["attributes"]["pkce.code.challenge.method"] == "S256"
    assert clients[1]["attributes"] == {}
    manifests.fix_realm([doc], zac_pkce=False)
    clients = json.loads(doc["data"]["zaakafhandelcomponent-realm.json"])["clients"]
    assert clients[0]["attributes"]["pkce.code.challenge.method"] == ""


def test_fix_up_splits_off_large_configmaps():
    large = {
        "kind": "ConfigMap",
        "metadata": {"name": "dashboards"},
        "data": {"x": "y" * manifests.LARGE_CONFIGMAP_BYTES},
    }
    small = {"kind": "ConfigMap", "metadata": {"name": "small"}, "data": {"x": "y"}}
    render = manifests.fix_up(yaml.safe_dump_all([large, small]), objecten_merged=False, zac_pkce=False)
    assert render.docs == [small]
    assert render.large_configmaps == [large]
    assert _docs(render.manifest) == [small]


def test_trust_ca_mounts_the_ca_in_workloads_and_jobs():
    docs: list[manifests.Doc] = [
        {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [{"name": "app", "env": [{"name": "SSL_CERT_FILE", "value": "/own"}]}]}}}},
        {"kind": "Job", "metadata": {"name": "openzaak-config"}, "spec": {"template": {"spec": {"containers": [{"name": "job"}]}}}},
        {"kind": "Job", "metadata": {"name": "storage-permissions-fix"}, "spec": {"template": {"spec": {"containers": [{"name": "job"}]}}}},
        {"kind": "StatefulSet", "spec": {"template": {"spec": {"volumes": None, "containers": [{"name": "db", "env": None, "volumeMounts": None}]}}}},
    ]  # fmt: skip
    manifests.trust_ca(docs)
    app = docs[0]["spec"]["template"]["spec"]
    assert app["volumes"] == [{"name": "podiumd-ca", "configMap": {"name": "podiumd-ca"}}]
    env = {item["name"]: item["value"] for item in app["containers"][0]["env"]}
    assert env["SSL_CERT_FILE"] == "/own"
    assert env["REQUESTS_CA_BUNDLE"] == "/etc/podiumd-ca/ca-bundle.pem"
    assert "truststore.p12" in env["JAVA_TOOL_OPTIONS"]
    assert docs[1]["spec"]["template"]["spec"]["volumes"]
    assert docs[2]["spec"]["template"]["spec"]["volumes"]
    assert docs[3]["spec"]["template"]["spec"]["containers"][0]["env"]


def test_limit_runtimes_adds_capacity_env_unless_the_container_sets_it():
    docs: list[manifests.Doc] = [
        {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [{"name": "app", "env": [{"name": "DOTNET_gcServer", "value": "1"}]}]}}}},
        {"kind": "CronJob", "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": [{"name": "sync"}]}}}}}},
        {"kind": "Service", "spec": {}},
    ]  # fmt: skip
    manifests.limit_runtimes(docs)
    app = {item["name"]: item["value"] for item in docs[0]["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert app == {**manifests.CAPACITY_ENV, "DOTNET_gcServer": "1"}
    sync = docs[1]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {"name": "DOTNET_gcServer", "value": "0"} in sync
    assert docs[2] == {"kind": "Service", "spec": {}}


def test_limit_runtimes_reaches_the_containers_of_eck_pod_templates():
    docs: list[manifests.Doc] = [
        {"kind": "Elasticsearch", "spec": {"nodeSets": [{"name": "default", "podTemplate": {"spec": {"containers": [{"name": "elasticsearch"}]}}}]}},
        {"kind": "Kibana", "spec": {"podTemplate": {"spec": {"containers": [{"name": "kibana"}]}}}},
    ]  # fmt: skip
    manifests.limit_runtimes(docs)
    elasticsearch = docs[0]["spec"]["nodeSets"][0]["podTemplate"]["spec"]["containers"][0]["env"]
    kibana = docs[1]["spec"]["podTemplate"]["spec"]["containers"][0]["env"]
    assert {"name": "MALLOC_ARENA_MAX", "value": "2"} in elasticsearch
    assert {"name": "MALLOC_ARENA_MAX", "value": "2"} in kibana


def test_limit_runtimes_gives_containers_with_a_memory_limit_gomemlimit():
    docs: list[manifests.Doc] = [
        {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [
            {"name": "tempo", "resources": {"limits": {"memory": "256Mi"}}},
            {"name": "zac", "resources": {"requests": {"memory": "1Gi"}}},
            {"name": "own", "resources": {"limits": {"memory": "1Gi"}}, "env": [{"name": "GOMEMLIMIT", "value": "900MiB"}]},
        ]}}}},
    ]  # fmt: skip
    manifests.limit_runtimes(docs)
    tempo, zac, own = docs[0]["spec"]["template"]["spec"]["containers"]
    go = {
        "name": "GOMEMLIMIT",
        "valueFrom": {"resourceFieldRef": {"containerName": "tempo", "resource": "limits.memory"}},
    }
    assert go in tempo["env"]
    assert not any(item["name"] == "GOMEMLIMIT" for item in zac["env"])
    assert [item for item in own["env"] if item["name"] == "GOMEMLIMIT"] == [{"name": "GOMEMLIMIT", "value": "900MiB"}]


def test_with_https_adds_twins_only_for_local_hosts():
    urls = ["http://zac.local/*", "http://zac.local", "http://localhost:8080/*", "https://kiss.local"]
    assert manifests.with_https(urls) == [*urls, "https://zac.local/*", "https://zac.local"]
    assert manifests.with_https(manifests.with_https(urls)) == manifests.with_https(urls)


def test_fix_up_separates_crds_for_server_side_apply():
    crd = {"kind": "CustomResourceDefinition", "metadata": {"name": "elasticsearches.elasticsearch.k8s.elastic.co"}}
    other = {"kind": "Service", "metadata": {"name": "zac"}}
    render = manifests.fix_up(yaml.safe_dump_all([crd, other]), objecten_merged=False, zac_pkce=False)
    assert render.crds == [crd]
    assert render.docs == [other]


def test_route_outbound_only_with_the_outway():
    config: manifests.Doc = {"kind": "ConfigMap", "metadata": {"name": "zac"}, "data": {"BRP": "http://api-proxy/x"}}
    manifests.route_outbound([config])
    assert config["data"]["BRP"] == "http://api-proxy/x"
    outway: manifests.Doc = {"kind": "Service", "metadata": {"name": manifests.OUTWAY_SERVICE}}
    manifests.route_outbound([config, outway])
    assert config["data"]["BRP"] == "http://frankgateway-outway:9080/x"
