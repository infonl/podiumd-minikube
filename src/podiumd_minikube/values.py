"""Reads single settings from values.yaml."""

from pathlib import Path

import yaml

from podiumd_minikube.paths import VALUES_YAML


def kvk_test_api_key(path: Path = VALUES_YAML) -> str:
    """podiumd.apiproxy.locations.kvkSearch.apikey: KvK's public test-environment key."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    locations = loaded["podiumd"]["apiproxy"]["locations"]
    return str(locations["kvkSearch"]["apikey"])
