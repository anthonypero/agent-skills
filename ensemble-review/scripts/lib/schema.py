"""A JSON Schema subset validator, hand-written for the same reason `report.py`'s is.

The skill is standard-library only, so there is no `jsonschema` to import. This covers the keywords
the two schemas in `schemas/` actually use — `type`, `enum`, `const`, `required`, `properties`,
`additionalProperties`, `items`, `minItems`, `minLength` — and reports every failure with the JSON
pointer that produced it rather than stopping at the first. Anything else in a schema is ignored, so
adding a keyword this module does not know about silently weakens the check: keep the schemas inside
the subset.
"""

import json

TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def validate(instance, schema, pointer="$"):
    """Return a list of human-readable errors. Empty means valid."""
    errors = []

    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(TYPE_CHECKS.get(option, lambda _v: True)(instance) for option in options):
            errors.append("{0}: expected {1}, got {2}".format(pointer, "/".join(options), _name(instance)))
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append("{0}: must be {1!r}".format(pointer, schema["const"]))
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("{0}: {1!r} is not one of {2}".format(pointer, instance, schema["enum"]))
    if "minLength" in schema and isinstance(instance, str) and len(instance) < schema["minLength"]:
        errors.append("{0}: must be at least {1} character(s)".format(pointer, schema["minLength"]))

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append("{0}: missing required property `{1}`".format(pointer, name))
        properties = schema.get("properties") or {}
        for name, value in instance.items():
            if name in properties:
                errors.extend(validate(value, properties[name], "{0}.{1}".format(pointer, name)))
            elif schema.get("additionalProperties") is False and not name.startswith("_"):
                errors.append("{0}: unexpected property `{1}`".format(pointer, name))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("{0}: must hold at least {1} item(s)".format(pointer, schema["minItems"]))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(validate(item, item_schema, "{0}[{1}]".format(pointer, index)))

    return errors


def _name(value):
    if value is None:
        return "null"
    return {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer", float: "number"}.get(type(value), type(value).__name__)
