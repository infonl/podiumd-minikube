"""podiumd_minikube.values: flags in values.yaml's top-level blocks."""

from pathlib import Path

from podiumd_minikube import values

VALUES = """\
zac:
  enabled: true # comment kept
  experimentalPkce: false

metrics:
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
    assert values.flag("metrics", "enabled", path) is False
    assert values.zac_experimental_pkce(path) is False
