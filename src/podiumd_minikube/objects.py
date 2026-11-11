"""The type of a Kubernetes object, or any other parsed YAML or JSON mapping, and YAML parsing."""

from typing import Any
from typing import cast

import yaml

# Parsed kubectl JSON and Helm YAML: keys and nesting differ per kind.
Doc = dict[str, Any]  # pyright: ignore[reportExplicitAny]

# libyaml: a --full render loads in 0.3 s instead of 3.1 s, dumps in 0.06 s instead of 0.5 s.
YAML_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # pylint: disable=invalid-name
YAML_DUMPER: type[yaml.SafeDumper] = getattr(yaml, "CSafeDumper", yaml.SafeDumper)  # pylint: disable=invalid-name


def parse_yaml(text: str) -> Doc | None:
    """Return the YAML document in text: a mapping, None when empty."""
    return cast("Doc | None", yaml.load(text, YAML_LOADER))  # nosec B506  # noqa: S506 - a safe loader


def serialize_yaml(data: object, *, sort_keys: bool = True) -> str:
    """Return data as one YAML document."""
    return yaml.dump(data, Dumper=YAML_DUMPER, default_flow_style=False, sort_keys=sort_keys)
