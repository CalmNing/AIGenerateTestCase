"""Schema parsing, example generation, and JSON merge helpers.

Extracted verbatim from api_test_tool.py (Phase 2 split). Leaf-dependency
module: consumed by swagger_parser, step_runner, and scenario_runner.
"""
import json
from typing import Any


MAX_SCHEMA_EXAMPLE_DEPTH = 8


def _resolve_ref(spec: dict, value: Any) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return value
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return value
    cur: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(cur, dict) or part not in cur:
            return value
        cur = cur[part]
    return cur

def _ref_key(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("$ref"), str):
        return value["$ref"]
    return None

def _schema_type(schema: dict) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for item in schema_type:
            if item != "null":
                return str(item)
        return "null" if schema_type else None
    if schema_type:
        return str(schema_type)
    if "properties" in schema or "additionalProperties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        value = schema["enum"][0]
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int) and not isinstance(value, bool):
            return "integer"
        if isinstance(value, float):
            return "number"
    for composite_key in ("allOf", "oneOf", "anyOf"):
        choices = schema.get(composite_key)
        if isinstance(choices, list) and choices:
            for item in choices:
                if isinstance(item, dict):
                    item_type = _schema_type(item)
                    if item_type:
                        return item_type
    return None

def _coerce_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _coerce_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _first_schema_value(schema: dict) -> Any:
    if "example" in schema:
        return schema["example"]
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        for item in enum:
            if item is not None:
                return item
        return enum[0]
    return None

def _minimum_number(schema: dict) -> int | float | None:
    exclusive = schema.get("exclusiveMinimum")
    if isinstance(exclusive, bool):
        minimum = _coerce_float(schema.get("minimum"))
        if minimum is None:
            return None
        return minimum + 1
    if exclusive is not None:
        value = _coerce_float(exclusive)
        return value + 1 if value is not None else None
    return _coerce_float(schema.get("minimum"))

def _maximum_number(schema: dict) -> int | float | None:
    exclusive = schema.get("exclusiveMaximum")
    if isinstance(exclusive, bool):
        maximum = _coerce_float(schema.get("maximum"))
        if maximum is None:
            return None
        return maximum - 1
    if exclusive is not None:
        value = _coerce_float(exclusive)
        return value - 1 if value is not None else None
    return _coerce_float(schema.get("maximum"))

def _valid_number_for_schema(schema: dict, *, integer: bool) -> int | float:
    minimum = _minimum_number(schema)
    maximum = _maximum_number(schema)
    value: int | float = 1
    if minimum is not None:
        value = minimum
    elif maximum is not None:
        value = maximum
    if integer:
        return int(value)
    return float(value)

def _valid_string_for_schema(schema: dict) -> str:
    explicit = _first_schema_value(schema)
    if explicit is not None:
        return str(explicit)

    fmt = schema.get("format")
    if fmt == "email":
        value = "user@example.com"
    elif fmt == "uuid":
        value = "00000000-0000-4000-8000-000000000000"
    elif fmt == "date":
        value = "2026-01-01"
    elif fmt == "date-time":
        value = "2026-01-01T00:00:00Z"
    elif fmt in {"uri", "url"}:
        value = "https://example.com"
    else:
        value = "string"

    min_length = _coerce_int(schema.get("minLength"), 0) or 0
    max_length = _coerce_int(schema.get("maxLength"))
    if len(value) < min_length:
        value = "x" * min_length
    if max_length is not None and len(value) > max_length:
        value = "x" * max_length
    return value

def _valid_array_for_schema(spec: dict, schema: dict, *, depth: int, seen_refs: set[str]) -> list[Any]:
    min_items = max(_coerce_int(schema.get("minItems"), 1) or 1, 0)
    max_items = _coerce_int(schema.get("maxItems"))
    count = min_items
    if max_items is not None:
        count = min(count, max_items)
    count = min(count, 3)
    item = _schema_example(spec, schema.get("items", {}), depth=depth + 1, seen_refs=seen_refs)
    return [item for _ in range(count)]

def _schema_example(spec: dict, schema: Any, *, depth: int = 0, seen_refs: set[str] | None = None) -> Any:
    if depth > MAX_SCHEMA_EXAMPLE_DEPTH:
        return None

    seen_refs = seen_refs or set()
    ref = _ref_key(schema)
    if ref:
        if ref in seen_refs:
            return None
        seen_refs = {*seen_refs, ref}

    schema = _resolve_ref(spec, schema)
    if not isinstance(schema, dict):
        return None
    explicit = _first_schema_value(schema)
    if explicit is not None:
        return explicit

    for composite_key in ("allOf", "oneOf", "anyOf"):
        choices = schema.get(composite_key)
        if isinstance(choices, list) and choices:
            if composite_key == "allOf":
                merged = {}
                for item in choices:
                    value = _schema_example(spec, item, depth=depth + 1, seen_refs=seen_refs)
                    if isinstance(value, dict):
                        merged.update(value)
                return merged or None
            return _schema_example(spec, choices[0], depth=depth + 1, seen_refs=seen_refs)

    schema_type = _schema_type(schema)
    if schema_type == "object" or "properties" in schema:
        result = {}
        for key, prop in (schema.get("properties") or {}).items():
            result[key] = _schema_example(spec, prop, depth=depth + 1, seen_refs=seen_refs)
        if not result and isinstance(schema.get("additionalProperties"), dict):
            result["key"] = _schema_example(
                spec,
                schema["additionalProperties"],
                depth=depth + 1,
                seen_refs=seen_refs,
            )
        return result
    if schema_type == "array":
        return _valid_array_for_schema(spec, schema, depth=depth, seen_refs=seen_refs)
    if schema_type == "integer":
        return _valid_number_for_schema(schema, integer=True)
    if schema_type == "number":
        return _valid_number_for_schema(schema, integer=False)
    if schema_type == "boolean":
        return True
    if schema_type == "null":
        return None
    return _valid_string_for_schema(schema)

def _resolve_schema_deep(spec: dict, schema: Any, *, depth: int = 0, seen_refs: set[str] | None = None) -> Any:
    if depth > MAX_SCHEMA_EXAMPLE_DEPTH:
        return schema

    seen_refs = seen_refs or set()
    ref = _ref_key(schema)
    if ref:
        if ref in seen_refs:
            return {"$ref": ref}
        seen_refs = {*seen_refs, ref}

    schema = _resolve_ref(spec, schema)
    if isinstance(schema, dict):
        resolved = {}
        for key, value in schema.items():
            if key == "$ref":
                continue
            resolved[key] = _resolve_schema_deep(spec, value, depth=depth + 1, seen_refs=seen_refs)
        return resolved
    if isinstance(schema, list):
        return [_resolve_schema_deep(spec, item, depth=depth + 1, seen_refs=seen_refs) for item in schema]
    return schema

def _json_fingerprint(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False, sort_keys=True)

def _json_equal(left: Any, right: Any) -> bool:
    return _json_fingerprint(left) == _json_fingerprint(right)

def _load_json_text(value: str | None) -> Any:
    if value is None or not str(value).strip():
        return None
    return json.loads(str(value))

def _schema_required(schema: dict | None) -> list[str]:
    if not isinstance(schema, dict):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in schema.get("allOf") or []:
        for field in _schema_required(item if isinstance(item, dict) else None):
            if field not in seen:
                result.append(field)
                seen.add(field)
    required = schema.get("required")
    if isinstance(required, list):
        for item in required:
            field = str(item)
            if field not in seen:
                result.append(field)
                seen.add(field)
    return result

def _schema_properties(schema: dict | None) -> dict:
    if not isinstance(schema, dict):
        return {}
    result: dict[str, Any] = {}
    for item in schema.get("allOf") or []:
        result.update(_schema_properties(item if isinstance(item, dict) else None))
    props = schema.get("properties")
    if isinstance(props, dict):
        result.update(props)
    if not result:
        for composite_key in ("oneOf", "anyOf"):
            choices = schema.get(composite_key)
            if isinstance(choices, list) and choices:
                for item in choices:
                    if isinstance(item, dict):
                        result.update(_schema_properties(item))
                        if result:
                            return result
    return result
