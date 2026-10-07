"""Changes to the live Keycloak realm that a realm re-import cannot make.

--import-realm skips a realm that already exists, and Keycloak persists to
Postgres, so a changed vendored realm never reaches a provisioned cluster.
"""

import sys

from lib import kube
from lib import polling
from lib import process
from lib.paths import NAMESPACE

REALM = "zaakafhandelcomponent"
CLIENT_ID = "zaakafhandelcomponent"
# templates/keycloak/deployment.yaml's KC_BOOTSTRAP_ADMIN_USERNAME/PASSWORD.
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin"  # nosec B105  # noqa: S105 - dev-only default from the template
KCADM = ["kubectl", "exec", "-n", NAMESPACE, "deploy/keycloak", "--", "/opt/keycloak/bin/kcadm.sh"]
STARTUP_TIMEOUT = 90


def _client_uuid() -> str | None:
    """The client's id after a fresh kcadm login; None while Keycloak is still starting."""
    login = [
        *KCADM, "config", "credentials", "--server", "http://localhost:8080",
        "--realm", "master", "--user", ADMIN_USER, "--password", ADMIN_PASSWORD,
    ]  # fmt: skip
    if not process.succeeds(login):
        return None
    lookup = [
        *KCADM,
        "get",
        "clients",
        "-r",
        REALM,
        "-q",
        f"clientId={CLIENT_ID}",
        "--fields",
        "id",
        "--format",
        "csv",
        "--noquotes",
    ]
    result = process.run(lookup, check=False)
    if result.returncode != 0:
        return None
    return "".join(result.stdout.split()) or None


def sync_zac_pkce(*, enabled: bool) -> None:
    """Sets the live zaakafhandelcomponent client's PKCE method to S256 or "" (not required)."""
    if not kube.exists("deployment/keycloak"):
        print("'keycloak' not found - skipping the PKCE sync.")
        return
    target = "S256" if enabled else ""
    print(f"Reconciling Keycloak's live '{REALM}' realm PKCE requirement (target: '{target}')...")
    uuid = polling.wait_until(_client_uuid, timeout=STARTUP_TIMEOUT, interval=3)
    if not uuid:
        print(
            f"WARNING: Keycloak or client '{CLIENT_ID}' in realm '{REALM}' not reachable after "
            f"{STARTUP_TIMEOUT}s; PKCE not synced - re-run deploy once Keycloak is up.",
            file=sys.stderr,
        )
        return
    process.output(
        [*KCADM, "update", f"clients/{uuid}", "-r", REALM, "-s", f'attributes."pkce.code.challenge.method"={target}']
    )
    print(f"Keycloak's live '{CLIENT_ID}' client PKCE requirement set to '{target}'.")
