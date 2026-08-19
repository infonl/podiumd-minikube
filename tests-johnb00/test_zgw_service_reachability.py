"""
Confirms every zgw_consumers.Service row seeded into each app's own
database has a reachable api_root, from inside the cluster - adapted from
../tests/test_zgw_service_reachability.py. Reachability is checked via
`kubectl exec` + a plain `urllib.request` call from inside each app's own
pod (same as the reference - these images have no `curl`), so this needs no
separate Postgres credential at all: `manage.py shell` uses the app's own
already-configured Django DB connection.

Only real adaptation needed vs. the reference: the in-cluster-hostname
namespace suffix (`podiumd`, not `podiumd-minikube`) and the pod name
lookup (label selector `app.kubernetes.io/name=<app>` was verified live to
still match johnb00's pods correctly even for opennotificaties, whose
Service/pod *name* is "notificaties" via fullnameOverride - the chart's own
`app.kubernetes.io/name` label is untouched by that override).
"""

import json
from urllib.parse import urlparse

import pytest

from conftest import NAMESPACE, kubectl

APPS = ["objecten", "opennotificaties", "openarchiefbeheer", "openformulieren"]


def _is_in_cluster_hostname(api_root):
    """True for this chart's own in-cluster Service DNS forms (a bare
    single-label name, or that label's own "<name>.podiumd" namespace-
    qualified form) - False for a real external reference API."""
    host = urlparse(api_root).hostname or ""
    labels = host.split(".")
    return len(labels) == 1 or (len(labels) == 2 and labels[1] == "podiumd")


def _pod_name(app):
    return kubectl(
        "get",
        "pod",
        "-n",
        NAMESPACE,
        "-l",
        f"app.kubernetes.io/name={app}",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ).strip()


@pytest.fixture(params=APPS)
def app_pod(request, enabled_profiles):
    app = request.param
    if not enabled_profiles.get(app):
        pytest.skip(f"'{app}' profile is not deployed")
    pod = _pod_name(app)
    if not pod:
        pytest.skip(f"no '{app}' pod found")
    return app, pod


@pytest.fixture
def zgw_services(app_pod):
    _, pod = app_pod
    raw = kubectl(
        "exec",
        "-n",
        NAMESPACE,
        pod,
        "--",
        "python",
        "/app/src/manage.py",
        "shell",
        "-c",
        "import json\n"
        "from zgw_consumers.models import Service\n"
        "print(json.dumps(list(Service.objects.values('slug', 'api_root'))))\n",
    )
    return json.loads(raw.strip().splitlines()[-1])


def test_zgw_services_seeded(app_pod, zgw_services):
    app, _ = app_pod
    assert zgw_services, f"no zgw_consumers.Service rows found for '{app}' - fixture not seeded?"


def test_zgw_service_api_roots_reachable(app_pod, zgw_services):
    app, pod = app_pod
    failures = []
    for service in zgw_services:
        slug, api_root = service["slug"], service["api_root"]
        if not _is_in_cluster_hostname(api_root):
            continue
        outcome = kubectl(
            "exec",
            "-n",
            NAMESPACE,
            pod,
            "--",
            "python",
            "-c",
            "import urllib.request, urllib.error\n"
            f"url = {api_root!r}\n"
            "try:\n"
            "    urllib.request.urlopen(url, timeout=5)\n"
            "    print('OK')\n"
            "except urllib.error.HTTPError as e:\n"
            "    print('OK' if e.code < 500 else f'FAIL http {e.code}')\n"
            "except Exception as e:\n"
            "    print(f'FAIL {e}')\n",
        ).strip().splitlines()[-1]
        if not outcome.startswith("OK"):
            failures.append(f"{slug} ({api_root}): {outcome}")
    assert not failures, (
        f"unreachable zgw_consumers.Service api_root(s) for '{app}':\n" + "\n".join(failures)
    )
