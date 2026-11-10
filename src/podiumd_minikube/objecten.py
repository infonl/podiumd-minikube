"""Objecten before podiumd's upgrade to Open Object (4.10): the objecttypes and tokens it must hold first.

As podiumd-infra's objecten.import_objecttypes and the chart's 4.9.5-to-4.10.0 upgrade guide: Open Object 4 refuses
to start ("Upgrading from 3.6.2 to 4.3.0 is not possible") until Objects API 3.6.1+ has imported the objecttypes.
"""

import tempfile

from pathlib import Path

from podiumd_minikube import dependency
from podiumd_minikube import kube
from podiumd_minikube import manifests
from podiumd_minikube import process
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import PROFILE

# podiumd-infra's IMPORT: every service an objecttype points at, then fails unless all are imported.
IMPORT = """\
from django.core.management import call_command
from objects.core.models import ObjectType
for slug in sorted(set(ObjectType.objects.values_list("service__slug", flat=True)) - {None}):
    call_command("import_objecttypes", slug)
bad = [str(uuid) for uuid in ObjectType.objects.exclude(is_imported=True).values_list("uuid", flat=True)]
print("OK" if not bad else "NOT IMPORTED: " + ", ".join(bad))
raise SystemExit(1 if bad else 0)
"""
TOKENS = "migrate-objecttypen-tokens.sh"
_OBJECTTYPEN = "Deployment/objecttypen"
_OBJECTEN = "Deployment/objecten"


def merging(installed: manifests.Render, podiumd: manifests.Render) -> bool:
    """Whether podiumd's upgrade drops the Deployment objecttypen installed beside objecten (podiumd<4.10 to 4.10+)."""
    before = {manifests.key_of(doc) for doc in installed.docs}
    after = {manifests.key_of(doc) for doc in podiumd.docs}
    return {_OBJECTTYPEN, _OBJECTEN} <= before and _OBJECTTYPEN not in after


def import_objecttypes() -> None:
    """Import the objecttypes, then the objecttypen-only tokens (podiumd's scripts/migrate-objecttypen-tokens.sh)."""
    print("\nImporting the objecttypes into objecten before the upgrade to Open Object...")
    shell = ["python", "src/manage.py", "shell", "-c", IMPORT]
    kube.kubectl_shown("exec", "deploy/objecten", "-c", "objecten", "-n", NAMESPACE, "--", *shell)
    with tempfile.TemporaryDirectory() as work:
        tokens = dependency.podiumd_script(TOKENS, Path(work))
        if not tokens.is_file():
            msg = f"the selected podiumd has no scripts/{TOKENS}: select podiumd 4.10.0 or later"
            raise process.UserError(msg)
        process.run(["bash", str(tokens), "--context", PROFILE, "--namespace", NAMESPACE, "--apply"], capture=False)
