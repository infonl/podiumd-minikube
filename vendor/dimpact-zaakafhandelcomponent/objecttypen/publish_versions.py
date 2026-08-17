# Idempotent fixup run via `manage.py shell < publish_versions.py`, after
# `manage.py loaddata demodata.json` (see
# templates/objecttypen/productaanvraag-objecttype-job.yaml). That fixture's
# own ObjectVersion rows are all seeded with status="draft" - confirmed live
# this leaves the productaanvraag objecttype's schema unusable by the
# Objects API's own object-create validation (which only resolves a
# *published* version when Open Formulieren's objects_api registration
# backend creates a productaanvraag object). Flips every version this
# fixture defines to "published" instead, since none of them are meant to
# stay in-progress drafts here - this is fixed demo/reference schema data,
# not a work-in-progress objecttype someone is still editing.
from django.apps import apps

ObjectVersion = apps.get_model("core", "ObjectVersion")

updated = ObjectVersion.objects.exclude(status="published").update(status="published")
print(f"published {updated} object version(s)")
