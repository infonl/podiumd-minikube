"""
Pod health checks - the first thing checked live after every deploy in this
project: every pod should be Running (and all its containers Ready), or for
one-shot Jobs, Succeeded.
"""

import pytest

ONE_SHOT_JOB_PREFIXES = (
    "pabc-migrations",
    "opa-tests",
    "storage-permissions-fix",
    "openarchiefbeheer-config",
)


def is_one_shot(name):
    return any(name == prefix or name.startswith(prefix + "-") for prefix in ONE_SHOT_JOB_PREFIXES)


def test_no_pods_in_bad_phase(pods):
    bad = [p for p in pods if p["phase"] not in ("Running", "Succeeded")]
    assert not bad, f"pods not Running/Succeeded: {[p['name'] for p in bad]}"


def test_long_running_pods_are_ready(pods):
    """
    Every container in every non-Job pod should report ready=true - catches
    a pod stuck at e.g. 1/2 Ready (a sidecar failing its own readiness
    probe) that "phase: Running" alone wouldn't catch.
    """
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
        "postgres",
        "redis",
        "keycloak",
        "solr",
        "wiremock",
        "brp-personen-mock",
        "openzaak",
        "openklant",
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
