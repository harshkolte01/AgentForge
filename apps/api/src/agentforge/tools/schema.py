"""Model-facing JSON schemas derived from the closed argument contracts.

The pydantic models stay the single source of truth for validation. These helpers only reshape the
schema a provider sees: hide fields the harness binds itself, and optionally convert to the
provider's strict structured-output dialect (every field required, optional ones nullable).
"""

from copy import deepcopy
from typing import Any

# Keywords strict structured outputs may reject. Their meaning is folded into the field description,
# and the gateway still enforces every bound when the arguments arrive.
_STRIPPED = ("minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "default", "title")


def drop_fields(schema: dict[str, Any], hidden: frozenset[str] | set[str]) -> dict[str, Any]:
    """Remove top-level properties (and their required entries) the harness fills in itself."""
    if not hidden:
        return schema
    result = dict(schema)
    result["properties"] = {key: value for key, value in schema.get("properties", {}).items() if key not in hidden}
    required = [key for key in schema.get("required", []) if key not in hidden]
    if required:
        result["required"] = required
    else:
        result.pop("required", None)
    return result


def _fold_bounds(node: dict[str, Any]) -> None:
    notes = []
    if "minLength" in node:
        notes.append(f"at least {node['minLength']} characters")
    if "maxLength" in node:
        notes.append(f"at most {node['maxLength']} characters")
    if "minItems" in node:
        notes.append(f"at least {node['minItems']} items")
    if "maxItems" in node:
        notes.append(f"at most {node['maxItems']} items")
    if notes:
        description = node.get("description", "")
        node["description"] = (description + (" " if description else "") + "(" + ", ".join(notes) + ")").strip()


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = dict(node)
    _fold_bounds(node)
    for key in _STRIPPED:
        node.pop(key, None)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in node:
            node[key] = [_strict(item) for item in node[key]]
    if isinstance(node.get("items"), dict):
        node["items"] = _strict(node["items"])
    if "$defs" in node:
        node["$defs"] = {name: _strict(value) for name, value in node["$defs"].items()}
    if node.get("type") == "object" or "properties" in node:
        required = set(node.get("required", []))
        properties: dict[str, Any] = {}
        for name, value in node.get("properties", {}).items():
            converted = _strict(value)
            nullable = any(isinstance(option, dict) and option.get("type") == "null" for option in converted.get("anyOf", []))
            if name not in required and not nullable:
                converted = {"anyOf": [converted, {"type": "null"}],
                             **({"description": converted["description"]} if "description" in converted else {})}
            properties[name] = converted
        node["properties"] = properties
        node["required"] = list(properties)
        node["additionalProperties"] = False
    return node


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The same contract in strict structured-output form. Optional fields become required-but-nullable."""
    return _strict(deepcopy(schema))
