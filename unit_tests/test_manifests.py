"""podiumd_minikube.manifests: the fixups applied to helm template output."""

import yaml

from podiumd_minikube import manifests

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


def test_allow_rolling_updates_drops_required_anti_affinity_from_deployments_only():
    affinity = """
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution: [{topologyKey: kubernetes.io/hostname}]
          preferredDuringSchedulingIgnoredDuringExecution: []"""
    docs = _docs(f"""
kind: Deployment
spec:
  template:
    spec:{affinity}
---
kind: StatefulSet
spec:
  template:
    spec:{affinity}
""")
    manifests.allow_rolling_updates(docs)
    assert docs[0]["spec"]["template"]["spec"]["affinity"] == {
        "podAntiAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": []}
    }
    assert (
        "requiredDuringSchedulingIgnoredDuringExecution"
        in docs[1]["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"]
    )


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


def test_fix_up_drops_test_and_delete_hooks():
    text = """
kind: Pod
metadata: {name: grafana-test, annotations: {helm.sh/hook: test}}
---
kind: Job
metadata: {name: redis-ha-pre-delete, annotations: {helm.sh/hook: pre-delete}}
---
kind: Job
metadata: {name: migrate, annotations: {helm.sh/hook: "pre-install,pre-upgrade"}}
---
kind: Service
metadata: {name: zac}
"""
    render = manifests.fix_up(text, objecten_merged=False)
    assert [manifests.name_of(doc) for doc in render.docs] == ["migrate", "zac"]


def _configmap(name: str, configuration: object) -> manifests.Doc:
    return {
        "kind": "ConfigMap",
        "metadata": {"name": name},
        "data": {"configuration.yaml": yaml.safe_dump(configuration)},
    }


def test_fixup_merged_objecten_removes_classic_only_settings():
    docs = [
        _configmap(
            "objecten-configuration",
            {
                "objecttypes": {"items": [{"identifier": "x", "service_identifier": "y"}]},
                "tokenauth": {"items": [{"identifier": "zac"}]},
            },
        ),
        _configmap(
            "openformulieren-configuration",
            {"zgw_consumers": {"services": [{"identifier": "objecttypen-api", "header_value": "Token old"}]}},
        ),
    ]
    manifests.fixup_merged_objecten(docs)
    objecten = yaml.safe_load(docs[0]["data"]["configuration.yaml"])
    openformulieren = yaml.safe_load(docs[1]["data"]["configuration.yaml"])
    assert objecten["objecttypes"]["items"] == [{"identifier": "x"}]
    assert objecten["tokenauth"]["items"] == [{"identifier": "zac"}, manifests.REQUIRED_OBJECTTYPEN_TOKEN]
    assert openformulieren["zgw_consumers"]["services"][0]["header_value"] == "Token objectenFormulierToken"


def test_fix_up_leaves_objecten_alone_on_the_classic_shape():
    doc = _configmap("objecten-configuration", {"objecttypes": {"items": [{"service_identifier": "y"}]}})
    render = manifests.fix_up(yaml.safe_dump(doc), objecten_merged=False)
    assert render.docs == [doc]


def test_fix_up_splits_off_large_configmaps():
    large = {
        "kind": "ConfigMap",
        "metadata": {"name": "dashboards"},
        "data": {"x": "y" * manifests.LARGE_CONFIGMAP_BYTES},
    }
    small = {"kind": "ConfigMap", "metadata": {"name": "small"}, "data": {"x": "y"}}
    render = manifests.fix_up(yaml.safe_dump_all([large, small]), objecten_merged=False)
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


def test_serve_postgres_tls_adds_the_certificate_and_turns_on_ssl():
    postgres: manifests.Doc = {
        "kind": "Deployment",
        "metadata": {"name": "postgres"},
        "spec": {"template": {"spec": {"containers": [{"name": "postgres", "args": ["postgres"]}]}}},
    }
    other: manifests.Doc = {
        "kind": "Deployment",
        "metadata": {"name": "zac"},
        "spec": {"template": {"spec": {"containers": []}}},
    }
    docs = manifests.serve_postgres_tls([other, postgres])
    certificate = docs[-1]
    assert certificate["kind"] == "Certificate"
    assert certificate["spec"]["secretName"] == manifests.POSTGRES_TLS
    assert "postgres" in certificate["spec"]["dnsNames"]
    container = postgres["spec"]["template"]["spec"]["containers"][0]
    assert container["args"][:3] == ["postgres", "-c", "ssl=on"]
    assert container["command"][-1] == "--"
    assert "install -o postgres -m 600 /etc/postgres-tls/tls.key" in container["command"][2]
    assert postgres["spec"]["template"]["spec"]["volumes"] == [
        {"name": manifests.POSTGRES_TLS, "secret": {"secretName": manifests.POSTGRES_TLS}}
    ]
    assert manifests.serve_postgres_tls([other]) == [other]


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


def test_limit_runtimes_reaches_the_prometheus_container_the_operator_builds():
    docs: list[manifests.Doc] = [{"kind": "Prometheus", "spec": {"resources": {"limits": {"memory": "512Mi"}}}}]
    manifests.limit_runtimes(docs)
    (prometheus,) = docs[0]["spec"]["containers"]
    assert prometheus["name"] == "prometheus"
    assert prometheus["resources"] == {"limits": {"memory": "512Mi"}}
    assert {item["name"] for item in prometheus["env"]} >= {"GOMEMLIMIT", "MALLOC_ARENA_MAX"}


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


def test_tune_solr_sets_the_solr_and_zookeeper_jvm_env():
    provided: manifests.Doc = {"replicas": 1}
    spec: manifests.Doc = {"zookeeperRef": {"provided": provided}}
    manifests.tune_solr([{"kind": "SolrCloud", "spec": spec}])
    pod_options = manifests.section(manifests.section(spec, "customSolrKubeOptions"), "podOptions")
    assert {"name": "GC_TUNE", "value": "-XX:+UseSerialGC"} in pod_options["envVars"]
    zookeeper_env = manifests.section(provided, "zookeeperPodPolicy")["env"]
    assert {"name": "ZK_SERVER_HEAP", "value": "128"} in zookeeper_env
    assert {"name": "MALLOC_ARENA_MAX", "value": "2"} in zookeeper_env


def test_keep_finished_jobs_drops_only_the_jobs_ttl():
    job: manifests.Doc = {"kind": "Job", "spec": {"ttlSecondsAfterFinished": 60, "backoffLimit": 6}}
    cron: manifests.Doc = {"kind": "CronJob", "spec": {"jobTemplate": {"spec": {"ttlSecondsAfterFinished": 60}}}}
    manifests.keep_finished_jobs([job, cron])
    assert job["spec"] == {"backoffLimit": 6}
    assert cron["spec"]["jobTemplate"]["spec"] == {"ttlSecondsAfterFinished": 60}


def test_tune_keycloak_sets_jvm_flags_and_the_container_resources():
    resources: manifests.Doc = {"limits": {"memory": "768Mi"}}
    builder: manifests.Doc = {"name": "keycloak-builder"}
    server: manifests.Doc = {"name": "keycloak"}
    container: manifests.Doc = {"name": "operator"}
    template: manifests.Doc = {"spec": {"initContainers": [builder], "containers": [server]}}
    keycloak: manifests.Doc = {
        "kind": "Keycloak",
        "spec": {"resources": resources, "unsupported": {"podTemplate": template}},
    }
    operator: manifests.Doc = {
        "kind": "Deployment",
        "metadata": {"name": manifests.KEYCLOAK_OPERATOR},
        "spec": {"template": {"spec": {"containers": [container]}}},
    }
    manifests.tune_keycloak([keycloak, operator])
    assert {"name": "JAVA_OPTS_APPEND", "value": manifests.KEYCLOAK_JAVA_OPTS} in builder["env"]
    assert {"name": "JAVA_OPTS_APPEND", "value": manifests.KEYCLOAK_JAVA_OPTS} in server["env"]
    assert server["resources"] == resources
    assert "resources" not in builder
    assert container["env"] == [
        {"name": name, "value": value} for name, value in manifests.KEYCLOAK_OPERATOR_ENV.items()
    ]


def test_fix_up_separates_crds_for_server_side_apply():
    crd = {"kind": "CustomResourceDefinition", "metadata": {"name": "elasticsearches.elasticsearch.k8s.elastic.co"}}
    other = {"kind": "Service", "metadata": {"name": "zac"}}
    render = manifests.fix_up(yaml.safe_dump_all([crd, other]), objecten_merged=False)
    assert render.crds == [crd]
    assert render.docs == [other]


def test_route_outbound_only_with_the_outway():
    config: manifests.Doc = {"kind": "ConfigMap", "metadata": {"name": "zac"}, "data": {"BRP": "http://api-proxy/x"}}
    manifests.route_outbound([config])
    assert config["data"]["BRP"] == "http://api-proxy/x"
    outway: manifests.Doc = {"kind": "Service", "metadata": {"name": manifests.OUTWAY_SERVICE}}
    manifests.route_outbound([config, outway])
    assert config["data"]["BRP"] == "http://frankgateway-outway:9080/x"


def test_route_outbound_rolls_the_pods_that_read_a_routed_configmap():
    def deployment(configmap: str) -> manifests.Doc:
        container = {"name": "app", "envFrom": [{"configMapRef": {"name": configmap}}]}
        return {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [container]}}}}

    config: manifests.Doc = {"kind": "ConfigMap", "metadata": {"name": "zac"}, "data": {"BRP": "http://api-proxy/x"}}
    reader, other = deployment("zac"), deployment("pabc")
    manifests.route_outbound([config, reader, other])
    annotations = reader["spec"]["template"]["metadata"]["annotations"]
    assert annotations[manifests.OUTBOUND_ANNOTATION] == manifests.API_PROXY_URL
    assert "metadata" not in other["spec"]["template"]


def _ingress(service: str) -> manifests.Doc:
    backend = {"service": {"name": service, "port": {"number": 80}}}
    return {
        "kind": "Ingress",
        "spec": {"rules": [{"host": "zac.local", "http": {"paths": [{"path": "/", "backend": backend}]}}]},
    }


def test_route_through_nginx_only_when_the_nginx_service_is_rendered():
    with_nginx: list[manifests.Doc] = [_ingress("zac"), {"kind": "Service", "metadata": {"name": "zac-nginx"}}]
    without: list[manifests.Doc] = [_ingress("zac")]
    manifests.route_through_nginx(with_nginx)
    manifests.route_through_nginx(without)
    assert with_nginx[0]["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "zac-nginx"
    assert without[0]["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["name"] == "zac"


def test_drop_duplicate_env_keeps_the_last_entry_per_name():
    env = [{"name": "A", "value": "chart"}, {"name": "B", "value": "1"}, {"name": "A", "value": "ours"}]
    doc: manifests.Doc = {"kind": "Deployment", "spec": {"template": {"spec": {"containers": [{"env": env}]}}}}
    manifests.drop_duplicate_env([doc])
    assert doc["spec"]["template"]["spec"]["containers"][0]["env"] == [
        {"name": "B", "value": "1"},
        {"name": "A", "value": "ours"},
    ]
