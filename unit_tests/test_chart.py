"""podiumd_minikube.chart: objecten shape and bundled images from the podiumd tarball."""

from pathlib import Path

from conftest import make_tgz

from podiumd_minikube import chart


def test_objecten_shape_is_classic_without_openobject(tmp_path: Path):
    make_tgz(tmp_path / "podiumd-4.9.3.tgz", {"podiumd/charts/objecten/Chart.yaml": ""})
    shape = chart.objecten_shape(tmp_path)
    assert not shape.merged
    assert "podiumd.objecttypen.enabled=true" in shape.sets


def test_objecten_shape_is_classic_before_any_fetch(tmp_path: Path):
    assert not chart.objecten_shape(tmp_path).merged


def test_objecten_shape_is_merged_with_openobject(tmp_path: Path):
    make_tgz(tmp_path / "podiumd-5.0.0.tgz", {"podiumd/charts/openobject/Chart.yaml": ""})
    shape = chart.objecten_shape(tmp_path)
    assert shape.merged
    assert "objecten.merged=true" in shape.sets


def test_bundled_image_sets_come_from_podiumds_values(tmp_path: Path):
    podiumd_values = """\
global:
  images:
    busybox: {repository: library/busybox, tag: "1.38.0-glibc@sha256:aa"}
    postgres: {repository: library/postgres, tag: "16.15-alpine@sha256:bb"}
keycloak-operator:
  jobs:
    ensurePodiumdAdminUser:
      initImage: {repository: python, tag: "3.14.7-slim"}
"""
    assert chart.bundled_image_sets(tmp_path) == []
    make_tgz(tmp_path / "podiumd-4.10.0.tgz", {"podiumd/values.yaml": podiumd_values})
    assert chart.bundled_image_sets(tmp_path) == [
        "--set", "images.busybox=library/busybox:1.38.0-glibc@sha256:aa", "--set", "images.python=python:3.14.7-slim",
        "--set", "images.postgresMajor=16",
    ]  # fmt: skip
