"""The api_roots values.yaml gives each app's zgw_consumers services answer from inside that app's pod.

Proves this project's wiring: in-cluster Service names, the *.local hosts
resolved to the edge (lib.dns) and the local CA trusted (manifests.trust_ca).
Read from each app's live database, so a django-admin edit is checked too.
"""

import json

from urllib.parse import urlparse

import pytest

from conftest import NAMESPACE
from conftest import kubectl

# Apps whose configuration.data in values.yaml registers zgw_consumers services.
APPS = ["objecten", "opennotificaties", "openarchiefbeheer", "openformulieren"]

PROBE = """import urllib.request, urllib.error
try:
    urllib.request.urlopen({url!r}, timeout=5)
    print('OK')
except urllib.error.HTTPError as e:
    print('OK' if e.code < 500 else f'FAIL http {{e.code}}')
except Exception as e:
    print(f'FAIL {{e}}')
"""


def _wired_here(api_root):
    """In-cluster names and *.local hosts; public APIs (selectielijst.openzaak.nl) are out of reach offline."""
    labels = (urlparse(api_root).hostname or "").split(".")
    return len(labels) == 1 or (len(labels) == 2 and labels[1] in ("podiumd-minikube", "local"))


def _last_line(*args):
    return kubectl(*args).strip().splitlines()[-1]


@pytest.mark.parametrize("app", APPS)
def test_zgw_service_api_roots_reachable(app, enabled_profiles):
    if not enabled_profiles.get(app):
        pytest.skip(f"'{app}' profile is not deployed")
    pod = kubectl(
        "get", "pod", "-n", NAMESPACE, "-l", f"app.kubernetes.io/name={app}", "-o", "jsonpath={.items[0].metadata.name}"
    ).strip()
    query = "import json\nfrom zgw_consumers.models import Service\nprint(json.dumps(list(Service.objects.values('slug', 'api_root'))))"
    services = json.loads(
        _last_line("exec", "-n", NAMESPACE, pod, "--", "python", "/app/src/manage.py", "shell", "-c", query)
    )
    assert services, f"no zgw_consumers services in '{app}': check its configuration Job"
    failures = [
        f"{s['slug']} ({s['api_root']}): {outcome}"
        for s in services
        if _wired_here(s["api_root"])
        and not (
            outcome := _last_line("exec", "-n", NAMESPACE, pod, "--", "python", "-c", PROBE.format(url=s["api_root"]))
        ).startswith("OK")
    ]
    assert not failures, f"unreachable api_roots in '{app}': " + "; ".join(failures)
