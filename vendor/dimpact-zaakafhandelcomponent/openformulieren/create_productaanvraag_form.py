# Idempotent creation of a demo productaanvraag form, run via
# `manage.py shell < create_productaanvraag_form.py` (see
# templates/openformulieren/productaanvraag-form-job.yaml). Forms aren't
# configurable through setup_configuration at all (confirmed by reading
# every step registered in this image's own settings.SETUP_CONFIGURATION_STEPS -
# none of them touch Form/FormDefinition/FormStep/FormRegistrationBackend),
# unlike the ZGW/Objects API services and the Objects API group this same
# form depends on - those are wired declaratively via
# podiumd.openformulieren.configuration.data instead (see that block's own
# comment).
#
# Uses the objects_api registration backend (the productaanvraag flow),
# not zgw-create-zaak (a direct zaak-creation backend also available in
# this image) - deliberate choice, matches
# nl.info.zac.productaanvraag.ProductaanvraagService on the ZAC side, which
# only reacts to Objects API "object created" notifications, not to zaken
# created directly via the ZGW APIs.
#
# update_or_create by slug/key everywhere: safe to re-run this Job's
# already-succeeded, unchanged spec, same as any other Job in this project.
import json
import os

from openforms.contrib.objects_api.models import ObjectsAPIGroupConfig
from openforms.forms.models import (
    Form,
    FormDefinition,
    FormRegistrationBackend,
    FormStep,
    FormVariable,
)

FORM_SLUG = os.environ["FORM_SLUG"]
FORM_NAME = os.environ["FORM_NAME"]
OBJECTS_API_GROUP_IDENTIFIER = os.environ["OBJECTS_API_GROUP_IDENTIFIER"]
PRODUCTAANVRAAG_OBJECTTYPE_UUID = os.environ["PRODUCTAANVRAAG_OBJECTTYPE_UUID"]
PRODUCTAANVRAAGTYPE = os.environ["PRODUCTAANVRAAGTYPE"]
ORGANISATIE_RSIN = os.environ["ORGANISATIE_RSIN"]

api_group = ObjectsAPIGroupConfig.objects.get(identifier=OBJECTS_API_GROUP_IDENTIFIER)

configuration = {
    "display": "form",
    "components": [
        {
            "type": "textfield",
            "key": "naamAanvrager",
            "label": "Naam aanvrager",
            "validate": {"required": True},
        },
        {
            "type": "textarea",
            "key": "omschrijving",
            "label": "Omschrijving van de melding",
            "validate": {"required": True},
        },
    ],
}

form_definition, _ = FormDefinition.objects.update_or_create(
    slug=f"{FORM_SLUG}-melding",
    defaults={"name": f"{FORM_NAME} - melding", "configuration": configuration},
)

form, _ = Form.objects.update_or_create(
    slug=FORM_SLUG,
    defaults={"name": FORM_NAME, "active": True, "maintenance_mode": False},
)

FormStep.objects.update_or_create(
    form=form,
    form_definition=form_definition,
    defaults={"order": 0},
)

FormVariable.objects.create_for_form(form)

content_json = (
    "{\n"
    '  "bron": {\n'
    '    "naam": "Open Formulieren",\n'
    '    "kenmerk": "{{ submission.public_reference }}"\n'
    "  },\n"
    f'  "type": "{PRODUCTAANVRAAGTYPE}",\n'
    '  "aanvraaggegevens": {% json_summary %},\n'
    '  "taal": "nld"\n'
    "}"
)

FormRegistrationBackend.objects.update_or_create(
    form=form,
    key="objects-api-productaanvraag",
    defaults={
        "name": f"Objecten API - productaanvraag ({FORM_NAME})",
        "backend": "objects_api",
        "options": {
            "version": 1,
            # The registration options serializer resolves this against
            # ObjectsAPIGroupConfig's own "identifier" slug, not its numeric
            # pk (SlugRelatedAsChoicesField(slug_field="identifier"),
            # confirmed live) - api_group is only fetched above to fail
            # fast with a clear error if the identifier doesn't exist yet.
            "objects_api_group": api_group.identifier,
            "objecttype": PRODUCTAANVRAAG_OBJECTTYPE_UUID,
            "objecttype_version": 1,
            "update_existing_object": False,
            "auth_attribute_path": [],
            "upload_submission_csv": False,
            "organisatie_rsin": ORGANISATIE_RSIN,
            "content_json": content_json,
            "iot_submission_report": "",
            "iot_submission_csv": "",
            "iot_attachment": "",
        },
    },
)

print(f"form {FORM_SLUG!r} configured with objects_api registration backend.")
