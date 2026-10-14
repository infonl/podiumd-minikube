"""Facts read from the vendored dependency tarballs in charts/."""

import tarfile

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import yaml

from podiumd_minikube.paths import CHARTS_DIR

# Profiles `deploy --full` turns on; provision-cluster pre-pulls their images too.
FULL_PROFILE_SETS = [
    "--set", "objecten.enabled=true", "--set", "podiumd.objecten.enabled=true",
    "--set", "opennotificaties.enabled=true", "--set", "podiumd.opennotificaties.enabled=true",
    "--set", "openarchiefbeheer.enabled=true", "--set", "podiumd.openarchiefbeheer.enabled=true",
    "--set", "openformulieren.enabled=true", "--set", "podiumd.openformulieren.enabled=true",
    "--set", "metrics.enabled=true",
    "--set", "openinwoner.enabled=true", "--set", "podiumd.openinwoner.enabled=true",
    "--set", "podiumd.eck-operator.enabled=true",
    "--set", "ita.enabled=true", "--set", "podiumd.ita.enabled=true",
    "--set", "kiss.enabled=true", "--set", "podiumd.kiss.enabled=true", "--set", "podiumd.kiss-eck.enabled=true",
    "--set", "referentielijsten.enabled=true", "--set", "podiumd.referentielijsten.enabled=true",
    "--set", "openbeheer.enabled=true", "--set", "podiumd.openbeheer.enabled=true",
    "--set", "frankgateway.enabled=true", "--set", "podiumd.frankgateway.enabled=true",
    "--set", "podiumd.openbao.enabled=true",
    "--set", "clamav.enabled=true", "--set", "podiumd.clamav.enabled=true",
    "--set", "omc.enabled=true", "--set", "podiumd.omc.enabled=true",
]  # fmt: skip


def tarball(chart: str, charts_dir: Path = CHARTS_DIR) -> Path | None:
    """charts/<chart>-*.tgz, or None before `helm dependency update` fetched it."""
    return next(iter(sorted(charts_dir.glob(f"{chart}-*.tgz"))), None)


# Our own Jobs' images, from podiumd's values.yaml: values key -> path there.
BUNDLED_IMAGES = {
    "busybox": ("global", "images", "busybox"),
    "python": ("keycloak-operator", "jobs", "ensurePodiumdAdminUser", "initImage"),
}


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


def bundled_image_sets(charts_dir: Path = CHARTS_DIR) -> list[str]:
    """--set images.<key>=<repository>:<tag> for BUNDLED_IMAGES and images.postgresMajor; none before a fetch.

    Without them the templates fail on `required`.
    """
    tgz = tarball("podiumd", charts_dir)
    text = member_text(tgz, "podiumd/values.yaml") if tgz else None
    if text is None:
        return []
    podiumd_values = yaml.safe_load(text)
    sets: list[str] = []
    for key, path in BUNDLED_IMAGES.items():
        image = podiumd_values
        for step in path:
            image = image[step]
        sets += ["--set", f"images.{key}={image['repository']}:{image['tag']}"]
    postgres_tag = str(podiumd_values["global"]["images"]["postgres"]["tag"])
    return [*sets, "--set", f"images.postgresMajor={postgres_tag.split('.', maxsplit=1)[0]}"]


@dataclass(frozen=True)
class ObjectenShape:
    """Which objecten/objecttypen chart layout the selected podiumd version ships.

    classic: separate objecten and objecttypen subcharts. merged: one
    openobject subchart serving both APIs (aliased to the values key
    "objecten"); objecttypen then needs a DNS and an Ingress alias.
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
            sets=["--set", "objecten.merged=true"],
        )
    # podiumd<4.10: separate objecten and objecttypen subcharts.
    return ObjectenShape(
        merged=False, sets=["--set", "podiumd.objecttypen.enabled=true", "--set", "objecten.merged=false"]
    )


def everything_args(charts_dir: Path = CHARTS_DIR) -> list[str]:
    """helm args that render every profile: all images and hosts the chart can have."""
    return [*FULL_PROFILE_SETS, *objecten_shape(charts_dir).sets, *bundled_image_sets(charts_dir)]
