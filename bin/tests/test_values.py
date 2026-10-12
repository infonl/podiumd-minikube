"""lib.values: flags in values.yaml's top-level blocks."""

from pathlib import Path

from lib import values

VALUES = """\
zac:
  enabled: true # comment kept
  experimentalPkce: false

monitoringLogging:
  # nested enabled keys elsewhere must not count
  enabled: false
other:
  enabled: true
"""


def _values(tmp_path: Path) -> Path:
    path = tmp_path / "values.yaml"
    path.write_text(VALUES, encoding="utf-8")
    return path


def test_flag_reads_only_its_own_block(tmp_path: Path):
    path = _values(tmp_path)
    assert values.flag("zac", "enabled", path) is True
    assert values.monitoring_logging_enabled(path) is False
    assert values.zac_experimental_pkce(path) is False


def test_set_flag_changes_only_its_own_block_and_keeps_formatting(tmp_path: Path):
    path = _values(tmp_path)
    values.set_flag("monitoringLogging", "enabled", value=True, path=path)
    assert values.monitoring_logging_enabled(path) is True
    assert path.read_text(encoding="utf-8") == VALUES.replace("  enabled: false\nother", "  enabled: true\nother")
    values.set_flag("zac", "enabled", value=False, path=path)
    assert "  enabled: false # comment kept" in path.read_text(encoding="utf-8")


def test_kvk_test_api_key_follows_the_yaml_anchor(tmp_path):
    path = tmp_path / "values.yaml"
    path.write_text(
        "podiumd:\n  apiproxy:\n    locations:\n      kvkSearch:\n        apikey: &k public-test-key\n"
        "      kvkBasic:\n        apikey: *k\n",
        encoding="utf-8",
    )
    assert values.kvk_test_api_key(path) == "public-test-key"
