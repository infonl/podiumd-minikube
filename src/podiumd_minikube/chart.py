"""Facts read from the vendored dependency tarballs in charts/."""

import tarfile

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import yaml

from podiumd_minikube.paths import CHARTS_DIR

# The metrics profile: monitoring-logging and the exporters it scrapes, as kees00 and
# ExternalsPodiumD. Their ServiceMonitors need the monitoring CRDs (+460 MiB on
# kube-apiserver), so the exporters never come without it.
METRICS_SETS = [
    "--set", "metrics.enabled=true",
    "--set", "podiumd.clamav.metrics.enabled=true",
    "--set", "podiumd.clamav.metrics.serviceMonitor.enabled=true",
    "--set", "podiumd.redis-operator.redis-ha.redisExporter.enabled=true",
    "--set", "podiumd.redis-operator.redis-ha.redisExporter.podMonitor.enabled=true",
    "--set", "podiumd.eck-operator.podMonitor.enabled=true",
]  # fmt: skip
NO_METRICS_SETS = [arg.replace("=true", "=false") for arg in METRICS_SETS]

# deploy --skip-roles: keycloak.config.skipRoles/skipGroups true, the chart default: the realm
# import creates no realm or client roles and no groups; pabc-keycloak-groups-job adds PABC's.
# Only a fresh realm shows the difference (nothing removes existing roles).
SKIP_ROLES_SETS = [
    "--set", "podiumd.keycloak.config.skipRoles=true",
    "--set", "podiumd.keycloak.config.skipGroups=true",
]  # fmt: skip

# deploy --otel: the apps' OpenTelemetry metrics, as ExternalsPodiumD dim1, info and mayk and
# the chart's values-enable-observability.yaml, into monitoring-logging's collector (so with the
# metrics profile). Opt-in: about 50 MiB per Maykin web pod. ZAC reads its endpoint only with its
# own collector subchart enabled; renamed (its default name is the shared collector's), its
# Deployment dropped by manifests.is_unused_zac_collector.
OTEL_COLLECTOR = "http://podiumd-minikube-opentelemetry-collector:4317"
OTEL_APPS = ("openzaak", "opennotificaties", "objecten", "openklant", "openformulieren", "openinwoner")
OTEL_SETS = [
    *METRICS_SETS,
    *(
        arg
        for app in OTEL_APPS
        for key, value in (
            ("disabled", "false"),
            ("exporterOtlpEndpoint", OTEL_COLLECTOR),
            ("exporterOtlpProtocol", "grpc"),
            ("exporterOtlpMetricsInsecure", "true"),
            ("metricExportInterval", "60000"),
            ("metricExportTimeout", "10000"),
        )
        for arg in ("--set", f"podiumd.{app}.settings.otel.{key}={value}")
    ),
    "--set", "podiumd.zac.opentelemetry-collector.enabled=true",
    "--set", "podiumd.zac.opentelemetry-collector.fullnameOverride=zac-opentelemetry-collector",
    "--set", f"podiumd.zac.opentelemetry_zaakafhandelcomponent.endpoint={OTEL_COLLECTOR}",
]  # fmt: skip

# Profiles `deploy --full` turns on; provision-cluster pre-pulls their images too.
FULL_PROFILE_SETS = [
    "--set", "podiumd.objecten.enabled=true",
    "--set", "podiumd.opennotificaties.enabled=true",
    "--set", "podiumd.openarchiefbeheer.enabled=true",
    "--set", "podiumd.openformulieren.enabled=true",
    *METRICS_SETS,
    "--set", "podiumd.openinwoner.enabled=true",
    "--set", "podiumd.eck-operator.enabled=true",
    "--set", "podiumd.ita.enabled=true",
    "--set", "podiumd.kiss.enabled=true", "--set", "podiumd.kiss-eck.enabled=true",
    "--set", "podiumd.referentielijsten.enabled=true",
    "--set", "podiumd.openbeheer.enabled=true",
    "--set", "podiumd.frankgateway.enabled=true",
    "--set", "podiumd.openbao.enabled=true",
    "--set", "podiumd.clamav.enabled=true",
    "--set", "podiumd.omc.enabled=true",
    "--set", "podiumd.openvtb.enabled=true",
    "--set", "podiumd.zaakbrug.enabled=true",
    "--set", "podiumd.zgw-office-addin.enabled=true",
]  # fmt: skip


def tarball(chart: str, charts_dir: Path = CHARTS_DIR) -> Path | None:
    """charts/<chart>-*.tgz, or None before `helm dependency update` fetched it."""
    return next(iter(sorted(charts_dir.glob(f"{chart}-*.tgz"))), None)


# Our own Jobs' images, from podiumd's values.yaml: values key -> path there.
BUNDLED_IMAGES = {
    "busybox": ("global", "images", "busybox"),
    "nginx": ("global", "images", "nginx"),
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
    """--set images.<key>=<repository>:<tag> for BUNDLED_IMAGES; none before a fetch.

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
    return sets


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
    """Detected from the podiumd tarball's contents, not its version (a chart checkout has none)."""
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
