"""Leftovers of podiumd releases older than the oldest one deployed here."""

import re

import yaml

from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import VALUES_YAML

# Raise when a release stops being deployed. Code needed only before release X.Y
# is marked `podiumd<X.Y`; the test lists markers at or below this version.
OLDEST_PODIUMD = "4.9.4"
MARKER = re.compile(r"podiumd<(\d+(?:\.\d+)*)")
SOURCES = ["values.yaml", "templates/**/*", "src/**/*.py", "tests/**/*.py", "vendor/**/*"]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_no_code_is_marked_for_releases_older_than_the_oldest_deployed():
    dead = [
        f"{path.relative_to(CHART_DIR)}:{number}: {line.strip()}"
        for pattern in SOURCES
        for path in sorted(CHART_DIR.glob(pattern))
        if path.is_file()
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
        if (match := MARKER.search(line)) and _version(match.group(1)) <= _version(OLDEST_PODIUMD)
    ]
    assert not dead, f"only needed before podiumd {OLDEST_PODIUMD}, remove: {dead}"


def _image_tags(node: object, path: str = "") -> list[str]:
    if not isinstance(node, dict):
        return []
    found = [f"{path}.tag" for key in node if key == "tag" and "image" in path.rsplit(".", 1)[-1].lower()]
    return found + [tag for key, value in node.items() for tag in _image_tags(value, f"{path}.{key}")]


def test_values_pin_no_image_tag_of_podiumd():
    podiumd = yaml.safe_load(VALUES_YAML.read_text(encoding="utf-8"))["podiumd"]
    pins = _image_tags(podiumd, "podiumd")
    assert not pins, f"podiumd pins these images itself; remove the overrides: {pins}"
