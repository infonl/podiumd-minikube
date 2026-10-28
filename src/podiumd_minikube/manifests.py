"""Local fixups applied to `helm template` output before it is applied.

deploy renders with `helm template | kubectl apply` instead of `helm install`:
Helm's release record embeds the whole podiumd dependency and exceeds the 3MB
API request limit. Without a release, Helm hooks never run and the render has
to be corrected here.
"""

import re

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import cast

import yaml

from podiumd_minikube import memory
from podiumd_minikube import pki
from podiumd_minikube import process
from podiumd_minikube import tls
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME

Doc = dict[str, Any]
# libyaml: a --full render loads in 0.3 s instead of 3.1 s, dumps in 0.06 s instead of 0.5 s.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


def _load(text: str) -> Any:
    return yaml.load(text, _LOADER)  # nosec B506  # noqa: S506 - a safe loader


# The apps' BRP/KvK/BAG base URL (values.yaml), and Frank!Gateway's outway, which
# serves the same paths (podiumd.frankgateway.instances.outway.routes).
API_PROXY_URL = "http://api-proxy/"
OUTWAY_SERVICE = "frankgateway-outway"
OUTWAY_URL = f"http://{OUTWAY_SERVICE}:9080/"
OUTBOUND_ANNOTATION = "podiumd-minikube/outbound"
# ECK resources; ECK renders their StatefulSets and Deployments itself.
ECK_KINDS = ("Elasticsearch", "Kibana")
# Laptop budget: these runtimes size themselves by the CPUs they see (all of the
# host's) and keep memory for throughput; other runtimes ignore the variables.
# .NET: workstation GC without the background GC, compacting more. glibc: up to
# 8 malloc arenas per CPU in threaded processes (uWSGI threads, JVMs).
# Elasticsearch: its launcher JVM stays resident beside the server (heap in use 13 MiB).
# nginx images: `worker_processes auto` counts the host's CPUs, not the cpuset; the
# entrypoint's autotune uses the CPU limit or the cpuset (needs a writable nginx.conf).
CAPACITY_ENV = {
    "DOTNET_gcServer": "0",
    "DOTNET_gcConcurrent": "0",
    "DOTNET_GCConserveMemory": "7",
    "APISIX_WORKER_PROCESSES": "2",
    "NGINX_ENTRYPOINT_WORKER_PROCESSES_AUTOTUNE": "1",
    "MALLOC_ARENA_MAX": "2",
    "CLI_JAVA_OPTS": "-Xmx32m -XX:TieredStopAtLevel=1",
    **memory.GO_NO_HUGE_PAGES,
}
GO_MEMORY_LIMIT = "GOMEMLIMIT"
# Laptop budget, Keycloak: kc.sh sets -XX:+UseG1GC, which -XX:-UseG1GC switches off for the
# serial GC; TieredStopAtLevel=1: only the C1 compiler. Its operator runs plain `java -jar`.
KEYCLOAK_JAVA_OPTS = (
    "-Xms64m -Xmx512m -XX:ActiveProcessorCount=2 -XX:-UseG1GC -XX:+UseSerialGC -XX:TieredStopAtLevel=1"
    " -XX:MinHeapFreeRatio=10 -XX:MaxHeapFreeRatio=30"
)
KEYCLOAK_OPERATOR = "keycloak-operator"
# Heap in use about 55 MiB. The operator SDK starts 50 reconciliation and 50 workflow
# threads; one Keycloak resource needs 2 of each.
# kcadm.sh reads KC_OPTS; Jobs on the Keycloak image start one JVM per call. C1 only, the serial
# GC and an archive of the loaded classes (written on the first call): 1040 -> 530 ms a call.
KCADM_ENV = {
    "KC_OPTS": "-XX:TieredStopAtLevel=1 -XX:+UseSerialGC -XX:+AutoCreateSharedArchive"
    " -XX:SharedArchiveFile=/tmp/kcadm.jsa"
}
KEYCLOAK_OPERATOR_ENV = {
    "JDK_JAVA_OPTIONS": "-Xms32m -Xmx128m -XX:ActiveProcessorCount=1 -XX:+UseSerialGC -XX:TieredStopAtLevel=1",
    "QUARKUS_OPERATOR_SDK_CONCURRENT_RECONCILIATION_THREADS": "2",
    "QUARKUS_OPERATOR_SDK_CONCURRENT_WORKFLOW_THREADS": "2",
}
# Solr's default G1 settings replaced: the serial GC lets the heap follow what is in use.
SOLR_ENV = {"GC_TUNE": "-XX:+UseSerialGC"}
# zkEnv.sh's ZK_SERVER_HEAP (MB) defaults to 1000. -XX:-UseContainerSupport: the
# image's JDK 11.0.16 throws a NullPointerException reading cgroup v2 metrics on
# kernel 7.x (its JMX agent then exits); heap and CPU count are set explicitly.
ZOOKEEPER_ENV = {
    "ZK_SERVER_HEAP": "128",
    "SERVER_JVMFLAGS": (
        "-Xms16m -XX:+UseSerialGC -XX:TieredStopAtLevel=1 -XX:ActiveProcessorCount=1 -XX:-UseContainerSupport"
    ),
}
# Headroom under the 262144-byte last-applied-configuration annotation of client-side apply.
LARGE_CONFIGMAP_BYTES = 200_000

# A tag in the reference, or an operator resource's separate `tag:` field.
_DIGEST_SUFFIX = re.compile(r"""((?::|\btag:[ \t]*"?)[^\s@"]+)@sha256:[0-9a-f]{64}\b""")
# [ \t], not \s: an `image:` key without a value must not take the next line.
_IMAGE_LINE = re.compile(r"""^[ \t]*(?:-[ \t]+)?image:[ \t]*"?([^"\s]+)""", re.MULTILINE)
# Operator resources (SolrCloud, its ZooKeeper) name an image as repository and tag.
_REPOSITORY_TAG = re.compile(
    r"""^([ \t]*)repository:[ \t]*"?([^"\s]+)"?[ \t]*\n\1tag:[ \t]*"?([^"\s]+)""", re.MULTILINE
)
# The Prometheus operator starts each Prometheus with this sidecar image.
_RELOADER_ARG = re.compile(r"--prometheus-config-reloader=(\S+)")
# ECK's image for an Elasticsearch resource without `image`.
_ELASTICSEARCH_IMAGE = "docker.elastic.co/elasticsearch/elasticsearch"
_POD_SPEC_PATHS = {
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}


def strip_image_digests(text: str) -> str:
    """Drops "@sha256:..." from tagged image references.

    minikube has no registry access; images are loaded by tag, and kubelet
    does not match a tag-only image to a digest reference.
    """
    return _DIGEST_SUFFIX.sub(r"\1", text)


# helm template's comment before each document: "# Source: <chart>/[charts/<dependency>/]<path>".
_SOURCE = re.compile(r"^# Source: ([^/]+)/(?:charts/([^/]+)/)?", re.MULTILINE)


def images(text: str) -> list[str]:
    """Sorted unique images in rendered text, including those operators start from their resources."""
    found = set(_IMAGE_LINE.findall(text)) | set(_RELOADER_ARG.findall(text))
    found |= {f"{repository}:{tag}" for _, repository, tag in _REPOSITORY_TAG.findall(text)}
    for chunk in re.split(r"^---$", text, flags=re.MULTILINE):
        doc: Doc | None = _load(chunk)
        spec = section(doc, "spec") if doc and doc.get("kind") == "Elasticsearch" else {}
        if spec.get("version") and not spec.get("image"):
            found.add(f"{_ELASTICSEARCH_IMAGE}:{spec['version']}")
    return sorted(found)


def section(node: Doc, key: str) -> Doc:
    """node[key] when it is a mapping, else {} (rendered sections are often null)."""
    value = node.get(key)
    return cast("Doc", value) if isinstance(value, dict) else {}


def _mapping(node: Doc, *keys: str) -> Doc:
    """node[keys[0]][keys[1]]..., creating each missing or null mapping on the way."""
    for key in keys:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    return node


def name_of(doc: Doc) -> str:
    """metadata.name, or ""."""
    return str(section(doc, "metadata").get("name") or "")


def _is_configmap(doc: Doc, name: str) -> bool:
    return doc.get("kind") == "ConfigMap" and name_of(doc) == name


def disable_service_links(docs: list[Doc]) -> None:
    """Sets enableServiceLinks: false on every workload's pod spec.

    Injected <SERVICE>_PORT=tcp://... variables broke Solr and
    opennotificaties-worker. Bare Jobs are skipped: their pod template is
    immutable, so a change would make apply fail on existing Jobs.
    """
    for doc in docs:
        path = _POD_SPEC_PATHS.get(doc.get("kind", ""))
        if not path:
            continue
        node: Doc | None = doc
        for key in path:
            value = node.get(key) if node is not None else None
            node = cast("Doc", value) if isinstance(value, dict) else None
        if node is not None:
            node["enableServiceLinks"] = False


def route_outbound(docs: list[Doc]) -> None:
    """Points ConfigMap values at OUTWAY_URL instead of API_PROXY_URL when the outway is rendered.

    Their pods are annotated with the target: a pod reads ConfigMap env only at
    its start, so toggling the outway must roll them.
    """
    outway = any(doc.get("kind") == "Service" and name_of(doc) == OUTWAY_SERVICE for doc in docs)
    target = OUTWAY_URL if outway else API_PROXY_URL
    _roll_readers(docs, _replace_in_configmaps(docs, API_PROXY_URL, target), OUTBOUND_ANNOTATION, target)


def _replace_in_configmaps(docs: list[Doc], old: str, new: str) -> set[str]:
    """Replaces old by new in every ConfigMap value; returns the names of the ConfigMaps that had it."""
    changed: set[str] = set()
    for doc in docs:
        if doc.get("kind") != "ConfigMap":
            continue
        data: Doc = doc.get("data") or {}
        for key, value in data.items():
            if isinstance(value, str) and old in value:
                data[key] = value.replace(old, new)
                changed.add(name_of(doc))
    return changed


def _roll_readers(docs: list[Doc], configmaps: set[str], annotation: str, value: str) -> None:
    """Annotates the pod templates that read configmaps with annotation=value, so a change rolls them."""
    for doc in docs:
        spec = _pod_spec(doc)
        if doc.get("kind") in _POD_SPEC_PATHS and spec is not None and configmaps & _configmaps_used(spec):
            template = _mapping(doc, *_POD_SPEC_PATHS[doc["kind"]][:-1])
            _mapping(template, "metadata", "annotations")[annotation] = value


# podiumd's api-proxy mounts its nginx.conf from a ConfigMap, read-only for the
# entrypoint's autotune (CAPACITY_ENV); `auto` counts the host's CPUs, not the cpuset.
# One worker: api-proxy's CPU limit is 500m.
NGINX_AUTO_WORKERS = "worker_processes auto;"
NGINX_WORKERS = "1"
NGINX_WORKERS_ANNOTATION = "podiumd-minikube/nginx-workers"


def limit_nginx_workers(docs: list[Doc]) -> None:
    """Sets NGINX_WORKERS in ConfigMaps with NGINX_AUTO_WORKERS and rolls the pods that read them."""
    changed = _replace_in_configmaps(docs, NGINX_AUTO_WORKERS, f"worker_processes {NGINX_WORKERS};")
    _roll_readers(docs, changed, NGINX_WORKERS_ANNOTATION, NGINX_WORKERS)


def _configmaps_used(spec: Doc) -> set[str]:
    """Names of the ConfigMaps a pod spec reads through env, envFrom or volumes."""
    volumes: list[Doc] = spec.get("volumes") or []
    names = {section(volume, "configMap").get("name") for volume in volumes}
    for container in _containers(spec):
        sources: list[Doc] = container.get("envFrom") or []
        items: list[Doc] = container.get("env") or []
        names |= {section(source, "configMapRef").get("name") for source in sources}
        names |= {section(section(item, "valueFrom"), "configMapKeyRef").get("name") for item in items}
    return {str(name) for name in names if name}


# App Service -> the nginx Service in front of it. ZAC's chart points its
# Ingress at the app even with nginx.enabled; ExternalsPodiumD routes to zac-nginx.
NGINX_FRONTS = {"zac": "zac-nginx"}


def _ingress_rules(docs: list[Doc]) -> list[Doc]:
    """The rules of every Ingress in docs."""
    rules: list[Doc] = []
    for doc in docs:
        if doc.get("kind") == "Ingress":
            rules += section(doc, "spec").get("rules") or []
    return rules


def route_through_nginx(docs: list[Doc]) -> None:
    """Points Ingress backends of NGINX_FRONTS apps at their nginx Service when the render has it."""
    services = {name_of(doc) for doc in docs if doc.get("kind") == "Service"}
    for rule in _ingress_rules(docs):
        paths: list[Doc] = section(rule, "http").get("paths") or []
        for path in paths:
            service = section(section(path, "backend"), "service")
            front = NGINX_FRONTS.get(service.get("name", ""))
            if front in services:
                service["name"] = front


def ingress_hosts(docs: list[Doc]) -> list[str]:
    """Sorted unique hosts of the Ingresses in docs."""
    return sorted({str(rule["host"]) for rule in _ingress_rules(docs) if rule.get("host")})


def drop_duplicate_env(docs: list[Doc]) -> None:
    """Keeps only the last env entry per name, the one Kubernetes uses.

    A chart value and extraEnvVars can both set a variable (Open Inwoner's
    low-latency worker: CELERY_WORKER_CONCURRENCY); kubectl apply warns that
    the earlier one may be dropped.
    """
    for doc in docs:
        for spec in _runtime_pod_specs(doc):
            for container in _containers(spec):
                env: list[Doc] = container.get("env") or []
                last = {item["name"]: index for index, item in enumerate(env)}
                if len(last) < len(env):
                    container["env"] = [item for index, item in enumerate(env) if last[item["name"]] == index]


def allow_rolling_updates(docs: list[Doc]) -> None:
    """Drops required pod anti-affinity from Deployments.

    On one node a rolling update's new pod stays Pending next to the old one
    (Loki's gateway). StatefulSets replace their pod in place and keep it.
    """
    for doc in docs:
        if doc.get("kind") == "Deployment" and (spec := _pod_spec(doc)) is not None:
            section(section(spec, "affinity"), "podAntiAffinity").pop(
                "requiredDuringSchedulingIgnoredDuringExecution", None
            )


def _pod_spec(doc: Doc) -> Doc | None:
    """doc's pod spec for workloads (and Jobs), or None."""
    path = _POD_SPEC_PATHS.get(doc.get("kind", "")) or (
        ("spec", "template", "spec") if doc.get("kind") == "Job" else None
    )
    node: Doc | None = doc
    for key in path or ():
        value = node.get(key) if node is not None else None
        node = cast("Doc", value) if isinstance(value, dict) else None
    return node if path else None


def _runtime_pod_specs(doc: Doc) -> list[Doc]:
    """doc's pod specs: a workload's or Job's, and those an operator expands into its own StatefulSets.

    ECK and Keycloak: the podTemplate specs. Prometheus: its `containers` override, given the
    prometheus container's resources, which the operator merges into its pod.
    """
    spec = _pod_spec(doc)
    if spec is not None:
        return [spec]
    kind, body = doc.get("kind"), section(doc, "spec")
    if kind == "Keycloak":
        return [section(section(section(body, "unsupported"), "podTemplate"), "spec")]
    if kind == "Prometheus":
        containers: list[Doc] = body.setdefault("containers", [])
        if not any(c.get("name") == "prometheus" for c in containers):
            containers.append({"name": "prometheus", "resources": body.get("resources") or {}})
        return [{"containers": containers}]
    node_sets: list[Doc] = (body.get("nodeSets") or []) if kind == "Elasticsearch" else []
    templates = [section(node_set, "podTemplate") for node_set in node_sets]
    if kind == "Kibana":
        templates = [section(body, "podTemplate")]
    return [section(template, "spec") for template in templates]


def trust_ca(docs: list[Doc]) -> None:
    """Mounts the local CA (pki.TRUST_CONFIGMAP) in every workload and Job, with its env.

    The reference environments call each other on public https hosts with
    publicly trusted certificates; here every client must trust the local
    CA. Jobs are recreated by every deploy (podiumd_minikube.deploy), so their immutable
    spec can change.
    """
    for doc in docs:
        spec = _pod_spec(doc)
        if spec is None:
            continue
        volumes: list[Doc] = spec.get("volumes") or []
        spec["volumes"] = volumes
        if not any(volume.get("name") == pki.TRUST_CONFIGMAP for volume in volumes):
            volumes.append({"name": pki.TRUST_CONFIGMAP, "configMap": {"name": pki.TRUST_CONFIGMAP}})
        for container in _containers(spec):
            mounts: list[Doc] = container.get("volumeMounts") or []
            container["volumeMounts"] = mounts
            mounts.append({"name": pki.TRUST_CONFIGMAP, "mountPath": pki.TRUST_DIR, "readOnly": True})
            add_env(container, pki.TRUST_ENV)


POSTGRES_TLS = "postgres-tls"
_POSTGRES_TLS_DIR = "/etc/postgres-tls"


def serve_postgres_tls(docs: list[Doc]) -> list[Doc]:
    """Turns on TLS in the Postgres Deployment, with a Certificate from the local CA added to docs.

    podiumd's apps connect with sslmode require, as to the reference
    environments' Azure Postgres. Postgres refuses a key file that its user
    cannot own with mode 0600, so the entrypoint copies it from the Secret first.
    """
    for doc in docs:
        if doc.get("kind") != "Deployment" or name_of(doc) != "postgres":
            continue
        spec = cast("Doc", _pod_spec(doc))
        spec.setdefault("volumes", []).append({"name": POSTGRES_TLS, "secret": {"secretName": POSTGRES_TLS}})
        container = _containers(spec)[0]
        container.setdefault("volumeMounts", []).append(
            {"name": POSTGRES_TLS, "mountPath": _POSTGRES_TLS_DIR, "readOnly": True}
        )
        key, cert = "/var/run/postgresql/tls.key", "/var/run/postgresql/tls.crt"
        container["command"] = [
            "sh",
            "-c",
            (
                f"install -o postgres -m 600 {_POSTGRES_TLS_DIR}/tls.key {key}"
                f" && install -o postgres -m 644 {_POSTGRES_TLS_DIR}/tls.crt {cert}"
                ' && exec docker-entrypoint.sh "$@"'
            ),
            "--",
        ]
        container["args"] = [
            *container["args"],
            "-c",
            "ssl=on",
            "-c",
            f"ssl_cert_file={cert}",
            "-c",
            f"ssl_key_file={key}",
        ]
        service = f"postgres.{NAMESPACE}"
        return [*docs, tls.certificate(POSTGRES_TLS, NAMESPACE, ["postgres", service, f"{service}.svc"])]
    return docs


# deploy --otel enables ZAC's collector subchart only for ZAC's endpoint; its template omits
# `replicas` for replicaCount 0 (`and ... .Values.replicaCount`), so Kubernetes would run 1.
ZAC_COLLECTOR = "zac-opentelemetry-collector"


def is_unused_zac_collector(doc: Doc) -> bool:
    """Whether doc is the Deployment of ZAC's own otel-collector, which nothing calls."""
    return doc.get("kind") == "Deployment" and name_of(doc) == ZAC_COLLECTOR


def mark_release_owner(docs: list[Doc]) -> None:
    """Adds the release annotations `helm upgrade` sets; the chart's objecttypen-compat-service checks them."""
    for doc in docs:
        _mapping(doc, "metadata", "annotations").update(
            {"meta.helm.sh/release-name": RELEASE_NAME, "meta.helm.sh/release-namespace": NAMESPACE}
        )


def keep_finished_jobs(docs: list[Doc]) -> None:
    """Drops ttlSecondsAfterFinished from Jobs, so deploy sees their outcome after its slower steps.

    deploy deletes and re-runs every rendered Job (deploy._rerun_jobs), so none piles up.
    """
    for doc in docs:
        if doc.get("kind") == "Job":
            section(doc, "spec").pop("ttlSecondsAfterFinished", None)


def tune_keycloak(docs: list[Doc]) -> None:
    """Sets the JVM flags of Keycloak and its operator, and the Keycloak resource's resources on its container.

    The operator takes the container's resources from the podTemplate over spec.resources.
    """
    for doc in docs:
        if doc.get("kind") == "Keycloak":
            body = section(doc, "spec")
            for spec in _runtime_pod_specs(doc):
                for container in _containers(spec):
                    add_env(container, {"JAVA_OPTS_APPEND": KEYCLOAK_JAVA_OPTS})
                    if container.get("name") == "keycloak" and body.get("resources"):
                        container["resources"] = body["resources"]
        elif doc.get("kind") == "Deployment" and name_of(doc) == KEYCLOAK_OPERATOR:
            for container in _containers(_pod_spec(doc) or {}):
                add_env(container, KEYCLOAK_OPERATOR_ENV)


def tune_kcadm(docs: list[Doc]) -> None:
    """Sets KCADM_ENV on the containers of Jobs that run the Keycloak image (kcadm.sh)."""
    for doc in docs:
        if doc.get("kind") == "Job":
            for container in _containers(_pod_spec(doc) or {}):
                if "/keycloak/keycloak:" in str(container.get("image")):
                    add_env(container, KCADM_ENV)


# Start-up timing only; start-cluster waited for each. Keycloak's startup probe already holds readiness
# back, so readiness is probed from the start (chart: 60 s after the container started, Keycloak needs
# 11 s), the startup probe every 5 s with the chart's 300 s budget. kc.sh build in keycloak-builder,
# run on every start: 40 s at the chart's 1 CPU.
KEYCLOAK_PROBES = {
    "readinessProbe": {"initialDelaySeconds": 0},
    "startupProbe": {"initialDelaySeconds": 0, "periodSeconds": 5, "failureThreshold": 60},
}
KEYCLOAK_BUILDER_CPU = "2"


def speed_up_keycloak_start(docs: list[Doc]) -> None:
    """Sets KEYCLOAK_PROBES on the Keycloak container and KEYCLOAK_BUILDER_CPU on keycloak-builder."""
    for doc in docs:
        if doc.get("kind") != "Keycloak":
            continue
        for spec in _runtime_pod_specs(doc):
            for container in _containers(spec):
                for probe, settings in KEYCLOAK_PROBES.items():
                    if isinstance(container.get(probe), dict):
                        container[probe].update(settings)
                if container.get("name") == "keycloak-builder":
                    _mapping(container, "resources", "limits")["cpu"] = KEYCLOAK_BUILDER_CPU


ZAC_SOLR_INIT = "init-solr-zac-core"
KEYCLOAK_ADMIN_USER = "ensure-podiumd-admin-user"
OPEN_INWONER_SEARCH_INDEX = "openinwoner-search-index"
# (kind, name, container) that fails while a dependency starts, and the Job or kubelet
# back-off retried it up to 5 minutes later. ZAC's init container: Solr still starting.
# Keycloak's admin user: its SQL needs the realm table Keycloak creates on its first start.
# Open Inwoner's index rebuild: its Elasticsearch still starting. The catalogus and
# objecttypen Jobs: Open Zaak and Objecttypen still being configured (known issue podiumd-chart-31);
# ZAC waits for the first.
# OpenBao's schema and config, Open VTB's and Open Beheer's config: Postgres or OpenBao still starting.
RETRIED_CONTAINERS = {
    ("Job", "openbeheer-config", "openbeheer"),
    ("Job", "openbao-db-schema", "schema"),
    ("Job", "openbao-config", "config"),
    ("Job", "openvtb-config", "openvtb"),
    ("Deployment", "zac", ZAC_SOLR_INIT),
    ("Job", KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_USER),
    ("Deployment", "openinwoner", OPEN_INWONER_SEARCH_INDEX),
    ("Job", "create-required-catalogi-job", "create-required-catalogi"),
    ("Job", "create-required-objecttypen-job", "create-required-objecttypen"),
}
RETRY_ATTEMPTS = 150


def retry_in_container(docs: list[Doc]) -> None:
    """Makes RETRIED_CONTAINERS rerun their command every 2 s, RETRY_ATTEMPTS times, instead of failing."""
    retry = f'i=0; until "$@"; do i=$((i + 1)); [ "$i" -ge {RETRY_ATTEMPTS} ] && exit 1; sleep 2; done'
    for doc in docs:
        for container in _containers(_pod_spec(doc) or {}):
            if (doc.get("kind"), name_of(doc), container.get("name")) not in RETRIED_CONTAINERS:
                continue
            # A process of its own: an until condition would turn off a script's `set -e`.
            argv: list[str] = [*(container.get("command") or []), *(container.pop("args", None) or [])]
            container["command"] = ["sh", "-c", retry, "retry", *argv]


def tune_solr(docs: list[Doc]) -> None:
    """Sets the JVM settings and CAPACITY_ENV of the pods the solr-operator builds from ZAC's SolrCloud."""
    for doc in docs:
        if doc.get("kind") != "SolrCloud":
            continue
        body = section(doc, "spec")
        add_env(_mapping(body, "customSolrKubeOptions", "podOptions"), SOLR_ENV | CAPACITY_ENV, "envVars")
        add_env(_mapping(body, "zookeeperRef", "provided", "zookeeperPodPolicy"), ZOOKEEPER_ENV | CAPACITY_ENV)


# Start-up only: while every pod starts after a node start, the probes killed ZooKeeper (liveness
# from 10 s, 500m) and Solr (startup probe `solr api`, a JVM, 5 s timeout, 60 s budget). ZooKeeper:
# the operator's probes, liveness from 120 s; set together, as the operator takes 0 for an unset
# field. Solr: the operator's startup probe with a 15 s timeout and a 300 s budget.
_ZOOKEEPER_PROBE = {"periodSeconds": 10, "failureThreshold": 3, "successThreshold": 1, "timeoutSeconds": 10}
ZOOKEEPER_PROBES = {
    "livenessProbe": {**_ZOOKEEPER_PROBE, "initialDelaySeconds": 120},
    "readinessProbe": {**_ZOOKEEPER_PROBE, "initialDelaySeconds": 10},
}
SOLR_STARTUP_PROBE = {"timeoutSeconds": 15, "failureThreshold": 60}
# Solr fails to start ("NodeExistsException", CoreContainer shut down; its liveness probe restarts it
# 3 minutes later) while its live_nodes entry from before a node stop still exists: ZooKeeper expires
# that session about 30 s after it is up. This init container waits up to 90 s for the entry to go.
SOLR_LIVE_NODE_WAIT = """\
node="$POD_NAME.{headless}:8983_solr"
i=0
while [ "$i" -lt 45 ]; do
  nodes=$(solr zk ls /live_nodes -z "$ZK_HOST" 2>&1) && ! printf '%s\\n' "$nodes" | grep -qx "$node" && exit 0
  printf '%s\\n' "$nodes" | grep -q NoNode && exit 0
  i=$((i + 1)); sleep 2
done
echo "$node is still in live_nodes; starting Solr anyway"
"""


def _solr_live_node_wait(solr_cloud: str, body: Doc) -> Doc:
    image = section(body, "solrImage")
    return {
        "name": "wait-for-stale-live-node",
        "image": f"{image.get('repository')}:{image.get('tag')}",
        "command": ["sh", "-c", SOLR_LIVE_NODE_WAIT.format(headless=f"{solr_cloud}-solrcloud-headless.{NAMESPACE}")],
        "env": [
            {"name": "POD_NAME", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
            {"name": "ZK_HOST", "value": f"{solr_cloud}-solrcloud-zookeeper-client:2181"},
        ],
        "resources": {"requests": {"cpu": "10m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}},
    }


def ease_solr_start(docs: list[Doc]) -> None:
    """Sets ZOOKEEPER_PROBES, SOLR_STARTUP_PROBE and the SOLR_LIVE_NODE_WAIT init container in ZAC's SolrCloud."""
    for doc in docs:
        if doc.get("kind") != "SolrCloud":
            continue
        body = section(doc, "spec")
        _mapping(body, "zookeeperRef", "provided")["probes"] = ZOOKEEPER_PROBES
        pod_options = _mapping(body, "customSolrKubeOptions", "podOptions")
        _mapping(pod_options, "startupProbe").update(SOLR_STARTUP_PROBE)
        pod_options["initContainers"] = [
            *(pod_options.get("initContainers") or []),
            _solr_live_node_wait(name_of(doc), body),
        ]


ZAAKBRUG_ROLE_MAPPING = "zaakbrug-oauth-role-mapping"
_ZAAKBRUG_RESOURCES = "/opt/frank/resources"


def mount_zaakbrug_role_mapping(docs: list[Doc]) -> None:
    """Mounts podiumd's ConfigMap ZAAKBRUG_ROLE_MAPPING into ZaakBrug, which Frank needs at startup.

    The zaakbrug chart has no extraVolumes; ExternalsPodiumD mounts it with a
    post-deploy `kubectl patch` (podiumd's zaakbrug-deploy.md, step 5c).
    """
    if not any(doc.get("kind") == "ConfigMap" and name_of(doc) == ZAAKBRUG_ROLE_MAPPING for doc in docs):
        return
    for doc in docs:
        spec = _pod_spec(doc)
        if doc.get("kind") != "Deployment" or name_of(doc) != "zaakbrug" or spec is None:
            continue
        volumes: list[Doc] = spec.get("volumes") or []
        spec["volumes"] = [*volumes, {"name": ZAAKBRUG_ROLE_MAPPING, "configMap": {"name": ZAAKBRUG_ROLE_MAPPING}}]
        for container in _containers(spec):
            mounts: list[Doc] = container.get("volumeMounts") or []
            container["volumeMounts"] = mounts + [
                {"name": ZAAKBRUG_ROLE_MAPPING, "mountPath": f"{_ZAAKBRUG_RESOURCES}/{key}", "subPath": key}
                for key in ("oauth-role-mapping.properties", "RoutingProfiles.json")
            ]


PROMETHEUS_POD = {"app.kubernetes.io/name": "prometheus"}


def scrape_only_from_prometheus(docs: list[Doc]) -> None:
    """Narrows the chart's NetworkPolicy rules for networkPolicies.monitoringNamespace to the Prometheus pod.

    The estates scrape from a namespace of their own; here Prometheus shares
    NAMESPACE, which the rule would open whole (Solr and OpenBao serve metrics
    on their main ports).
    """
    for doc in docs:
        if doc.get("kind") != "NetworkPolicy":
            continue
        rules: list[Doc] = section(doc, "spec").get("ingress") or []
        for rule in rules:
            peers: list[Doc] = rule.get("from") or []
            for peer in peers:
                labels: Doc = section(peer, "namespaceSelector").get("matchLabels") or {}
                if labels == {"kubernetes.io/metadata.name": NAMESPACE} and "podSelector" not in peer:
                    peer["podSelector"] = {"matchLabels": dict(PROMETHEUS_POD)}


def limit_runtimes(docs: list[Doc]) -> None:
    """Adds CAPACITY_ENV to every container of every workload, Job and operator pod template; GOMEMLIMIT where limited.

    Without GOMEMLIMIT the Go runtime only collects when its heap has doubled
    and runs into the OOM killer under a peak (Tempo, at 256Mi); the value
    comes from the container's own limit through the downward API, without
    containerName: the Keycloak operator copies the env into its update Job's
    containers, which have other names.
    """
    for doc in docs:
        for container in [c for spec in _runtime_pod_specs(doc) for c in _containers(spec)]:
            add_env(container, CAPACITY_ENV)
            if section(section(container, "resources"), "limits").get("memory"):
                limit = {"resourceFieldRef": {"resource": "limits.memory"}}
                _add_env_from(container, {GO_MEMORY_LIMIT: limit})


def _containers(spec: Doc) -> list[Doc]:
    return [*(spec.get("initContainers") or []), *(spec.get("containers") or [])]


def add_env(container: Doc, values: dict[str, str], key: str = "env") -> None:
    """Appends values to container's env (or another list under key); a variable already set wins."""
    _merge_env(container, [{"name": name, "value": value} for name, value in values.items()], key)


def _add_env_from(container: Doc, sources: dict[str, Doc]) -> None:
    """Appends variables taken from valueFrom sources; a variable the container already sets wins."""
    _merge_env(container, [{"name": name, "valueFrom": source} for name, source in sources.items()])


def _merge_env(container: Doc, items: list[Doc], key: str = "env") -> None:
    env: list[Doc] = container.get(key) or []
    container[key] = env
    present = {item.get("name") for item in env}
    env.extend(item for item in items if item["name"] not in present)


def _is_test_or_delete_hook(doc: Doc) -> bool:
    """Helm test and delete hooks: `kubectl apply` would run them at once (redis-ha-pre-delete deletes Redis)."""
    hooks = str(section(section(doc, "metadata"), "annotations").get("helm.sh/hook") or "").split(",")
    return any(hook.strip().startswith("test") or hook.strip().endswith("-delete") for hook in hooks)


def _configuration_yaml(doc: Doc, configmap: str) -> Any | None:
    if not _is_configmap(doc, configmap):
        return None
    raw = section(doc, "data").get("configuration.yaml")
    return yaml.safe_load(raw) if raw else None


# podiumd's values.yaml example item; the env var is from podiumd.objecten.configuration.secrets.
REQUIRED_OBJECTTYPEN_TOKEN: Doc = {
    "identifier": "create-required-objecttypen",
    "token": {"value_from": {"env": "create_required_objecttypen_token"}},
    "is_superuser": True,
    "contact_person": "podiumd",
    "email": "podiumd@example.com",
    "organization": "podiumd",
    "application": "create-required-objecttypen",
    "administration": "podiumd",
}


# The Objecttypen service of each client -> the token Open Object knows for that client:
# merged, one token table serves both APIs (helm-charts #627).
MERGED_OBJECTTYPEN_TOKENS = {
    "openformulieren-configuration": ("objecttypen-api", "objectenFormulierToken"),
    "openbeheer-configuration": ("objecttypen-service", "objectenOpenbeheerToken"),
}
# objecten's own Objecttypen service: its header's token is added to Open Object's tokens.
OBJECTEN_SELF_SERVICE = "objecttypen-api"


# podiumd<4.10: values.yaml configures the classic shape; this adapts it to Open Object.
def fixup_merged_objecten(docs: list[Doc]) -> None:
    """Merged objecten shape only: removes what only the classic shape accepts.

    objecten's setup_configuration rejects objecttypes service_identifier
    (Pydantic extra_forbidden), and the clients' classic objecttypen tokens
    only exist in classic objecttypen's token table. podiumd's
    create-required-objecttypen Job needs a superuser token on objecten.
    """
    for doc in docs:
        objecten = _configuration_yaml(doc, "objecten-configuration")
        if objecten is not None:
            for item in objecten.get("objecttypes", {}).get("items", []):
                item.pop("service_identifier", None)
            objecten["tokenauth"]["items"].append(REQUIRED_OBJECTTYPEN_TOKEN)
            for service in objecten.get("zgw_consumers", {}).get("services", []):
                if service.get("identifier") == OBJECTEN_SELF_SERVICE:
                    objecten["tokenauth"]["items"].append({
                        "identifier": "objecten-objecttypen",
                        "token": str(service["header_value"]).removeprefix("Token "),
                        "contact_person": "podiumd-minikube",
                        "email": "objecten@example.com",
                    })  # fmt: skip
            doc["data"]["configuration.yaml"] = yaml.safe_dump(objecten, sort_keys=False)
        for configmap, (identifier, token) in MERGED_OBJECTTYPEN_TOKENS.items():
            client = _configuration_yaml(doc, configmap)
            if client is None:
                continue
            for service in client.get("zgw_consumers", {}).get("services", []):
                if service.get("identifier") == identifier:
                    service["header_value"] = f"Token {token}"
            doc["data"]["configuration.yaml"] = yaml.safe_dump(client, sort_keys=False)


def _is_large_configmap(doc: Doc) -> bool:
    return doc.get("kind") == "ConfigMap" and len(yaml.dump(doc, Dumper=_DUMPER)) > LARGE_CONFIGMAP_BYTES


def dump(docs: list[Doc]) -> str:
    """docs as a multi-document YAML stream."""
    return yaml.dump_all(docs, Dumper=_DUMPER, default_flow_style=False, sort_keys=False)


@dataclass(frozen=True)
class Render:
    """A fixed-up render: the manifest for `kubectl apply`, plus what it cannot carry.

    Large ConfigMaps (monitoring-logging's Grafana dashboards) and CRDs
    (ECK's) exceed client-side apply's annotation limit and go server-side;
    CRDs also have to be established before the resources that use them.
    """

    docs: list[Doc]
    large_configmaps: list[Doc]
    crds: list[Doc] = field(default_factory=list[Doc])
    # "Kind/name" -> the dependency that renders it, or this chart.
    charts: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def jobs(self) -> list[str]:
        """Names of the Jobs in docs (all idempotent)."""
        return [name_of(doc) for doc in self.docs if doc.get("kind") == "Job"]

    @property
    def manifest(self) -> str:
        """docs as YAML."""
        return dump(self.docs)


def _chunks(text: str) -> list[str]:
    """The YAML documents of rendered text, image digests stripped."""
    return re.split(r"^---$", strip_image_digests(text), flags=re.MULTILINE)


def fix_up(text: str, *, objecten_merged: bool, ca_trust: bool = False) -> Render:
    """Applies every local fixup to `helm template` output."""
    loaded: list[tuple[Doc | None, re.Match[str] | None]] = [(_load(c), _SOURCE.search(c)) for c in _chunks(text)]
    charts = {f"{doc.get('kind')}/{name_of(doc)}": source[2] or source[1] for doc, source in loaded if doc and source}
    docs = [doc for doc, _ in loaded if doc and not _is_test_or_delete_hook(doc) and not is_unused_zac_collector(doc)]
    disable_service_links(docs)
    allow_rolling_updates(docs)
    drop_duplicate_env(docs)
    route_outbound(docs)
    limit_nginx_workers(docs)
    route_through_nginx(docs)
    keep_finished_jobs(docs)
    mark_release_owner(docs)
    tune_keycloak(docs)
    speed_up_keycloak_start(docs)
    tune_solr(docs)
    ease_solr_start(docs)
    mount_zaakbrug_role_mapping(docs)
    scrape_only_from_prometheus(docs)
    docs = post_render(docs, objecten_merged=objecten_merged, ca_trust=ca_trust)
    return Render(
        docs=[doc for doc in docs if not _is_large_configmap(doc) and doc.get("kind") != "CustomResourceDefinition"],
        large_configmaps=[doc for doc in docs if _is_large_configmap(doc)],
        crds=[doc for doc in docs if doc.get("kind") == "CustomResourceDefinition"],
        charts=charts,
    )


def post_render(docs: list[Doc], *, objecten_merged: bool, ca_trust: bool) -> list[Doc]:
    """The fixups every object needs before it is created: Jobs are immutable, pods read them at their start."""
    tune_kcadm(docs)
    retry_in_container(docs)
    limit_runtimes(docs)
    if ca_trust:
        trust_ca(docs)
        docs = serve_postgres_tls(docs)
    if objecten_merged:
        fixup_merged_objecten(docs)
    return docs


def post_render_text(text: str, *, objecten_merged: bool, ca_trust: bool) -> str:
    """post_render on a rendered manifest, image digests stripped: the Helm post-renderer plugin's work."""
    docs = [doc for doc in map(_load, _chunks(text)) if doc]
    return dump(post_render(docs, objecten_merged=objecten_merged, ca_trust=ca_trust))


def helm_template(*args: str) -> str:
    """Raw `helm template` of this chart, with the subcharts' crds/ (the Keycloak operator's)."""
    return process.output(["helm", "template", RELEASE_NAME, str(CHART_DIR), "-n", NAMESPACE, "--include-crds", *args])
