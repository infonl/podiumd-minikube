"""The Helm releases a deploy installs, named and placed as ExternalsPodiumD's pipelines do.

ExternalsPodiumD: release podiumd in namespace podiumd (pipelines/application.yml) and
monitoring-logging in namespace monitoring (pipelines/monitoring.yml). This chart's own
templates (Postgres, Mailpit, the mocks: what the estates get from Azure) are a third
release next to podiumd, which reaches them by short name.
"""

import tempfile

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from podiumd_minikube import chart
from podiumd_minikube import process
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import MONITORING_NAMESPACE
from podiumd_minikube.paths import MONITORING_RELEASE
from podiumd_minikube.paths import NAMESPACE
from podiumd_minikube.paths import PODIUMD_RELEASE
from podiumd_minikube.paths import RELEASE_NAME
from podiumd_minikube.paths import VALUES_YAML

PODIUMD = PODIUMD_RELEASE
MONITORING = MONITORING_RELEASE
HELM_PLUGINS = CHART_DIR / "helm-plugins"
POST_RENDERER = "podiumd-minikube"
# ExternalsPodiumD's podiumd upgrade; monitoring-logging's adds --wait and its own timeout.
UPGRADE_ARGS = ["--timeout", "15m", "--server-side=true", "--force-conflicts", "--reset-values"]
MONITORING_ARGS = ["--timeout", "10m", "--wait", "--reset-values"]


@dataclass
class Release:
    """One `helm upgrade --install`: what, where, and its own values and --set arguments."""

    name: str
    namespace: str
    chart: Path
    values: dict[str, Any]
    sets: list[str] = field(default_factory=list[str])


def _split(helm_args: list[str], prefix: str) -> list[str]:
    """The --set pairs of helm_args under prefix, with the prefix removed."""
    pairs = zip(helm_args[::2], helm_args[1::2], strict=True)
    return [arg for flag, value in pairs if value.startswith(prefix) for arg in (flag, value[len(prefix) :])]


def _enabled(helm_args: list[str], key: str, *, default: bool) -> bool:
    """The last `--set key=true|false` in helm_args, else default."""
    found = [value.split("=", 1)[1] == "true" for value in helm_args[1::2] if value.startswith(f"{key}=")]
    return found[-1] if found else default


def releases(helm_args: list[str]) -> list[Release]:
    """This chart's own release, podiumd, and monitoring-logging when the metrics profile is on; install order."""
    values = yaml.safe_load(VALUES_YAML.read_text(encoding="utf-8"))
    podiumd_tgz = chart.tarball(PODIUMD)
    if podiumd_tgz is None:
        msg = "podiumd is not fetched: run podiumd-minikube set-podiumd-version"
        raise process.UserError(msg)
    # Our templates read podiumd's values; Helm hands a subchart's defaults to the parent only
    # while the subchart is part of the release.
    own = Release(
        RELEASE_NAME,
        NAMESPACE,
        CHART_DIR,
        {PODIUMD: chart.merged(chart.podiumd_defaults(), values[PODIUMD])},
        [*helm_args, "--set", f"{PODIUMD}.enabled=false", "--set", "metrics.enabled=false"],
    )
    found = [own, Release(PODIUMD, NAMESPACE, podiumd_tgz, values[PODIUMD], _split(helm_args, f"{PODIUMD}."))]
    monitoring_tgz = chart.tarball(MONITORING)
    if _enabled(helm_args, "metrics.enabled", default=values["metrics"]["enabled"]) and monitoring_tgz:
        sets = _split(helm_args, f"{MONITORING}.")
        found.append(Release(MONITORING, MONITORING_NAMESPACE, monitoring_tgz, values[MONITORING], sets))
    return found


def post_renderer_args(*, objecten_merged: bool, hold_zac: bool = False) -> list[str]:
    """--post-renderer and its arguments (cli.post_render's options)."""
    flags = ["--ca-trust", *(["--objecten-merged"] if objecten_merged else []), *(["--hold-zac"] if hold_zac else [])]
    return ["--post-renderer", POST_RENDERER, *(arg for flag in flags for arg in ("--post-renderer-args", flag))]


@contextmanager
def values_file(release: Release) -> Generator[str]:
    """release's values in a temporary file, for helm's --values."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as file:
        yaml.safe_dump(release.values, file)
        file.flush()
        yield file.name


def helm(*args: str) -> list[str]:
    """argv of helm with this repository's plugins (the post-renderer) instead of the user's."""
    return ["env", f"HELM_PLUGINS={HELM_PLUGINS}", "helm", *args]


def upgrade(release: Release, post_render: list[str]) -> None:
    """`helm upgrade --install` of release, output shown."""
    flags = MONITORING_ARGS if release.name == MONITORING else UPGRADE_ARGS
    with values_file(release) as values:
        args = ["upgrade", "--install", release.name, str(release.chart), "--namespace", release.namespace]
        args += ["--create-namespace", "--values", values, *release.sets, *flags, *post_render]
        print(f"\nhelm upgrade --install {release.name} (namespace {release.namespace})...")
        process.run(helm(*args), capture=False)


def template(release: Release, *extra: str) -> str:
    """`helm template` of release with its values, before the post-renderer: manifests.fix_up applies the same."""
    with values_file(release) as values:
        args = ["template", release.name, str(release.chart), "--namespace", release.namespace, "--values", values]
        return process.output(["helm", *args, "--include-crds", *release.sets, *extra])
