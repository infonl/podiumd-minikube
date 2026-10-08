"""Demo data that docker-compose loads with `manage.py loaddata` inside the running app.

Only objecten/objecttypen (or merged openobject) use that pattern; other apps
seed through Postgres init SQL or setup_configuration in values.yaml. Each
step is skipped when its model already has rows, so seeding runs on every
deploy.
"""

import json
import sys
import tempfile

from pathlib import Path
from typing import Any

from lib import kube
from lib import process
from lib.paths import NAMESPACE
from lib.paths import VENDOR_DIR
from lib.process import UserError

# objects-api 3.6.1/3.6.2 declare ObjectType.service without a migration for
# its column, so any ObjectType write fails (upstream bug, see plan.md).
KNOWN_OBJECTEN_BUG = 'column "service_id" of relation "core_objecttype" does not exist'
POD_FIXTURE = "/tmp/demodata.json"  # noqa: S108 - path inside the app pod
# ZAC's dev fixtures add their own Productaanvraag-Dimpact; podiumd-infra has only
# the chart Job's (11a5f7fd-...), so this one is left out with what depends on it.
FIXTURE_ONLY_OBJECTTYPE = "021f685e-9482-4620-b157-34cd4003da6b"
# Open Zaak's docker-compose-only Objects API service used this token; Open Zaak
# here uses Open Formulieren's (values.yaml), so it is left out too.
FIXTURE_ONLY_TOKEN = "openzaak"  # nosec B105  # noqa: S105 - a token identifier
_OBJECTTYPE_FIELDS = ("object_type", "_object_type")

_CREATE_SUPERUSER = """
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@example.com', 'admin')
"""


def _has_rows(pod: str, app_label: str, model: str) -> bool:
    code = f"""
from django.apps import apps
print('yes' if apps.get_model('{app_label}', '{model}').objects.exists() else 'no')
"""
    lines = kube.django_shell(pod, code).splitlines()
    return bool(lines) and lines[-1] == "yes"


def _publish(pod: str, model: str) -> None:
    """Sets every draft <model> to published: object creation only resolves published versions."""
    code = f"""
from django.apps import apps
updated = apps.get_model('core', '{model}').objects.exclude(status='published').update(status='published')
print(f'published {{updated}} {model} row(s)')
"""
    print(kube.django_shell(pod, code).strip())


def without_objecttype(rows: list[dict[str, Any]], uuid: str) -> list[dict[str, Any]]:
    """rows minus the core.objecttype with uuid and every row that points at it (directly or via its objects)."""
    gone = {row["pk"] for row in rows if row["model"] == "core.objecttype" and row["fields"].get("uuid") == uuid}
    objects = {
        row["pk"]
        for row in rows
        if row["model"] == "core.object" and any(row["fields"].get(f) in gone for f in _OBJECTTYPE_FIELDS)
    }

    def kept(row: dict[str, Any]) -> bool:
        fields = row["fields"]
        if row["model"] == "core.objecttype":
            return row["pk"] not in gone
        return not any(fields.get(f) in gone for f in _OBJECTTYPE_FIELDS) and fields.get("object") not in objects

    return [row for row in rows if kept(row)]


def without_token(rows: list[dict[str, Any]], identifier: str) -> list[dict[str, Any]]:
    """rows minus the token.tokenauth with identifier and its token.permission rows."""
    gone = {
        row["pk"] for row in rows if row["model"] == "token.tokenauth" and row["fields"].get("identifier") == identifier
    }
    return [
        row
        for row in rows
        if not (row["model"] == "token.tokenauth" and row["pk"] in gone)
        and not (row["model"] == "token.permission" and row["fields"].get("token_auth") in gone)
    ]


def seed(deployment: str, fixture: str, app_label: str, model: str) -> None:
    """Loads vendor fixture into deployment unless app_label.model has rows, then ensures the admin user."""
    kube.kubectl_shown(
        "wait", "--for=condition=available", f"deployment/{deployment}", "-n", NAMESPACE, "--timeout=180s"
    )
    pod = kube.first_pod(f"app.kubernetes.io/name={deployment}")
    if _has_rows(pod, app_label, model):
        print(f"'{deployment}' already has {app_label}.{model} data - skipping (not re-seeding).")
        return
    path = VENDOR_DIR / fixture
    print(f"Seeding '{deployment}' from {path} (without the fixture-only objecttype and token)...")
    rows = without_objecttype(json.loads(path.read_text(encoding="utf-8")), FIXTURE_ONLY_OBJECTTYPE)
    rows = without_token(rows, FIXTURE_ONLY_TOKEN)
    with tempfile.TemporaryDirectory() as directory:
        filtered = Path(directory) / path.name
        filtered.write_text(json.dumps(rows), encoding="utf-8")
        kube.kubectl_shown("cp", str(filtered), f"{NAMESPACE}/{pod}:{POD_FIXTURE}")
    loaddata = ["python", "/app/src/manage.py", "loaddata", POD_FIXTURE]
    loaded = process.run(["kubectl", "exec", "-n", NAMESPACE, pod, "--", *loaddata], check=False)
    print(loaded.stdout, end="")
    if loaded.returncode != 0:
        if KNOWN_OBJECTEN_BUG in loaded.stderr:
            msg = (
                f"seeding '{deployment}' hit the known objects-api bug (ObjectType.service has no "
                "migrated column; any ObjectType write fails) - nothing to fix here, see plan.md"
            )
            raise UserError(msg)
        print(loaded.stderr, file=sys.stderr, end="")
        msg = f"loaddata failed in '{deployment}'"
        raise UserError(msg)
    print(kube.django_shell(pod, _CREATE_SUPERUSER), end="")


def seed_fixtures(*, merged: bool) -> None:
    """Seeds objecten (and classic objecttypen) and publishes their draft versions."""
    if merged:
        seed("objecten", "openobject/demodata.json", "core", "Object")
        _publish(kube.first_pod("app.kubernetes.io/name=objecten"), "ObjectTypeVersion")
    else:
        seed("objecten", "objecten/demodata.json", "core", "Object")
        seed("objecttypen", "objecttypen/demodata.json", "core", "ObjectType")
        _publish(kube.first_pod("app.kubernetes.io/name=objecttypen"), "ObjectVersion")
    print("\nDone.")
