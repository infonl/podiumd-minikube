"""lib.chart: objecten shape and ZAC PKCE detection from the podiumd tarball."""

from pathlib import Path

import pytest

from conftest import make_tgz

from lib import chart
from lib import values
from lib.process import UserError


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
    assert f"podiumd.objecten.settings.allowedHosts={chart.MERGED_ALLOWED_HOSTS}" in shape.sets


def test_zac_pkce_off_needs_no_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(values, "zac_experimental_pkce", lambda: False)
    assert chart.zac_pkce(tmp_path) == chart.ZacPkce(enabled=False)


def test_zac_pkce_on_requires_pkce_support_in_the_zac_chart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(values, "zac_experimental_pkce", lambda: True)
    config = "podiumd/charts/zaakafhandelcomponent/templates/config.yaml"
    make_tgz(tmp_path / "podiumd-4.9.3.tgz", {config: "OTHER: 1\n"})
    with pytest.raises(UserError, match="AUTH_ENABLE_PKCE"):
        chart.zac_pkce(tmp_path)
    make_tgz(tmp_path / "podiumd-4.9.3.tgz", {config: "AUTH_ENABLE_PKCE: true\n"})
    assert chart.zac_pkce(tmp_path).sets == ["--set", f"podiumd.zac.image.tag={chart.ZAC_PKCE_IMAGE_TAG}"]
