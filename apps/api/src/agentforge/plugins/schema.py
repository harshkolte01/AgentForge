"""What the model is shown for a plugin's tools: safe function names and schemas it can rely on.

A plugin server is third-party code. Its tool names, descriptions and schemas are untrusted, so they are cleaned before the
model sees them: names become `slug__tool` within the provider's 64-character limit, schemas keep only the standard
keywords, and anything oversized is reduced rather than trusted. The server's own name for a tool is kept for the call.
"""
import hashlib
import json
import re
from typing import Any

MAX_TOOLS = 100
MAX_NAME = 64
MAX_DESCRIPTION = 1500
MAX_SCHEMA_BYTES = 12000
MAX_DEPTH = 12
MAX_NODES = 1500

JSON_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
# Keywords every provider accepts. Others (title, examples, pattern, format, $schema, $id, vendor extensions) add tokens or
# risk a provider rejecting the whole request, and are dropped.
KEEP = frozenset({"type", "properties", "required", "items", "enum", "const", "description", "default", "anyOf", "oneOf", "allOf",
                  "additionalProperties", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
                  "maxLength", "minItems", "maxItems", "uniqueItems", "multipleOf", "$ref", "$defs", "definitions"})
NUMERIC = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "minItems", "maxItems", "multipleOf"})
EMPTY_OBJECT: dict[str, Any] = {"type": "object", "properties": {}}
MAX_SLUG = 24


def clean_text(value: Any, limit: int) -> str:
    """Printable single-spaced text of at most `limit` characters."""
    if not isinstance(value, str):
        return ""
    text = "".join(char if char.isprintable() or char == "\n" else " " for char in value)
    text = "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()
    return text[:limit]


def make_slug(name: str, taken: set[str]) -> str:
    """A short lowercase identifier for a plugin, unique among `taken`."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    base = re.sub(r"^[^a-z]+", "", base)[:MAX_SLUG].strip("-") or "plugin"
    candidate, number = base, 1
    while candidate in taken:
        number += 1
        suffix = f"-{number}"
        candidate = base[:MAX_SLUG - len(suffix)].rstrip("-") + suffix
    return candidate


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0


def _schema(node: Any, depth: int, budget: _Budget) -> Any:
    budget.nodes += 1
    if budget.nodes > MAX_NODES or depth > MAX_DEPTH:
        return {}
    if node is True or node is False:
        return node
    if not isinstance(node, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in KEEP:
            continue
        if key == "type":
            kinds = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            kinds = [kind for kind in kinds if isinstance(kind, str) and kind in JSON_TYPES]
            if kinds:
                out[key] = kinds[0] if len(kinds) == 1 else kinds
        elif key == "properties":
            if isinstance(value, dict):
                out[key] = {str(name)[:128]: _schema(item, depth + 1, budget) for name, item in list(value.items())[:100]}
        elif key in ("$defs", "definitions"):
            if isinstance(value, dict):
                out[key] = {str(name)[:128]: _schema(item, depth + 1, budget) for name, item in list(value.items())[:50]}
        elif key == "required":
            if isinstance(value, list):
                out[key] = [item for item in value if isinstance(item, str)][:100]
        elif key in ("items", "additionalProperties"):
            out[key] = value if isinstance(value, bool) else _schema(value, depth + 1, budget)
        elif key in ("anyOf", "oneOf", "allOf"):
            if isinstance(value, list):
                out[key] = [_schema(item, depth + 1, budget) for item in value[:20]]
        elif key == "enum":
            if isinstance(value, list):
                out[key] = [item for item in value if isinstance(item, (str, int, float, bool)) or item is None][:100]
        elif key == "$ref":
            if isinstance(value, str) and value.startswith("#/") and len(value) <= 200:
                out[key] = value
        elif key == "description":
            text = clean_text(value, 500)
            if text:
                out[key] = text
        elif key in ("const", "default"):
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
        elif key in NUMERIC:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = value
        elif key == "uniqueItems":
            if isinstance(value, bool):
                out[key] = value
    return out


def _strip_descriptions(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _strip_descriptions(value) for key, value in node.items() if not (key == "description" and isinstance(value, str))}
    if isinstance(node, list):
        return [_strip_descriptions(item) for item in node]
    return node


def clean_input_schema(raw: Any) -> dict[str, Any]:
    """A JSON Schema object for a tool's arguments that any provider accepts, or an empty object when the server gave none."""
    if not isinstance(raw, dict):
        return dict(EMPTY_OBJECT)
    cleaned = _schema(raw, 0, _Budget())
    if not isinstance(cleaned, dict):
        return dict(EMPTY_OBJECT)
    cleaned["type"] = "object"
    if not isinstance(cleaned.get("properties"), dict):
        cleaned["properties"] = {}
    if isinstance(cleaned.get("required"), list):
        cleaned["required"] = [name for name in cleaned["required"] if name in cleaned["properties"]]
        if not cleaned["required"]:
            del cleaned["required"]
    if len(json.dumps(cleaned, ensure_ascii=False).encode()) > MAX_SCHEMA_BYTES:
        cleaned = _strip_descriptions(cleaned)
    if len(json.dumps(cleaned, ensure_ascii=False).encode()) > MAX_SCHEMA_BYTES:
        # Too large to offer faithfully: the names of the arguments, as free-form values the server will check itself.
        cleaned = {"type": "object", "properties": {name: {} for name in list(cleaned["properties"])[:60]}}
    return cleaned


def tool_spec(raw: Any) -> dict[str, Any] | None:
    """One tool as the server listed it, in the form stored and offered; None when it is unusable."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"] or len(raw["name"]) > 256:
        return None
    annotations = raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {}
    title = clean_text(raw.get("title") or annotations.get("title"), 120)
    return {"name": raw["name"], "title": title, "description": clean_text(raw.get("description"), MAX_DESCRIPTION),
            "input_schema": clean_input_schema(raw.get("inputSchema", raw.get("input_schema"))),
            # A hint from the server; a tool that does not say it only reads is treated as one that changes something.
            "read_only": annotations.get("readOnlyHint", raw.get("read_only")) is True}


def tool_specs(raw_tools: Any) -> list[dict[str, Any]]:
    """The usable, distinct tools of a server's list (at most MAX_TOOLS), in a stable order."""
    seen: set[str] = set()
    specs = []
    for raw in raw_tools if isinstance(raw_tools, list) else []:
        spec = tool_spec(raw)
        if spec is not None and spec["name"] not in seen:
            seen.add(spec["name"])
            specs.append(spec)
    return sorted(specs, key=lambda spec: spec["name"])[:MAX_TOOLS]


def function_name(slug: str, remote_name: str, taken: set[str]) -> str:
    """`slug__tool`, at most 64 characters of [a-z0-9_-], unique among `taken` (which it is added to)."""
    clean = re.sub(r"[^a-z0-9_-]+", "_", remote_name.lower()).strip("_-") or "tool"
    room = MAX_NAME - len(slug) - 2
    if len(clean) > room:
        digest = hashlib.sha256(remote_name.encode()).hexdigest()[:6]
        clean = clean[:room - 7].rstrip("_-") + "_" + digest
    name, number = f"{slug}__{clean}", 1
    while name in taken:
        number += 1
        suffix = f"_{number}"
        name = f"{slug}__{clean[:room - len(suffix)]}{suffix}"
    taken.add(name)
    return name
