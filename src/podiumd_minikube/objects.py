"""The type of a Kubernetes object, or any other parsed YAML or JSON mapping."""

from typing import Any

# Parsed kubectl JSON and Helm YAML: keys and nesting differ per kind.
Doc = dict[str, Any]  # pyright: ignore[reportExplicitAny]
