"""What a `helm upgrade` of the chart meets that this project's `kubectl apply` deploy does not."""

import re

from podiumd_minikube import manifests

# ExternalsPodiumD pipelines/application.yml, step "Delete stale one-shot Jobs":
# deleted before each `helm upgrade`, since a Job's pod template is immutable.
EXTERNALS_DELETED_JOBS = frozenset({
    "create-required-catalogi-job", "create-required-objecttypen-job", "ensure-keycloak-operator-sa",
    "ensure-podiumd-admin-user", "import-master-realm-job", "objecten-config", "objecttypen-config",
    "openarchiefbeheer-config", "openformulieren-config", "openinwoner-config", "openklant-config",
    "opennotificaties-config", "openzaak-config",
})  # fmt: skip
# A content hash or .Release.Revision suffix gives each change a new Job.
_VARYING_NAME = re.compile(r"-(?:[0-9a-f]{10}|\d+)$")
_OURS = "podiumd-minikube"


def undeleted_fixed_jobs(docs: list[manifests.Doc]) -> list[str]:
    """The chart's non-hook Jobs with a fixed name that ExternalsPodiumD does not delete before `helm upgrade`."""
    return sorted(
        name
        for doc in docs
        if doc.get("kind") == "Job"
        and not manifests.section(doc, "metadata").get("annotations", {}).get("helm.sh/hook")
        and manifests.section(doc, "metadata").get("labels", {}).get("app.kubernetes.io/part-of") != _OURS
        and not _VARYING_NAME.search(name := manifests.name_of(doc))
        and name not in EXTERNALS_DELETED_JOBS
    )
