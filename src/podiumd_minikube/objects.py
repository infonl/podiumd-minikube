"""The type of a Kubernetes object, or any other parsed YAML or JSON mapping, and YAML parsing."""

import io

from collections.abc import Iterable
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


def serialize_yaml_all(docs: Iterable[object], *, sort_keys: bool = False) -> str:
    """Return docs as a multi-document YAML stream.

    Built on the Dumper class: the stubs of the yaml.*dump* functions type width as _typeshed.FloatInt, which
    basedpyright 1.40.2's typeshed lacks, so every call to them is partially Unknown.
    """
    stream = io.StringIO()
    dumper = YAML_DUMPER(stream, default_flow_style=False, sort_keys=sort_keys)
    dumper.open()
    for doc in docs:
        dumper.represent(doc)  # pyright: ignore[reportUnknownMemberType] - untyped stub
    dumper.close()
    return stream.getvalue()


def serialize_yaml(data: object, *, sort_keys: bool = True) -> str:
    """Return data as one YAML document."""
    return serialize_yaml_all([data], sort_keys=sort_keys)
