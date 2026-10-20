"""podiumd_minikube.crds: the monitoring-logging CRDs and the Keycloak operator's upstream ones."""

from pathlib import Path

import pytest
import yaml

from conftest import FakeRun
from conftest import make_tgz

from podiumd_minikube import chart
from podiumd_minikube import crds
from podiumd_minikube.process import UserError


def _crd(name: str, version: str | None) -> str:
    annotations = {crds.PROMETHEUS_OPERATOR_VERSION: version} if version else {}
    doc = {"kind": "CustomResourceDefinition", "metadata": {"name": name, "annotations": annotations}}
    return yaml.safe_dump(doc)


def test_crd_documents_reads_a_bare_equals_sign_as_a_string(tmp_path: Path):
    text = _crd("probes.x", None) + "spec:\n  enum:\n  - =\n"
    tgz = make_tgz(tmp_path / "monitoring-logging-1.0.0.tgz", {"m/crds/probes.yaml": text})
    assert crds.crd_documents(str(tgz))[0]["spec"]["enum"] == ["="]


def test_crd_documents_keeps_the_newest_copy_per_name(tmp_path: Path):
    tgz = make_tgz(
        tmp_path / "monitoring-logging-1.0.0.tgz",
        {
            "m/charts/kube-prometheus-stack/crd-servicemonitors.yaml": _crd("servicemonitors.x", "0.90.1"),
            "m/charts/loki/servicemonitors.yaml": _crd("servicemonitors.x", None),
            "m/charts/loki/podlogs.yaml": _crd("podlogs.y", None),
            "m/templates/service.yaml": yaml.safe_dump({"kind": "Service", "metadata": {"name": "s"}}),
            "m/templates/helpers.yaml": "{{- $values := (mustMergeOverwrite .) }}\n",
        },
    )
    found = crds.crd_documents(str(tgz))
    assert [(doc["metadata"]["name"], doc["metadata"]["annotations"]) for doc in found] == [
        ("podlogs.y", {}),
        ("servicemonitors.x", {crds.PROMETHEUS_OPERATOR_VERSION: "0.90.1"}),
    ]


def test_crd_documents_skips_non_yaml_members_and_other_kinds(tmp_path: Path):
    tgz = make_tgz(
        tmp_path / "monitoring-logging-1.0.0.tgz",
        {
            "m/README.md": _crd("readme.x", None),
            "m/crds/mixed.yaml": "---\n" + _crd("probes.x", None) + "---\nkind: Service\nmetadata:\n  name: s\n",
        },
    )
    assert [doc["metadata"]["name"] for doc in crds.crd_documents(str(tgz))] == ["probes.x"]


@pytest.mark.parametrize(
    ("api_version", "expected"), [("monitoring.coreos.com/v1", True), ("monitoring.example.com/v1", False)]
)
def test_needed_only_for_prometheus_operator_kinds(api_version: str, *, expected: bool):
    assert crds.needed([{"apiVersion": "v1"}, {"apiVersion": api_version}]) is expected


@pytest.mark.parametrize(
    ("members", "error"),
    [(None, "no charts/monitoring-logging-"), ({"m/Chart.yaml": "name: m\n"}, "no CustomResourceDefinition in ")],
)
def test_apply_monitoring_logging_crds_needs_crds_in_the_tarball(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: dict[str, str] | None, error: str
):
    tgz = make_tgz(tmp_path / "monitoring-logging-1.0.0.tgz", members) if members else None
    monkeypatch.setattr(chart, "tarball", lambda _name: tgz)
    with pytest.raises(UserError, match=f"^{error}"):
        crds.apply_monitoring_logging_crds()


def test_apply_monitoring_logging_crds_applies_server_side(
    fake_run: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    tgz = make_tgz(tmp_path / "monitoring-logging-1.0.0.tgz", {"m/crds/probes.yaml": _crd("probes.x", None)})
    monkeypatch.setattr(chart, "tarball", lambda _name: tgz)
    crds.apply_monitoring_logging_crds()
    assert fake_run.calls == [["kubectl", "apply", "--server-side", "--force-conflicts", "-f", "-"]]
    assert yaml.safe_load(fake_run.stdins[0] or "")["metadata"]["name"] == "probes.x"


def test_keycloak_version_reads_podiumds_anchor(tmp_path: Path):
    assert crds.keycloak_version(tmp_path) is None
    values = 'keycloak:\n  image:\n    tag: &keycloakImageVersion "26.8.1"\n'
    make_tgz(tmp_path / "podiumd-4.10.0.tgz", {"podiumd/values.yaml": values})
    assert crds.keycloak_version(tmp_path) == "26.8.1"


def test_with_upstream_keycloak_replaces_only_keycloaks_crds(monkeypatch: pytest.MonkeyPatch):
    fetched: list[str] = []

    def fetch(url: str) -> str | None:
        fetched.append(url)
        return None if "saml" in url else _crd(url.rsplit("/", 1)[1].removesuffix("-v1.yml"), None)

    monkeypatch.setattr(crds, "_fetch", fetch)
    bundled = [yaml.safe_load(_crd("keycloaks.k8s.keycloak.org", None)), yaml.safe_load(_crd("other.x", None))]
    names = [doc["metadata"]["name"] for doc in crds.with_upstream_keycloak(bundled, "26.8.1")]
    assert names == [
        "other.x", "keycloaks.k8s.keycloak.org", "keycloakrealmimports.k8s.keycloak.org",
        "keycloakoidcclients.k8s.keycloak.org",
    ]  # fmt: skip
    assert fetched[0].endswith("/26.8.1/kubernetes/keycloaks.k8s.keycloak.org-v1.yml")
    assert crds.with_upstream_keycloak(bundled, None) == bundled


def test_with_upstream_keycloak_refuses_a_version_without_crds(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(crds, "_fetch", lambda _url: None)
    with pytest.raises(UserError, match="no Keycloak CRDs upstream"):
        crds.with_upstream_keycloak([], "1.0.0")
