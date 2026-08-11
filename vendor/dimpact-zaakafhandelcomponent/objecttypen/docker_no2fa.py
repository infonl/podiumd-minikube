# Django settings module that disables enforced two-factor authentication for
# local docker-compose development.
#
# Upstream only reads DISABLE_2FA in objecttypes.conf.dev, which is not part
# of the objecttypes.conf.docker -> production -> base import chain used by
# this container, so setting the DISABLE_2FA env var (however it reaches the
# container - explicit env: or envFrom: a ConfigMap, confirmed both ways
# live) has no effect here - without this shim, a fresh superuser with no
# registered device is forced to a "Set up MFA" wall instead of reaching the
# admin. Switching DJANGO_SETTINGS_MODULE to objecttypes.conf.dev instead is
# not an option either: it unconditionally adds django-debug-toolbar to
# INSTALLED_APPS, which is not installed in the production image this
# container runs from.
#
# This mirrors what conf.dev does for its DISABLE_2FA handling (and
# openarchiefbeheer's/openformulieren's/openzaak's/opennotificaties' own
# identical docker_no2fa.py fix - same upstream gap, same maykin_2fa-based
# apps), without pulling in those dev-only dependencies.
from objecttypes.conf.docker import *  # noqa: F401,F403

MAYKIN_2FA_ALLOW_MFA_BYPASS_BACKENDS = AUTHENTICATION_BACKENDS  # noqa: F405
