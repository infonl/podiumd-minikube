"""podiumd_minikube.crds: the monitoring-logging CRDs."""

from pathlib import Path

import yaml

from conftest import make_tgz

from podiumd_minikube import crds


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
