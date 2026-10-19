"""podiumd_minikube.values: single settings from values.yaml."""

from pathlib import Path

from podiumd_minikube import values


def test_kvk_test_api_key_reads_the_kvk_search_location(tmp_path: Path):
    path = tmp_path / "values.yaml"
    path.write_text("podiumd:\n  apiproxy:\n    locations:\n      kvkSearch:\n        apikey: l7xx\n", encoding="utf-8")
    assert values.kvk_test_api_key(path) == "l7xx"


def test_kvk_test_api_key_is_set_in_this_charts_values():
    assert values.kvk_test_api_key()
