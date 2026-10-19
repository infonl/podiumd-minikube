# The actor OMC links each klantcontact to (OMC_ACTOR_ID); the API assigns its
# own uuids, so it is created here. Run with `manage.py shell`; idempotent.
import os

from openklant.components.klantinteracties.models.actoren import Actor
from openklant.components.klantinteracties.models.actoren import GeautomatiseerdeActor
from openklant.components.klantinteracties.models.constants import SoortActor

actor, created = Actor.objects.update_or_create(
    uuid=os.environ["OMC_ACTOR_ID"],
    defaults={"naam": "OMC", "soort_actor": SoortActor.geautomatiseerde_actor, "indicatie_actief": True},
)
GeautomatiseerdeActor.objects.update_or_create(actor=actor, defaults={"functie": "notificaties"})
print(f"OMC actor {actor.uuid} {'created' if created else 'already existed, updated'}")
