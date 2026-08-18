"""
Confirms every zgw_consumers.Service row seeded into any app's own
database (both the classic and merged/openobject fixture shapes - see
scripts/lib/seed-fixtures.sh) actually has a reachable api_root, from
inside the cluster - the same place that app itself makes requests to it.

Exists because both vendored fixtures (objecten/demodata.json and
openobject/demodata.json) previously seeded stale docker-compose-era
values - a nonexistent hostname, and a port the target Service never
listens on (see values.yaml's Service definitions: only `port: 80` is
declared, `targetPort` forwards internally to each app's real 8000, so
port 8000 is never reachable through the Service itself) - that
`loaddata` accepted without complaint. Fixture rows load successfully
regardless of whether the URL inside them resolves to anything; this is
the regression check for that class of bug going unnoticed again.

Covers every app that registers its own zgw_consumers.Service rows via
values.yaml's `configuration.data` mechanism, not just objecten - found
live that openformulieren's own `objecttypes-api` entry
(values.yaml's podiumd.openformulieren.configuration.data) points at the
exact same "objecttypen" hostname objecten's own `objecttypen-api` entry
does, so a DNS-name regression there (e.g. the merged-shape
"objecttypen" Service dropping out - see
templates/objecten/service-objecttypen-alias.yaml) could go unnoticed if
only objecten's own rows were ever checked. opennotificaties and
openarchiefbeheer also register zgw_consumers rows but only reference
openzaak - included anyway for completeness/symmetry, and to catch any
*future* app config pointing at objecttypen too.

Queries each app's live database rather than re-reading the fixture file,
so this also catches drift from a direct django-admin edit, not just a
stale fixture (confirmed live during this project's own build history
that django-admin edits are common here - see plan.md).

Reachability itself is checked via `kubectl exec` into each app's own pod
and a plain `urllib.request` call, not `requests`/`curl` from the test
runner: every api_root here is an in-cluster Service DNS name
(e.g. http://opennotificaties:80/...), unresolvable and unroutable from
outside the cluster - and `curl` isn't installed in these images (confirmed
live), which is why urllib is used instead.
"""

import json
from urllib.parse import urlparse

import pytest

from conftest import NAMESPACE, kubectl

# Every app that registers its own zgw_consumers.Service rows via
# values.yaml's configuration.data mechanism (grep
# "zgw_consumers_config_enable" in values.yaml for the full set) - each
# one's manage.py lives at the same /app/src/manage.py path (confirmed
# live for all four), so one generic check covers all of them.
APPS = ["objecten", "opennotificaties", "openarchiefbeheer", "openformulieren"]


def _is_in_cluster_hostname(api_root):
    """
    True for this chart's own in-cluster Service DNS forms - a bare
    single-label name ("openzaak") or that label's own
    "<name>.podiumd-minikube" namespace-qualified form (see values.yaml's
    own top-of-file naming-convention comment, and its objecttypen-api
    entry's own URLValidator-workaround comment for why the qualified form
    exists at all). False for a real internet hostname (has a real
    registrable domain/TLD, e.g. "selectielijst.openzaak.nl" or
    "autorisaties-api.vng.cloud") - those are genuinely external reference
    APIs this offline minikube box was never going to reach regardless of
    any in-cluster DNS wiring, found live expanding this test beyond
    objecten's own rows (see plan.md) - reachability isn't the property
    being regression-tested for those, so they're skipped rather than
    failed.
    """
    host = urlparse(api_root).hostname or ""
    labels = host.split(".")
    return len(labels) == 1 or (len(labels) == 2 and labels[1] == "podiumd-minikube")


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
    """
    Every zgw_consumers.Service row actually in this app's live database,
    as [{"slug": ..., "api_root": ...}, ...]. `manage.py shell` can print
    warnings (e.g. the OTEL_SERVICE_NAME one seen live) before the actual
    output - the JSON is always the last line of stdout, so only that
    line is parsed.
    """
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
    """Sanity check the fixture actually loaded - an empty result here
    means the reachability test below would trivially pass on nothing."""
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
            # A real HTTP error response (401/403/404/...) still proves
            # something is actually listening - only 5xx/no-response at
            # all counts as unreachable.
            "    print('OK' if e.code < 500 else f'FAIL http {e.code}')\n"
            "except Exception as e:\n"
            "    print(f'FAIL {e}')\n",
        ).strip().splitlines()[-1]
        if not outcome.startswith("OK"):
            failures.append(f"{slug} ({api_root}): {outcome}")
    assert not failures, (
        f"unreachable zgw_consumers.Service api_root(s) for '{app}':\n" + "\n".join(failures)
    )
