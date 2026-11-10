"""What a `helm upgrade` of the chart meets that this project's `kubectl apply` deploy does not."""

import re

from podiumd_minikube import manifests
from podiumd_minikube.objects import Doc

# ExternalsPodiumD pipelines/application.yml, step "Delete stale one-shot Jobs":
# deleted before each `helm upgrade`, since a Job's pod template is immutable.
EXTERNALS_DELETED_JOBS = frozenset((
    "create-required-catalogi-job", "create-required-objecttypen-job", "ensure-keycloak-operator-sa",
    "ensure-podiumd-admin-user", "import-master-realm-job", "objecten-config", "objecttypen-config",
    "openarchiefbeheer-config", "openformulieren-config", "openinwoner-config", "openklant-config",
    "opennotificaties-config", "openzaak-config",
))  # fmt: skip
# A content hash or .Release.Revision suffix gives each change a new Job.
_VARYING_NAME = re.compile(r"-(?:[0-9a-f]{10}|\d+)$")
_OURS = "podiumd-minikube"


def undeleted_fixed_jobs(docs: list[Doc]) -> list[str]:
    """Return the chart's fixed-name non-hook Jobs ExternalsPodiumD does not delete before `helm upgrade`."""
    jobs = [doc for doc in docs if doc.get("kind") == "Job"]
    return sorted(manifests.name_of(job) for job in jobs if _fixed_upgraded_job(job))


def _fixed_upgraded_job(job: Doc) -> bool:
    """Whether job is no hook, not ours, has a fixed name and is not in EXTERNALS_DELETED_JOBS."""
    meta = manifests.section(job, "metadata")
    name = manifests.name_of(job)
    hook = meta.get("annotations", {}).get("helm.sh/hook")
    ours = meta.get("labels", {}).get("app.kubernetes.io/part-of") == _OURS
    return not hook and not ours and not _VARYING_NAME.search(name) and name not in EXTERNALS_DELETED_JOBS
