"""
Pod health checks - adapted from ../tests/test_pods.py for johnb00's real
pod set (checked live via `kubectl get pods -n podiumd`).

core_pod_prefix differences from the minikube reference:
  - "postgres"/"solr" dropped: johnb00 has no in-cluster Postgres (external
    Azure Flexible Server) and no bare "solr" pod (only zac's own bundled
    "zac-solr-solrcloud-*", which isn't a standalone core service the way
    minikube's shared solr pod is).
  - "wiremock" dropped: not deployed on johnb00.
  - "mailpit" kept: deployed this session (values/johnb00/mailpit.yaml).
  - "redis" -> "redis-ha": johnb00 uses the shared redis-ha subchart.

The `image-prepull` DaemonSet previously crash-looped on every node: its
generic init-container command (`sh -c "exit 0"`, just there to trigger
imagePullPolicy: IfNotPresent) assumed every image has a shell, which several
Go-binary/operator images (openpolicyagent/opa, pravega/zookeeper-operator,
opstree/redis-operator) don't ship. Fixed in
podiumd-infra/scripts/setup-image-prepull.sh (per-image command override
table) rather than special-cased away here - this suite was correctly
catching a real (now-fixed) infra bug, not a suite-adaptation issue.
"""

import pytest

ONE_SHOT_JOB_PREFIXES = (
    "pabc-migrations",
    "podiumd-realm-import",
    "keycloak-realm-import",
    # redis-ha's own periodic CronJob that re-labels the current master -
    # confirmed live: short-lived, already gone (TTL'd) by the time a
    # second `kubectl get pods` was run moments later.
    "redis-ha-label-master",
    # The chart's own bundled setup_configuration/bootstrap Jobs (see each
    # app's own podiumd.<app>.configuration.data comment in podiumd.yaml),
    # plus podiumd-infra's own post-deployment-pabc-init Job - all one-shot,
    # left as Completed pods by design.
    "create-required-catalogi-job",
    "create-required-objecttypen-job",
    "ensure-keycloak-operator-sa",
    "ensure-podiumd-admin-user",
    "import-master-realm-job",
    "import-podiumd-realm-job",
    "objecten-config",
    "objecttypen-config",
    "openbeheer-config",
    "openformulieren-config",
    "openklant-config",
    "opennotificaties-config",
    "openzaak-config",
    "post-deployment-pabc-init",
    "referentielijsten-config",
)


def is_one_shot(name):
    return any(name == prefix or name.startswith(prefix + "-") for prefix in ONE_SHOT_JOB_PREFIXES)


def test_no_pods_in_bad_phase(pods):
    bad = [p for p in pods if p["phase"] not in ("Running", "Succeeded")]
    assert not bad, f"pods not Running/Succeeded: {[p['name'] for p in bad]}"


def test_long_running_pods_are_ready(pods):
    not_ready = []
    for pod in pods:
        if is_one_shot(pod["name"]):
            continue
        for status in pod["container_statuses"]:
            if not status.get("ready", False):
                not_ready.append(f"{pod['name']}/{status['name']}")
    assert not not_ready, f"containers not ready: {not_ready}"


@pytest.mark.parametrize(
    "core_pod_prefix",
    [
        "redis-ha",
        "keycloak",
        "mailpit",
        "brp-personen-mock",
        "openzaak",
        "openzaak-worker",
        "openklant",
        "openklant-worker",
        "pabc",
        "zac",
        "zac-office-converter",
    ],
)
def test_core_profile_pod_present(pods, core_pod_prefix):
    """The always-on core stack should be present regardless of which
    optional profiles are also enabled."""
    names = {p["name"] for p in pods}
    assert any(
        n == core_pod_prefix or n.startswith(core_pod_prefix + "-") for n in names
    ), f"no pod found matching '{core_pod_prefix}'"
