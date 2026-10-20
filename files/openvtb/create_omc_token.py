# OMC's API user and DRF token (OMC_TOKEN, OMC's zgw.auth.key.openvtb); no
# setup_configuration step makes tokens. Run with `manage.py shell`; idempotent.
import os

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

user, _ = get_user_model().objects.get_or_create(username="omc")
Token.objects.filter(user=user).exclude(key=os.environ["OMC_TOKEN"]).delete()
_, created = Token.objects.get_or_create(user=user, key=os.environ["OMC_TOKEN"])
print(f"OMC token for user {user.username} {'created' if created else 'already existed'}")
