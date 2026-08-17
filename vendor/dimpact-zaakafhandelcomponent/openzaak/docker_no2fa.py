# Django settings module that disables enforced two-factor authentication for
# local docker-compose development.
#
# Upstream only reads DISABLE_2FA in openzaak.conf.dev/openzaak.conf.ci, which
# are not part of the openzaak.conf.docker -> production -> includes.base
# import chain used by this container (confirmed live: neither DISABLE_2FA
# nor MAYKIN_2FA appear anywhere in conf/docker.py, conf/production.py, or
# conf/includes/base.py inside the running openzaak pod), so setting the
# DISABLE_2FA env var has no effect here - without this shim, a fresh
# superuser with no registered device is forced to a "Tweestapsauthenticatie
# instellen" (set up two-step auth) wall instead of reaching the admin,
# confirmed live. Switching DJANGO_SETTINGS_MODULE to openzaak.conf.dev
# instead is not an option either: it unconditionally adds
# django-debug-toolbar to INSTALLED_APPS, which is not installed in the
# production image this container runs from.
#
# This mirrors what conf.dev does for its DISABLE_2FA handling (and
# openarchiefbeheer's/openformulieren's own identical docker_no2fa.py fix -
# same upstream gap, same maykin_2fa-based apps), without pulling in those
# dev-only dependencies.
from openzaak.conf.docker import *  # noqa: F401,F403

MAYKIN_2FA_ALLOW_MFA_BYPASS_BACKENDS = AUTHENTICATION_BACKENDS  # noqa: F405

# Unrelated second fix bundled into this same shim (simpler than adding a
# second ConfigMap+extraVolumeMounts pair just for this) - see
# vendor/dimpact-zaakafhandelcomponent/objecten/docker_no_solo_cache.py's own
# header for the full story of what this fixes and why (django-solo's cache
# for notifications_api_common's own NotificationsConfig singleton
# intermittently going stale). Confirmed live here too, and with real
# consequences for openzaak specifically: POST /zaken/api/v1/zaken (i.e.
# ZAC creating any zaak at all, not just through the productaanvraag flow)
# raises "Not notifying, Notifications API configuration is broken or
# absent." straight through as a 500 whenever this happens, since
# openzaak's own zaak creation triggers its own "zaken" channel notification
# the same way Objects API's object creation does.
SOLO_CACHE = None
