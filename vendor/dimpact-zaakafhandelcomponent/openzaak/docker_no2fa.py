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
