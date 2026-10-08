"""Reads values.yaml: flags in its top-level blocks (written back keeping its formatting) and single settings."""

import re

from pathlib import Path

import yaml

from lib.paths import VALUES_YAML

_TOP_LEVEL_KEY = re.compile(r"^[^\s#]")


def _flag_line_indexes(lines: list[str], block: str, key: str) -> list[int]:
    """Indexes of `<indent>key:` lines inside top-level `block:` (other blocks reuse the key)."""
    key_line = re.compile(rf"^\s+{re.escape(key)}:")
    indexes: list[int] = []
    in_block = False
    for index, line in enumerate(lines):
        if line.rstrip("\n") == f"{block}:":
            in_block = True
        elif in_block and _TOP_LEVEL_KEY.match(line):
            in_block = False
        elif in_block and key_line.match(line):
            indexes.append(index)
    return indexes


def flag(block: str, key: str, path: Path = VALUES_YAML) -> bool:
    """Whether any `key: true` line sits in top-level `block:`."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    true_value = re.compile(rf"^\s+{re.escape(key)}:\s*true")
    return any(true_value.match(lines[i]) for i in _flag_line_indexes(lines, block, key))


def set_flag(block: str, key: str, *, value: bool, path: Path = VALUES_YAML) -> None:
    """Sets every `key: true|false` line in top-level `block:` to value."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_value = "true" if value else "false"
    pattern = re.compile(rf"{re.escape(key)}:\s*(true|false)")
    for index in _flag_line_indexes(lines, block, key):
        lines[index] = pattern.sub(f"{key}: {new_value}", lines[index], count=1)
    path.write_text("".join(lines), encoding="utf-8")


def monitoring_logging_enabled(path: Path = VALUES_YAML) -> bool:
    """monitoringLogging.enabled."""
    return flag("monitoringLogging", "enabled", path)


def zac_experimental_pkce(path: Path = VALUES_YAML) -> bool:
    """zac.experimentalPkce."""
    return flag("zac", "experimentalPkce", path)


def kvk_test_api_key(path: Path = VALUES_YAML) -> str:
    """podiumd.apiproxy.locations.kvkSearch.apikey: KvK's public test-environment key."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return str(loaded["podiumd"]["apiproxy"]["locations"]["kvkSearch"]["apikey"])
