"""KISS's crawler index-template hook: deploy runs it only once Elasticsearch admits it (known issue kiss-3).

The pre-install,pre-upgrade hook PUTs to Elasticsearch before Helm creates or updates anything: it fails on a first
install and in the deploy that first adds its NetworkPolicy peer (helm-charts #690).
"""

from urllib.parse import urlparse

from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube.objects import Doc
from podiumd_minikube.paths import NAMESPACE

WEBSITE_OFF = ["--set", "podiumd.kiss.settings.syncJobs.website=null"]
_HOOK_SUFFIX = "-crawler-index-template"


def _is_admitting(policy: Doc, peer: Doc) -> bool:
    rules: list[Doc] = manifests.section(policy, "spec").get("ingress") or []
    return any(peer in (rule.get("from") or []) for rule in rules)


def website_hook_blocked(podiumd: manifests.Render) -> str:
    """Why the crawler index-template hook in podiumd's render cannot reach Elasticsearch now; "" when it can."""
    hook = next((name for name in podiumd.jobs if name.endswith(_HOOK_SUFFIX)), None)
    if hook is None:
        return ""
    template = (podiumd.named("Job", hook) or {})["spec"]["template"]
    env: list[Doc] = template["spec"]["containers"][0].get("env", [])
    url: str = next(item["value"] for item in env if item["name"] == "ELASTIC_BASE_URL")
    service = (urlparse(url).hostname or "").split(".")[0]
    if not kube.is_present(f"service/{service}"):
        return f"Service {service} does not exist yet"
    peer = {"podSelector": {"matchLabels": {"batch.kubernetes.io/job-name": hook}}}
    policies = [doc for doc in podiumd.docs if doc.get("kind") == "NetworkPolicy" and _is_admitting(doc, peer)]
    return next(
        (
            f"NetworkPolicy {name} does not admit it yet"
            for name in map(manifests.name_of, policies)
            if not _is_live_admitting(name, peer)
        ),
        "",
    )


def _is_live_admitting(name: str, peer: Doc) -> bool:
    """Whether the NetworkPolicy called name exists in the cluster and admits peer."""
    return kube.is_present(f"networkpolicy/{name}") and _is_admitting(
        kube.get_json("networkpolicy", name, "-n", NAMESPACE), peer
    )
