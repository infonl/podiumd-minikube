"""Local fixups applied to `helm template` output before it is applied.

deploy renders with `helm template | kubectl apply` instead of `helm install`:
Helm's release record embeds the whole podiumd dependency and exceeds the 3MB
API request limit. Without a release, Helm hooks never run and the render has
to be corrected here.
"""

import json
import re

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import cast

import yaml

from lib import pki
from lib import process
from lib.paths import CHART_DIR
from lib.paths import NAMESPACE
from lib.paths import RELEASE_NAME

Doc = dict[str, Any]

PABC_MIGRATION_JOB = "pabc-migrations-1"
# Creates objecttypes with the next primary keys, which the objecttypen
# fixtures (lib.seed, by primary key) would overwrite on a fresh cluster.
AFTER_SEED_JOBS = frozenset({"create-required-objecttypen-job"})
ZAC_UNUSED_OTEL_COLLECTOR = "zac-unused-otel-collector"
# The apps' BRP/KvK/BAG base URL (values.yaml), and Frank!Gateway's outway, which
# serves the same paths (podiumd.frankgateway.instances.outway.routes).
API_PROXY_URL = "http://api-proxy/"
OUTWAY_SERVICE = "frankgateway-outway"
OUTWAY_URL = f"http://{OUTWAY_SERVICE}:9080/"
# Headroom under the 262144-byte last-applied-configuration annotation of client-side apply.
LARGE_CONFIGMAP_BYTES = 200_000

_DIGEST_SUFFIX = re.compile(r"(:[^\s@]+)@sha256:[0-9a-f]{64}\b")
# [ \t], not \s: an `image:` key without a value must not take the next line.
_IMAGE_LINE = re.compile(r"""^[ \t]*image:[ \t]*"?([^"\s]+)""", re.MULTILINE)
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


def images(text: str) -> list[str]:
    """Sorted unique `image:` values in rendered text."""
    return sorted(set(_IMAGE_LINE.findall(text)))


def section(node: Doc, key: str) -> Doc:
    """node[key] when it is a mapping, else {} (rendered sections are often null)."""
    value = node.get(key)
    return cast("Doc", value) if isinstance(value, dict) else {}


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
    """Points ConfigMap values at OUTWAY_URL instead of API_PROXY_URL when the outway is rendered."""
    if not any(doc.get("kind") == "Service" and name_of(doc) == OUTWAY_SERVICE for doc in docs):
        return
    for doc in docs:
        if doc.get("kind") != "ConfigMap":
            continue
        data: Doc = doc.get("data") or {}
        for key, value in data.items():
            if isinstance(value, str) and API_PROXY_URL in value:
                data[key] = value.replace(API_PROXY_URL, OUTWAY_URL)


def ingress_hosts(docs: list[Doc]) -> list[str]:
    """Sorted unique hosts of the Ingresses in docs."""
    found: set[str] = set()
    for doc in docs:
        if doc.get("kind") != "Ingress":
            continue
        rules: list[Doc] = section(doc, "spec").get("rules") or []
        found.update(str(rule["host"]) for rule in rules if rule.get("host"))
    return sorted(found)


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


def trust_ca(docs: list[Doc]) -> None:
    """Mounts the local CA (pki.TRUST_CONFIGMAP) in every workload and Job, with its env.

    The reference environments call each other on public https hosts with
    publicly trusted certificates; here every client must trust the local
    CA. Jobs are recreated by every deploy (lib.deploy), so their immutable
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
        containers: list[Doc] = [*(spec.get("initContainers") or []), *(spec.get("containers") or [])]
        for container in containers:
            mounts: list[Doc] = container.get("volumeMounts") or []
            container["volumeMounts"] = mounts
            mounts.append({"name": pki.TRUST_CONFIGMAP, "mountPath": pki.TRUST_DIR, "readOnly": True})
            env: list[Doc] = container.get("env") or []
            container["env"] = env
            present = {item.get("name") for item in env}
            env.extend({"name": name, "value": value} for name, value in pki.TRUST_ENV.items() if name not in present)


def _is_test_hook(doc: Doc) -> bool:
    hook = section(section(doc, "metadata"), "annotations").get("helm.sh/hook")
    return "test" in str(hook or "")


def _excluded(doc: Doc) -> bool:
    """The pabc-migrations Job (only lib.pabc may create it), Helm test hooks, zac's bundled otel-collector."""
    name = name_of(doc)
    return (
        (doc.get("kind") == "Job" and name == PABC_MIGRATION_JOB)
        or _is_test_hook(doc)
        or name == ZAC_UNUSED_OTEL_COLLECTOR
    )


def _configuration_yaml(doc: Doc, configmap: str) -> Any | None:
    if not _is_configmap(doc, configmap):
        return None
    raw = section(doc, "data").get("configuration.yaml")
    return yaml.safe_load(raw) if raw else None


def fixup_merged_objecten(docs: list[Doc]) -> None:
    """Merged objecten shape only: removes what only the classic shape accepts.

    objecten's setup_configuration rejects objecttypes service_identifier
    (Pydantic extra_forbidden), and openformulieren's objecttypes-api token
    only exists in classic objecttypen's token table.
    """
    for doc in docs:
        objecten = _configuration_yaml(doc, "objecten-configuration")
        if objecten is not None:
            for item in objecten.get("objecttypes", {}).get("items", []):
                item.pop("service_identifier", None)
            doc["data"]["configuration.yaml"] = yaml.safe_dump(objecten, sort_keys=False)
        openformulieren = _configuration_yaml(doc, "openformulieren-configuration")
        if openformulieren is not None:
            for service in openformulieren.get("zgw_consumers", {}).get("services", []):
                if service.get("identifier") == "objecttypes-api":
                    service["header_value"] = "Token fakeOpenFormulierenObjectsToken"
            doc["data"]["configuration.yaml"] = yaml.safe_dump(openformulieren, sort_keys=False)


_LOCAL_HTTP = re.compile(r"http://[^/:]+\.local(/|$)")


def with_https(urls: list[str]) -> list[str]:
    """urls plus an https:// twin of every http://<host>.local one (HTTPS next to HTTP, as ExternalsPodiumD)."""
    twins = [f"https://{url.removeprefix('http://')}" for url in urls if _LOCAL_HTTP.match(url)]
    return [*urls, *(twin for twin in twins if twin not in urls)]


def fix_realm(docs: list[Doc], *, zac_pkce: bool) -> None:
    """Fixes the vendored realm: https redirect URIs and web origins, zaakafhandelcomponent's PKCE (S256 or "")."""
    for doc in docs:
        if not _is_configmap(doc, "keycloak-realm"):
            continue
        data = section(doc, "data")
        for key, raw in data.items():
            if not key.endswith("zaakafhandelcomponent-realm.json") or not raw:
                continue
            realm = json.loads(raw)
            for client in realm.get("clients", []):
                client["redirectUris"] = with_https(client.get("redirectUris", []))
                client["webOrigins"] = with_https(client.get("webOrigins", []))
                if client.get("clientId") == "zaakafhandelcomponent":
                    client["attributes"]["pkce.code.challenge.method"] = "S256" if zac_pkce else ""
            data[key] = json.dumps(realm, indent=2)


def _is_large_configmap(doc: Doc) -> bool:
    return doc.get("kind") == "ConfigMap" and len(yaml.safe_dump(doc)) > LARGE_CONFIGMAP_BYTES


def dump(docs: list[Doc]) -> str:
    """docs as a multi-document YAML stream."""
    return yaml.safe_dump_all(docs, default_flow_style=False, sort_keys=False)


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
    after_seed: list[Doc] = field(default_factory=list[Doc])

    @property
    def manifest(self) -> str:
        """docs as YAML."""
        return dump(self.docs)


def fix_up(text: str, *, objecten_merged: bool, zac_pkce: bool, ca_trust: bool = False) -> Render:
    """Applies every local fixup to `helm template` output."""
    loaded: list[Doc | None] = list(yaml.safe_load_all(strip_image_digests(text)))
    docs = [doc for doc in loaded if doc and not _excluded(doc)]
    disable_service_links(docs)
    route_outbound(docs)
    if ca_trust:
        trust_ca(docs)
    if objecten_merged:
        fixup_merged_objecten(docs)
    fix_realm(docs, zac_pkce=zac_pkce)
    after_seed = [doc for doc in docs if doc.get("kind") == "Job" and name_of(doc) in AFTER_SEED_JOBS]
    return Render(
        docs=[
            doc
            for doc in docs
            if not _is_large_configmap(doc) and doc.get("kind") != "CustomResourceDefinition" and doc not in after_seed
        ],
        large_configmaps=[doc for doc in docs if _is_large_configmap(doc)],
        crds=[doc for doc in docs if doc.get("kind") == "CustomResourceDefinition"],
        after_seed=after_seed,
    )


def helm_template(*args: str) -> str:
    """Raw `helm template` of this chart."""
    return process.output(["helm", "template", RELEASE_NAME, str(CHART_DIR), "-n", NAMESPACE, *args])
