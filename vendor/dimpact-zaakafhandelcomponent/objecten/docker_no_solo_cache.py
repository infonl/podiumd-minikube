# Django settings module that disables django-solo's cache for this app's
# singleton config models (currently just notifications_api_common's own
# NotificationsConfig).
#
# Found live while building the productaanvraag flow: NotificationsConfig's
# cached value (django-solo's own SOLO_CACHE="default"/SOLO_CACHE_TIMEOUT=300
# defaults, set in open_api_framework.conf.base and not overridden anywhere
# in this image) intermittently went stale - get_solo() returned
# notifications_api_service=None from the shared Redis cache while the
# underlying database row was, every time it was checked directly, correct
# (confirmed live via psql). This breaks POST /api/v2/objects outright
# (NotificationCreateMixin.notify() treats a None client as "Notifications
# API configuration is broken or absent" and raises, since
# NOTIFICATIONS_GUARANTEE_DELIVERY defaults to True) - exactly the request
# path Open Formulieren's own objects_api registration backend uses on every
# real form submission.
#
# Not chased down to an exact deterministic trigger (tried: single and
# concurrent repeated requests through Traefik and directly against the pod,
# with and without delays - none reproduced it on demand, but it was
# directly observed twice in real testing all the same) - disabling the
# cache for this specific, rarely-read singleton sidesteps the entire class
# of bug rather than depending on correctly diagnosing one specific race in
# a third-party library. The extra database round trip this adds per
# request is a single indexed primary-key lookup - negligible next to the
# rest of the work a productaanvraag object-create request already does
# (schema validation, the outgoing notification itself).
from objects.conf.docker import *  # noqa: F401,F403

SOLO_CACHE = None
