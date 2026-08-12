"""
Confirms every zgw_consumers.Service row seeded into the objecten pod's
database (both the classic and merged/openobject fixture shapes - see
scripts/lib/seed-fixtures.sh) actually has a reachable api_root, from
inside the cluster - the same place the objecten app itself makes requests
to it.

Exists because both vendored fixtures (objecten/demodata.json and
openobject/demodata.json) previously seeded stale docker-compose-era
values - a nonexistent hostname, and a port the target Service never
listens on (see values.yaml's Service definitions: only `port: 80` is
declared, `targetPort` forwards internally to each app's real 8000, so
port 8000 is never reachable through the Service itself) - that
`loaddata` accepted without complaint. Fixture rows load successfully
regardless of whether the URL inside them resolves to anything; this is
the regression check for that class of bug going unnoticed again.

Queries the live database rather than re-reading the fixture file, so
this also catches drift from a direct django-admin edit, not just a
stale fixture (confirmed live during this project's own build history
that django-admin edits are common here - see plan.md).

Reachability itself is checked via `kubectl exec` into the objecten pod
and a plain `urllib.request` call, not `requests`/`curl` from the test
runner: every api_root here is an in-cluster Service DNS name
(e.g. http://opennotificaties:80/...), unresolvable and unroutable from
outside the cluster - and `curl` isn't installed in this image (confirmed
live), which is why urllib is used instead.
"""

import json

import pytest

from conftest import NAMESPACE, kubectl


def _objecten_pod_name():
    return kubectl(
        "get",
        "pod",
        "-n",
        NAMESPACE,
        "-l",
        "app.kubernetes.io/name=objecten",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    ).strip()


@pytest.fixture(scope="module")
def objecten_pod(enabled_profiles):
    if not enabled_profiles.get("objecten"):
        pytest.skip("'objecten' profile is not deployed")
    pod = _objecten_pod_name()
    if not pod:
        pytest.skip("no 'objecten' pod found")
    return pod


@pytest.fixture(scope="module")
def zgw_services(objecten_pod):
    """
    Every zgw_consumers.Service row actually in the live database, as
    [{"slug": ..., "api_root": ...}, ...]. `manage.py shell` can print
    warnings (e.g. the OTEL_SERVICE_NAME one seen live) before the actual
    output - the JSON is always the last line of stdout, so only that
    line is parsed.
    """
    raw = kubectl(
        "exec",
        "-n",
        NAMESPACE,
        objecten_pod,
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


def test_zgw_services_seeded(zgw_services):
    """Sanity check the fixture actually loaded - an empty result here
    means the reachability test below would trivially pass on nothing."""
    assert zgw_services, "no zgw_consumers.Service rows found - fixture not seeded?"


def test_zgw_service_api_roots_reachable(objecten_pod, zgw_services):
    failures = []
    for service in zgw_services:
        slug, api_root = service["slug"], service["api_root"]
        outcome = kubectl(
            "exec",
            "-n",
            NAMESPACE,
            objecten_pod,
            "--",
            "python",
            "-c",
            "import urllib.request, urllib.error\n"
            f"url = {api_root!r}\n"
            "try:\n"
            "    urllib.request.urlopen(url, timeout=5)\n"
            "    print('OK')\n"
            "except urllib.error.HTTPError as e:\n"
            # A real HTTP error response (401/403/404/...) still proves
            # something is actually listening - only 5xx/no-response at
            # all counts as unreachable.
            "    print('OK' if e.code < 500 else f'FAIL http {e.code}')\n"
            "except Exception as e:\n"
            "    print(f'FAIL {e}')\n",
        ).strip().splitlines()[-1]
        if not outcome.startswith("OK"):
            failures.append(f"{slug} ({api_root}): {outcome}")
    assert not failures, "unreachable zgw_consumers.Service api_root(s):\n" + "\n".join(
        failures
    )
