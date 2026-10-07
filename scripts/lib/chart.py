"""Facts read from the vendored dependency tarballs in charts/."""

import tarfile

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from lib import values
from lib.paths import CHARTS_DIR
from lib.process import UserError

# Profiles `deploy --full` turns on; provision-cluster pre-pulls their images too.
FULL_PROFILE_SETS = [
    "--set", "wiremock.enabled=true",
    "--set", "objecten.enabled=true", "--set", "podiumd.objecten.enabled=true",
    "--set", "opennotificaties.enabled=true", "--set", "podiumd.opennotificaties.enabled=true",
    "--set", "openarchiefbeheer.enabled=true", "--set", "podiumd.openarchiefbeheer.enabled=true",
    "--set", "openformulieren.enabled=true", "--set", "podiumd.openformulieren.enabled=true",
    "--set", "metrics.enabled=true",
    "--set", "openinwoner.enabled=true", "--set", "podiumd.openinwoner.enabled=true",
    "--set", "podiumd.eck-operator.enabled=true",
    "--set", "ita.enabled=true", "--set", "podiumd.ita.enabled=true",
    "--set", "kiss.enabled=true", "--set", "podiumd.kiss.enabled=true", "--set", "podiumd.kiss-eck.enabled=true",
    "--set", "omc.enabled=true", "--set", "podiumd.omc.enabled=true",
    "--set", "referentielijsten.enabled=true", "--set", "podiumd.referentielijsten.enabled=true",
    "--set", "openbeheer.enabled=true", "--set", "podiumd.openbeheer.enabled=true",
]  # fmt: skip

ZAC_PKCE_IMAGE_TAG = "5.4.2"
# The merged app also answers the dropped objecttypen hostnames (Django ALLOWED_HOSTS).
MERGED_ALLOWED_HOSTS = r"objecten.local\,objecttypen\,objecttypen.podiumd-minikube"


def tarball(chart: str, charts_dir: Path = CHARTS_DIR) -> Path | None:
    """charts/<chart>-*.tgz, or None before `helm dependency update` fetched it."""
    return next(iter(sorted(charts_dir.glob(f"{chart}-*.tgz"))), None)


def member_names(tgz: Path) -> list[str]:
    """Paths of all members of tgz."""
    with tarfile.open(tgz) as archive:
        return archive.getnames()


def member_text(tgz: Path, name: str) -> str | None:
    """Content of member name in tgz, or None when absent."""
    with tarfile.open(tgz) as archive:
        try:
            member = archive.extractfile(name)
        except KeyError:
            return None
        return member.read().decode() if member else None


@dataclass(frozen=True)
class ObjectenShape:
    """Which objecten/objecttypen chart layout the selected podiumd version ships.

    classic: separate objecten and objecttypen subcharts. merged: one
    openobject subchart serving both APIs (aliased to the values key
    "objecten"); objecttypen then needs a DNS alias and extra ALLOWED_HOSTS,
    and values.yaml's objects-api image tag does not exist for open-object.
    """

    merged: bool
    sets: list[str] = field(default_factory=list[str])


def objecten_shape(charts_dir: Path = CHARTS_DIR) -> ObjectenShape:
    """Detected from the podiumd tarball's contents, not its version (a --path checkout has none)."""
    tgz = tarball("podiumd", charts_dir)
    names = member_names(tgz) if tgz else []
    if any(name.startswith("podiumd/charts/openobject/") for name in names):
        return ObjectenShape(
            merged=True,
            sets=[
                "--set", "podiumd.objecten.image.tag=null",
                "--set", "podiumd.objecten.create_required_objecttypen_job.enabled=false",
                "--set", "objecten.merged=true",
                "--set", f"podiumd.objecten.settings.allowedHosts={MERGED_ALLOWED_HOSTS}",
            ],
        )  # fmt: skip
    return ObjectenShape(
        merged=False, sets=["--set", "podiumd.objecttypen.enabled=true", "--set", "objecten.merged=false"]
    )


@dataclass(frozen=True)
class ZacPkce:
    """zac.experimentalPkce and the --set flags it needs."""

    enabled: bool
    sets: list[str] = field(default_factory=list[str])


def everything_args(charts_dir: Path = CHARTS_DIR) -> list[str]:
    """helm args that render every profile plus monitoring-logging: all images and hosts the chart can have."""
    return [
        *FULL_PROFILE_SETS,
        *objecten_shape(charts_dir).sets,
        "--set", "monitoringLogging.enabled=true",
        *zac_pkce(charts_dir).sets,
    ]  # fmt: skip


def zac_pkce(charts_dir: Path = CHARTS_DIR) -> ZacPkce:
    """zac.experimentalPkce, refused when the selected zac chart cannot send PKCE.

    Requiring PKCE in the realm while zac never sends it rejects every login
    (this happened once, see plan.md).
    """
    if not values.zac_experimental_pkce():
        return ZacPkce(enabled=False)
    tgz = tarball("podiumd", charts_dir)
    config = member_text(tgz, "podiumd/charts/zaakafhandelcomponent/templates/config.yaml") if tgz else None
    if "AUTH_ENABLE_PKCE" not in (config or ""):
        msg = (
            "zac.experimentalPkce is true, but the selected podiumd version's zac chart has no "
            "AUTH_ENABLE_PKCE in templates/config.yaml, so every login would be rejected. "
            "Bump the zac chart to >= 1.0.289 in the --path checkout (see values.yaml's "
            "podiumd.zac.image.tag comment), or set zac.experimentalPkce to false."
        )
        raise UserError(msg)
    return ZacPkce(enabled=True, sets=["--set", f"podiumd.zac.image.tag={ZAC_PKCE_IMAGE_TAG}"])
