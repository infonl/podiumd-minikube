"""Reads single settings from values.yaml."""

from pathlib import Path

from podiumd_minikube.objects import Doc
from podiumd_minikube.objects import parse_yaml
from podiumd_minikube.paths import VALUES_YAML


def kvk_test_api_key(path: Path = VALUES_YAML) -> str:
    """podiumd.apiproxy.locations.kvkSearch.apikey: KvK's public test-environment key."""
    loaded: Doc = parse_yaml(path.read_text(encoding="utf-8")) or {}
    locations = loaded["podiumd"]["apiproxy"]["locations"]
    return str(locations["kvkSearch"]["apikey"])
