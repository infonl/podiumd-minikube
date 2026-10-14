"""podiumd_minikube.chart: objecten shape from the podiumd tarball."""

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
