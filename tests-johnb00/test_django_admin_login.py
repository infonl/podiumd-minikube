"""
Adapted from ../tests/test_django_admin_login.py - skipped entirely.

Confirmed live: none of openzaak/openklant/objecten/objecttypen has a
`configuration.superuser`-style classic Django-admin credential configured
in values/johnb00/podiumd.yaml (no plaintext username/password anywhere in
any of their `settings:` blocks). Admin access on johnb00 instead goes
through Keycloak OIDC SSO with a group-based superuser mapping
(`oidc_db_config_admin_auth...groups_settings.superuser_group_names:
["administrators"]`, visible in each app's own `configuration.data`) - a
materially different login flow than this file's classic
/admin/login/-form-POST replay, and there's no known valid classic
credential to test that path with (the minikube reference's "admin"/"admin"
is a dev-only fixture specific to that project, not something johnb00 has).

Adapting this to exercise the real OIDC-admin path instead would
essentially duplicate test_login_flow.py/test_browser.py against each
app's own /admin/ (rather than zac's dashboard) - worth doing as a
follow-up, but not attempted here for lack of time in this session.
"""

import pytest

pytest.skip(
    "johnb00 has no classic Django-admin superuser credentials configured "
    "- admin access is via Keycloak OIDC SSO group mapping instead, see "
    "this file's own docstring",
    allow_module_level=True,
)
