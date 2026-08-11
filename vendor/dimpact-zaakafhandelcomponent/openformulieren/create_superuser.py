# Idempotent superuser creation, run via `manage.py shell < create_superuser.py`
# from create-superuser-job.yaml.
#
# Unlike objecttypen/openzaak/opennotificaties, this chart never wires
# configuration.superuser to an actual user-creation mechanism at all -
# confirmed by grepping every template in the vendored openforms chart for
# "superuser": zero hits outside a commented-out django-setup-configuration
# example. `manage.py createsuperuser --noinput` itself works fine
# (confirmed live via `kubectl exec`), but isn't idempotent - it errors if
# the user already exists, which would fail every deploy after the first.
# get_or_create + set_password sidesteps that: this script succeeds
# identically whether the user exists yet or not, so re-applying this Job's
# already-succeeded, unchanged spec (a safe no-op, same as any other Job in
# this project) never needs a guard script the way pabc-migrations does.
import os

from django.contrib.auth import get_user_model

User = get_user_model()

username = os.environ["DJANGO_SUPERUSER_USERNAME"]
password = os.environ["DJANGO_SUPERUSER_PASSWORD"]
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")

user, created = User.objects.get_or_create(
    username=username,
    defaults={"email": email, "is_staff": True, "is_superuser": True},
)
user.email = email
user.is_staff = True
user.is_superuser = True
user.set_password(password)
user.save()

print(f"superuser '{username}' {'created' if created else 'already existed, updated'}")
