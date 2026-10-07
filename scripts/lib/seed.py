"""Demo data that docker-compose loads with `manage.py loaddata` inside the running app.

Only objecten/objecttypen (or merged openobject) use that pattern; other apps
seed through Postgres init SQL or setup_configuration in values.yaml. Each
step is skipped when its model already has rows, so seeding runs on every
deploy.
"""

import sys

from lib import kube
from lib import process
from lib.paths import NAMESPACE
from lib.paths import VENDOR_DIR
from lib.process import UserError

# objects-api 3.6.1/3.6.2 declare ObjectType.service without a migration for
# its column, so any ObjectType write fails (upstream bug, see plan.md).
KNOWN_OBJECTEN_BUG = 'column "service_id" of relation "core_objecttype" does not exist'
POD_FIXTURE = "/tmp/demodata.json"  # noqa: S108 - path inside the app pod

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


def seed(deployment: str, fixture: str, app_label: str, model: str) -> None:
    """Loads vendor fixture into deployment unless app_label.model has rows, then ensures the admin user."""
    kube.kubectl_shown(
        "wait", "--for=condition=available", f"deployment/{deployment}", "-n", NAMESPACE, "--timeout=180s"
    )
    pod = kube.first_pod(deployment)
    if _has_rows(pod, app_label, model):
        print(f"'{deployment}' already has {app_label}.{model} data - skipping (not re-seeding).")
        return
    path = VENDOR_DIR / fixture
    print(f"Seeding '{deployment}' from {path}...")
    kube.kubectl_shown("cp", str(path), f"{NAMESPACE}/{pod}:{POD_FIXTURE}")
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
        _publish(kube.first_pod("objecten"), "ObjectTypeVersion")
    else:
        seed("objecten", "objecten/demodata.json", "core", "Object")
        seed("objecttypen", "objecttypen/demodata.json", "core", "ObjectType")
        _publish(kube.first_pod("objecttypen"), "ObjectVersion")
    print("\nDone.")
